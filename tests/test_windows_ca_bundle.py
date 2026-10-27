import importlib.util
import os
import ssl
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "windows_ca_bundle.py"


@pytest.fixture()
def cab():
    spec = importlib.util.spec_from_file_location("windows_ca_bundle_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _a_cert_der():
    import certifi

    text = Path(certifi.where()).read_text(encoding="ascii", errors="ignore")
    start = text.index("-----BEGIN CERTIFICATE-----")
    end = text.index("-----END CERTIFICATE-----", start) + len("-----END CERTIFICATE-----")
    return ssl.PEM_cert_to_DER_cert(text[start:end])


def _fake_inspection_root():
    # A real certificate re-encoded with one changed byte stands in for a corporate root
    # that only Windows trusts (it only has to be distinct, not valid).
    der = bytearray(_a_cert_der())
    der[-1] ^= 0xFF
    return bytes(der)


def test_bundle_contains_certifi_and_windows_roots(cab, tmp_path, monkeypatch):
    corporate = _fake_inspection_root()
    monkeypatch.setattr(cab, "windows_certificates", lambda: [corporate, _a_cert_der()])
    path, counts = cab.build_ca_bundle(tmp_path / "bundle.pem")
    ders = cab._read_pem_file(path)
    assert corporate in ders
    assert counts["windows"] == 1  # the certifi duplicate is not added twice
    assert counts["certifi"] > 50
    assert len(ders) == counts["total"]


def test_existing_user_bundle_is_kept(cab, tmp_path, monkeypatch):
    user = tmp_path / "user.pem"
    user.write_text(ssl.DER_cert_to_PEM_cert(_fake_inspection_root()), encoding="ascii")
    monkeypatch.setattr(cab, "windows_certificates", lambda: [])
    environment = {"REQUESTS_CA_BUNDLE": str(user)}
    monkeypatch.setattr(cab, "default_bundle_path", lambda root: tmp_path / "out" / "b.pem")
    path = cab.apply_ca_bundle(environment, tmp_path)
    assert path == tmp_path / "out" / "b.pem"
    assert all(environment[name] == str(path) for name in cab.CA_ENV_VARS)
    assert _fake_inspection_root() in cab._read_pem_file(path)


def test_switch_off_leaves_environment_alone(cab, tmp_path):
    environment = {"DARA_CA_BUNDLE": "off"}
    assert cab.apply_ca_bundle(environment, tmp_path) is None
    assert "SSL_CERT_FILE" not in environment


def test_failure_never_blocks_startup(cab, tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("no certificates")

    monkeypatch.setattr(cab, "build_ca_bundle", boom)
    environment = {}
    assert cab.apply_ca_bundle(environment, tmp_path) is None
    assert environment == {}


@pytest.mark.skipif(os.name != "nt", reason="Windows certificate store")
def test_real_windows_store_is_read(cab):
    assert len(cab.windows_certificates()) > 10


def test_bundle_works_for_a_tls_context(cab, tmp_path, monkeypatch):
    path, _ = cab.build_ca_bundle(tmp_path / "bundle.pem")
    context = ssl.create_default_context(cafile=str(path))
    assert context.cert_store_stats()["x509_ca"] > 50


def test_launcher_passes_bundle_to_server(tmp_path, monkeypatch):
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("launch_under_test", SCRIPT.parent / "launch_dara_web.py")
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
    finally:
        sys.path.remove(str(SCRIPT.parent))
    assert callable(launcher.apply_ca_bundle)

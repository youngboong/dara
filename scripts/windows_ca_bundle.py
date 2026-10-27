"""Build a CA bundle that includes the certificates Windows trusts.

Networks that inspect HTTPS re-sign traffic with a root certificate that only the
Windows certificate store trusts. Python (requests/certifi, OpenSSL) does not read
that store, so COD downloads and Materials Project calls fail with
"certificate verify failed". START writes certifi's bundle plus the Windows ROOT/CA
stores into one PEM file and points SSL_CERT_FILE / REQUESTS_CA_BUNDLE /
CURL_CA_BUNDLE at it for the processes it starts.

Standard library only (certifi is used when available). Set DARA_CA_BUNDLE=off to
disable.
"""

from __future__ import annotations

import argparse
import os
import ssl
import sys
from pathlib import Path

CA_ENV_VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
SERVER_AUTH_OID = "1.3.6.1.5.5.7.3.1"
WINDOWS_STORES = ("ROOT", "CA")
PEM_BEGIN = "-----BEGIN CERTIFICATE-----"
PEM_END = "-----END CERTIFICATE-----"


def default_bundle_path(repo_root: Path) -> Path:
    return repo_root / "tools" / "certs" / "dara-ca-bundle.pem"


def _pem_blocks(text: str) -> list[str]:
    blocks, start = [], text.find(PEM_BEGIN)
    while start != -1:
        end = text.find(PEM_END, start)
        if end == -1:
            break
        blocks.append(text[start:end + len(PEM_END)])
        start = text.find(PEM_BEGIN, end)
    return blocks


def _pem_to_der(block: str) -> bytes | None:
    try:
        return ssl.PEM_cert_to_DER_cert(block)
    except ValueError:
        return None


def _read_pem_file(path: str | os.PathLike) -> list[bytes]:
    try:
        text = Path(path).read_text(encoding="ascii", errors="ignore")
    except OSError:
        return []
    return [der for der in (_pem_to_der(block) for block in _pem_blocks(text)) if der]


def _certifi_bundle() -> str | None:
    try:
        import certifi

        return certifi.where()
    except ImportError:
        return ssl.get_default_verify_paths().cafile


def windows_certificates() -> list[bytes]:
    """DER certificates from the Windows ROOT and CA stores trusted for server auth."""
    if os.name != "nt" or not hasattr(ssl, "enum_certificates"):
        return []
    found = []
    for store in WINDOWS_STORES:
        try:
            entries = ssl.enum_certificates(store)
        except OSError:
            continue
        for der, encoding, trust in entries:
            if encoding != "x509_asn":
                continue
            if trust is True or (isinstance(trust, (set, frozenset)) and SERVER_AUTH_OID in trust):
                found.append(der)
    return found


def build_ca_bundle(out_path: Path, extra_files=()) -> tuple[Path, dict]:
    """Write certifi + extra PEM files + Windows certificates (deduplicated) to ``out_path``."""
    sources = {"certifi": 0, "extra": 0, "windows": 0}
    seen, ordered = set(), []

    def add(ders, key):
        for der in ders:
            if der not in seen:
                seen.add(der)
                ordered.append(der)
                sources[key] += 1

    base = _certifi_bundle()
    if base:
        add(_read_pem_file(base), "certifi")
    for extra in extra_files:
        if extra and Path(extra).is_file() and Path(extra).resolve() != out_path.resolve():
            add(_read_pem_file(extra), "extra")
    add(windows_certificates(), "windows")
    if not ordered:
        raise RuntimeError("No CA certificates were found")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text("".join(ssl.DER_cert_to_PEM_cert(der) for der in ordered), encoding="ascii")
    os.replace(tmp, out_path)
    return out_path, {**sources, "total": len(ordered)}


def apply_ca_bundle(environment: dict, repo_root: Path) -> Path | None:
    """Build the bundle and point the CA environment variables of ``environment`` at it.

    Certificates from a bundle the user already configured are kept. Returns ``None``
    (environment unchanged) when disabled or when the bundle cannot be built.
    """
    if environment.get("DARA_CA_BUNDLE", "").strip().lower() in {"0", "off", "false", "no"}:
        return None
    out_path = default_bundle_path(repo_root)
    try:
        path, _ = build_ca_bundle(out_path, [environment.get(name) for name in CA_ENV_VARS])
    except Exception as error:  # never block startup over certificates
        print(f"Could not build the Windows CA bundle ({error}); using Python's default certificates.", flush=True)
        return None
    for name in CA_ENV_VARS:
        environment[name] = str(path)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    environment = dict(os.environ)
    path = apply_ca_bundle(environment, args.repo_root)
    if path is None:
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

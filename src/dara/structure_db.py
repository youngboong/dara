"""Interact with the (local) ICSD database."""

from __future__ import annotations

import itertools
import os
import re
import tempfile
import threading
import time
from abc import ABCMeta, abstractmethod
from pathlib import Path

import requests
from monty.json import MSONable
from monty.serialization import loadfn
from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.core import Composition
from tqdm.contrib.concurrent import thread_map

from dara.cif import Cif
from dara.data import COMMON_GASES
from dara.settings import DaraSettings
from dara.utils import copy_and_rename_files, get_logger

logger = get_logger(__name__)

DARA_SETTINGS = DaraSettings()

PATH_TO_COD = DARA_SETTINGS.PATH_TO_COD
PATH_TO_ICSD = DARA_SETTINGS.PATH_TO_ICSD

COD_CACHE_ENV_VAR = "DARA_COD_CACHE_DIR"
_DEFAULT_COD_CACHE_NAME = "dara_downloaded_cifs"


class CODDownloadError(RuntimeError):
    """None of the requested COD reference structures could be obtained."""


def cod_cache_dir() -> Path:
    """Absolute folder where COD CIFs downloaded from the web are cached as ``<id>.cif``.

    Resolution order:

    1. ``$DARA_COD_CACHE_DIR`` when set.
    2. ``<project root>/dara_downloaded_cifs`` for a source checkout (the folder that
       holds ``pyproject.toml``). This is where earlier DARA versions wrote their
       downloads when started from the project folder, so existing downloads are reused,
       and it no longer depends on the current working directory.
    3. ``~/.dara/dara_downloaded_cifs`` for an installed package.
    """
    configured = os.environ.get(COD_CACHE_ENV_VAR, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    project_root = Path(__file__).resolve().parents[2]
    if (project_root / "pyproject.toml").is_file():
        return project_root / _DEFAULT_COD_CACHE_NAME
    return Path.home() / ".dara" / _DEFAULT_COD_CACHE_NAME


def is_valid_cif_file(path: Path | str) -> bool:
    """True if ``path`` is a non-empty text file with a CIF ``data_`` header and a cell."""
    try:
        path = Path(path)
        if not path.is_file() or path.stat().st_size == 0:
            return False
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read(262144)
    except OSError:
        return False
    return bool(re.search(r"^data_\S*", text, re.M)) and "_cell_length_a" in text


def atomic_write_text(path: Path | str, text: str) -> None:
    """Write ``text`` to ``path`` via a temporary file and an atomic rename.

    A crash or error mid-write never leaves a truncated file at ``path``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _is_not_found_error(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) in (404, 410)



class StructureDatabase(MSONable, metaclass=ABCMeta):
    """Base class to interact with CIF files from an experimental database of
    structures. This class is subclassed by ICSDDatabase, CODDatabase, etc.
    """

    def __init__(self, path_to_cifs: Path | str | None = None):
        """
        Initialize an object for interacting with CIFs from a structure database.

        Args:
            path_to_cifs: Path to a folder containing the CIFs for the database. If this
                is not provided, the default path from DaraSettings will be used.
        """
        self._path_to_cifs = path_to_cifs
        self._preparsed_info: dict = {}
        # IDs of reference structures that could not be downloaded (cumulative per object).
        self.last_unavailable: list[str] = []

    def get_cifs_by_formulas(
        self,
        formulas: list[str],
        e_hull_filter: float = 0.1,
        copy_files=True,
        dest_dir: str = "dara_cifs",
        exclude_gases: bool = True,
    ):
        """Get a list of database CIF codes corresponding to formulas, and optionally copy CIF
        files into a destination folder.

        Args:
            formulas: List of formulas to search for.
            e_hull_filter: Filter out structures with e_hull above this value (compared
                via spacegroup).
            copy_files: Whether to copy CIF files to a destination folder.
            dest_dir: Destination folder for copied CIF files.
            exclude_gases: Whether to exclude common gases (e.g., O2) from the results.
        """
        all_data = []
        for formula in formulas:
            all_data.extend(self.get_formula_data(formula))

        if copy_files:
            file_map = self._generate_file_map(all_data, e_hull_filter, exclude_gases)
            copy_and_rename_files(file_map, dest_dir)
        elif not self.local_copy_found:
            # Nothing to copy, so nothing is downloaded; still fail early for databases
            # that can neither be read locally nor downloaded (e.g. the ICSD).
            self._ensure_cached([], self.download_cache_dir)

        return [data[1] for data in all_data]

    def get_cifs_by_chemsys(
        self,
        chemsys: str | list[str] | set[str],
        e_hull_filter: float = 0.1,
        copy_files=True,
        dest_dir: str = "dara_cifs",
        exclude_gases: bool = True,
    ):
        """Get a list of database CIF codes corresponding to structures in a chemical system.
        Option to copy CIF files into a destination folder.

        Args:
            chemsys: Chemical system to search for (e.g., "Fe-O").
            e_hull_filter: Filter out structures with e_hull above this value (compared
                via spacegroup).
            copy_files: Whether to copy CIF files to a destination folder.
            dest_dir: Destination folder for copied CIF files.
            exclude_gases: Whether to exclude common gases (e.g., O2) from the results.
        """
        if isinstance(chemsys, str):
            chemsys = chemsys.split("-")

        elements_set = set(chemsys)  # remove duplicate elements
        all_data = []

        for i in range(len(elements_set)):
            for els in itertools.combinations(elements_set, i + 1):
                sub_chemsys = "-".join(sorted(els))
                if sub_chemsys in self.preparsed_info:
                    all_data.extend(self.preparsed_info[sub_chemsys])

        if copy_files:
            file_map = self._generate_file_map(all_data, e_hull_filter, exclude_gases)
            copy_and_rename_files(file_map, dest_dir)
        elif not self.local_copy_found:
            # Nothing to copy, so nothing is downloaded; still fail early for databases
            # that can neither be read locally nor downloaded (e.g. the ICSD).
            self._ensure_cached([], self.download_cache_dir)

        return [data[1] for data in all_data]

    def get_formula_data(self, formula: str):
        """Get a list of database codes + info corresponding to a formula.

        Args:
            formula: Chemical formula to search for.
        """
        formula_reduced = Composition(formula).reduced_formula
        chemsys = Composition(formula).chemical_system
        db_chemsys = self.preparsed_info.get(chemsys)

        if not db_chemsys:
            logger.warning(f"No database codes found in chemical system: {db_chemsys}!")
            return []

        formula_data = [i for i in db_chemsys if i[0] == formula_reduced]

        if not formula_data:
            logger.warning(f"No database codes found for {formula}!")
            return []

        return formula_data

    def _generate_file_map(
        self,
        all_data,
        e_hull_filter,
        exclude_gases,
        download_folder=None,
    ):
        """A mapping of database file paths to new file names with structure metadata included.

        Only structures that pass the gas and energy-above-hull filters are fetched, and
        downloaded files that do not exist (failed downloads) are left out of the mapping.
        """
        selected = []
        for formula, code, sg, e_hull in all_data:
            if exclude_gases and formula in COMMON_GASES:
                logger.info("Skipping common gas: %s", formula)
                continue

            if e_hull is not None and e_hull > e_hull_filter:
                print(
                    f"Skipping high-energy phase: {code} ({formula}, {sg}): e_hull = {e_hull}"
                )
                continue
            selected.append((formula, code, sg, e_hull))

        folder = None
        if not self.local_copy_found:
            folder = Path(download_folder) if download_folder else self.download_cache_dir
            logger.warning(
                "Local copy of database not found. Using downloaded structures in %s...",
                folder,
            )
            self._ensure_cached(list(dict.fromkeys(str(row[1]) for row in selected)), folder)

        file_map = {}
        for formula, code, sg, e_hull in selected:
            e_hull_value = round(1000 * e_hull) if e_hull is not None else None
            fp = self.get_file_path(code) if folder is None else folder / f"{code}.cif"
            if folder is not None and not Path(fp).is_file():
                continue  # download failed; skip instead of mapping a missing file
            file_map[str(fp)] = f"{formula}_{sg}_({self.name}_{code})-{e_hull_value}.cif"

        return file_map

    @property
    def download_cache_dir(self) -> Path:
        """Folder for structures downloaded because no local copy of the database exists."""
        return Path(_DEFAULT_COD_CACHE_NAME)

    def _ensure_cached(self, ids: list[str], folder: Path) -> None:
        """Make sure the CIFs for ``ids`` are present in ``folder`` (download the missing ones)."""
        self.download_structures(ids, save=True, default_folder=folder)

    def _record_unavailable(self, ids) -> None:
        """Remember (without duplicates) structure IDs that could not be obtained."""
        for cif_id in ids:
            if str(cif_id) not in self.last_unavailable:
                self.last_unavailable.append(str(cif_id))

    @property
    def path(self) -> Path:
        """Path to local copy of the structure database (i.e., the folder containing all
        the relevant CIFs). Automatically uses the default path if not
        provided.
        """
        p = self._path_to_cifs
        if p is None:
            p = self.default_folder_path

        return Path(p)

    @property
    def local_copy_found(self) -> bool:
        """Check if a local copy of the database is found."""
        return self.path.exists()

    @abstractmethod
    def download_structures(
        self, ids: list[str] | None = None, save=False, default_folder=None
    ) -> list[Cif]:
        """Download structures from the database."""

    @abstractmethod
    def get_file_path(self, cif_id: str | int):
        """Get the path to a CIF file in the database from its database ID."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the database."""

    @property
    @abstractmethod
    def default_folder_path(self) -> Path:
        """Default path to the folder containing the CIFs for the database."""

    @property
    def preparsed_info(self) -> dict:
        """Preparsed information about the database. This is used to quickly filter and
        find structures based on chemical system, formula, etc. This has already been
        generated via the code in the scripts folder.
        """
        return self._preparsed_info


class CODDatabase(StructureDatabase):
    """Class to interact with CIF files acquired from the COD database. This class uses
    a parsed/filtered list of COD numbers that are automatically selected from when doing
    phase searches (data/cod_filtered_info_2024.json.gz).

    This class features a method to download CIFs from the online COD database, as well
    as a method to get CIFs from a local copy of the database. To download the COD
    database in a folder called COD_2024, run the following command (this may take a
    while):

        rsync -av --delete rsync://www.crystallography.net/cif/ COD_2024/
    """

    def __init__(self, path_to_cifs: Path | str | None = None):
        """
        Initialize an object for interacting with CIFs from the ICSD.

        path_to_cifs: Path to a folder containing the CIFs for the database.
        """
        super().__init__(path_to_cifs)
        self._preparsed_info = loadfn(
            Path(__file__).parent / "data/cod_filtered_info_2024.json.gz"
        )

    #: Concurrent connections to crystallography.net; kept small to be polite.
    MAX_WORKERS = 4
    #: Pause (seconds) a worker makes before each request.
    REQUEST_DELAY_S = 0.2
    #: Stop downloading after this many consecutive failed (non-404) downloads.
    MAX_CONSECUTIVE_FAILURES = 12

    @property
    def download_cache_dir(self) -> Path:
        """Persistent, working-directory independent cache (see :func:`cod_cache_dir`)."""
        return cod_cache_dir()

    def download_structures(
        self,
        ids: list[str] | None = None,
        save=False,
        default_folder="downloaded_cod_cifs",
    ) -> list[Cif]:
        """Download structures from the COD database. Note that this downloads directly
        from the COD website, so it may be slow. Please do not abuse this feature.

        IDs that cannot be downloaded are skipped with a warning and recorded in
        ``self.last_unavailable``. A ``CODDownloadError`` is raised only if none of the
        requested structures could be downloaded.

        Args:
            ids: List of COD IDs to download.
            save: Also write the downloaded CIFs (atomically) to ``default_folder``.
            default_folder: Destination folder when ``save`` is set.

        Returns:
            The CIFs that were downloaded, in request order.
        """
        ids = list(dict.fromkeys(str(i) for i in (ids or [])))
        folder = Path(default_folder) if save else None
        if folder is not None:
            logger.info(f"Saving downloaded CIFs to {folder}")
        cifs, failed = self._fetch_many(ids, folder)
        self._record_unavailable(failed)
        if ids and not cifs:
            raise CODDownloadError(
                f"None of the {len(ids)} requested COD structures could be downloaded "
                "from www.crystallography.net. Check the internet connection or try again later."
            )
        return list(cifs.values())

    def _ensure_cached(self, ids: list[str], folder: Path) -> None:
        """Reuse valid cached ``<id>.cif`` files in ``folder``; download only the missing ones."""
        folder = Path(folder)
        ids = list(dict.fromkeys(str(i) for i in ids))
        missing = [i for i in ids if not is_valid_cif_file(folder / f"{i}.cif")]
        logger.info(
            "COD reference cache %s: %d cached, %d to download.",
            folder,
            len(ids) - len(missing),
            len(missing),
        )
        if not missing:
            return
        folder.mkdir(parents=True, exist_ok=True)
        cifs, failed = self._fetch_many(missing, folder)
        self._record_unavailable(failed)
        if failed:
            logger.warning(
                "%d of %d COD reference structures could not be downloaded and were skipped: %s",
                len(failed),
                len(ids),
                ", ".join(failed[:10]) + (", ..." if len(failed) > 10 else ""),
            )
        if ids and len(failed) == len(ids):
            raise CODDownloadError(
                f"None of the {len(ids)} requested COD structures are cached or could be "
                "downloaded from www.crystallography.net. Check the internet connection, "
                f"try again later, or seed the cache ({folder}) with scripts/seed_cod_cache.py."
            )

    def _fetch_many(
        self, ids: list[str], folder: Path | None
    ) -> tuple[dict[str, Cif], list[str]]:
        """Download ``ids`` politely; returns ``({id: Cif}, [failed ids])``.

        With ``folder`` the CIFs are also saved there as ``<id>.cif`` (atomic write).
        """
        if not ids:
            return {}, []
        lock = threading.Lock()
        state = {"streak": 0, "aborted": False}

        def fetch(cod_id: str):
            with lock:
                if state["aborted"]:
                    return cod_id, None
            if self.REQUEST_DELAY_S:
                time.sleep(self.REQUEST_DELAY_S)
            try:
                cif = self._download_cod(cod_id)
                if folder is not None:
                    atomic_write_text(folder / f"{cod_id}.cif", str(cif))
            except Exception as exc:  # noqa: BLE001 - any failure just skips this structure
                logger.warning("Skipping COD %s: %s", cod_id, exc)
                with lock:
                    if not _is_not_found_error(exc):
                        state["streak"] += 1
                        if state["streak"] >= self.MAX_CONSECUTIVE_FAILURES:
                            state["aborted"] = True
                return cod_id, None
            with lock:
                state["streak"] = 0
            return cod_id, cif

        results = thread_map(
            fetch,
            ids,
            chunksize=1,
            max_workers=max(1, int(self.MAX_WORKERS)),
            desc="Downloading CIFs from COD...",
        )
        if state["aborted"]:
            logger.error(
                "COD (www.crystallography.net) appears to be unreachable or rate limiting; "
                "remaining downloads were skipped."
            )
        cifs = {cod_id: cif for cod_id, cif in results if cif is not None}
        failed = [cod_id for cod_id, cif in results if cif is None]
        return cifs, failed

    def get_file_path(self, cif_id: str | int):
        """Get the path to a CIF file in the COD database."""
        cod_id = str(cif_id).ljust(7, "0")
        if len(cod_id) > 7:
            raise ValueError(f"Invalid COD ID provided (too long): {cod_id}")

        return self.path / cod_id[0] / cod_id[1:3] / cod_id[3:5] / f"{cod_id}.cif"

    @property
    def default_folder_path(self) -> Path:
        """Default path to the folder containing the CIFs for the COD database."""
        return PATH_TO_COD

    @property
    def name(self) -> str:
        """Name of the database."""
        return "cod"

    @staticmethod
    def _download_cod(cod_id: str, max_attempts: int = 3) -> Cif:
        """Download a COD file from its ID."""
        COD_URL = "https://www.crystallography.net/cod/{cod_id}.cif"
        url = COD_URL.format(cod_id=cod_id)
        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                # Windows locks an open NamedTemporaryFile, so close the handle
                # before pymatgen opens the same path.
                temp_path = None
                try:
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".cif"
                    ) as temp_file:
                        temp_file.write(response.content)
                        temp_path = Path(temp_file.name)
                    cif = Cif.from_file(temp_path)
                finally:
                    if temp_path is not None:
                        temp_path.unlink(missing_ok=True)
                cif.filename = str(cod_id)
                return cif
            except Exception as exc:
                if _is_not_found_error(exc):
                    logger.error("COD %s does not exist on the server: %s", cod_id, exc)
                    raise
                if attempt < max_attempts:
                    delay = 0.5 * (2 ** (attempt - 1))
                    logger.warning(
                        "COD %s download failed (attempt %d/%d); retrying in %.1fs: %s",
                        cod_id,
                        attempt,
                        max_attempts,
                        delay,
                        exc,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "COD %s unavailable after %d attempts: %s",
                        cod_id,
                        max_attempts,
                        exc,
                    )
                    raise


class MaterialsProjectDatabase(StructureDatabase):
    """Use Materials Project structures as supplemental XRD references.

    Unlike COD and ICSD, Materials Project primarily contains DFT-relaxed
    structures.  These structures are therefore intended to fill gaps in the
    experimental databases, rather than replace experimental references.

    The API key is always read from ``MP_API_KEY`` at query time.  It is never
    stored on this object, which keeps serialized jobs and result documents
    free of credentials.
    """

    def __init__(
        self,
        path_to_cifs: Path | str | None = None,
        max_structures: int = 200,
        e_hull_filter: float = 0.05,
    ):
        super().__init__(path_to_cifs)
        self.max_structures = max_structures
        self.e_hull_filter = e_hull_filter
        self._query_cache: dict[tuple, list] = {}

    @property
    def name(self) -> str:
        return "mp"

    @property
    def default_folder_path(self) -> Path:
        # MP references are downloaded per job; no persistent local mirror is
        # assumed.  This property only satisfies the common database API.
        return Path("materials_project_cifs")

    @property
    def local_copy_found(self) -> bool:
        return False

    def get_file_path(self, cif_id: str | int):
        return self.path / f"{cif_id}.cif"

    def download_structures(
        self, ids: list[str] | None = None, save=False, default_folder=None
    ) -> list[Cif]:
        """Download MP structures by material id."""
        if not ids:
            return []
        docs = self._query(material_ids=[str(i) for i in ids])
        cifs = [self._document_to_cif(doc) for doc in docs]
        if save:
            destination = Path(default_folder or self.default_folder_path)
            destination.mkdir(parents=True, exist_ok=True)
            for cif in cifs:
                cif.to_file(destination / f"{cif.filename}.cif")
        return cifs

    def get_cifs_by_formulas(
        self,
        formulas: list[str],
        e_hull_filter: float | None = None,
        copy_files=True,
        dest_dir: str = "dara_cifs",
        exclude_gases: bool = True,
    ):
        """Get stable MP structures matching one of the requested formulas."""
        canonical = sorted({Composition(f).reduced_formula for f in formulas})
        if not canonical:
            return []
        docs = self._query(
            formulas=canonical,
            e_hull_filter=e_hull_filter,
            max_structures=max(self.max_structures, 20 * len(canonical)),
        )
        return self._write_documents(docs, copy_files, dest_dir, exclude_gases)

    def get_cifs_by_chemsys(
        self,
        chemsys: str | list[str] | set[str],
        e_hull_filter: float | None = None,
        copy_files=True,
        dest_dir: str = "dara_cifs",
        exclude_gases: bool = True,
    ):
        """Get stable MP structures in every sub-system of an element set."""
        elements = sorted(set(chemsys.split("-") if isinstance(chemsys, str) else chemsys))
        systems = [
            "-".join(combo)
            for size in range(1, len(elements) + 1)
            for combo in itertools.combinations(elements, size)
        ]
        docs = self._query(
            chemsys=systems,
            e_hull_filter=e_hull_filter,
            max_structures=self.max_structures,
        )
        return self._write_documents(docs, copy_files, dest_dir, exclude_gases)

    def _query(
        self,
        *,
        formulas: list[str] | None = None,
        chemsys: list[str] | None = None,
        material_ids: list[str] | None = None,
        e_hull_filter: float | None = None,
        max_structures: int | None = None,
    ):
        api_key = os.environ.get("MP_API_KEY")
        if not api_key:
            raise RuntimeError(
                "MP_API_KEY is not set; Materials Project structures cannot be queried."
            )

        cutoff = self.e_hull_filter if e_hull_filter is None else e_hull_filter
        limit = self.max_structures if max_structures is None else max_structures
        cache_key = (
            tuple(formulas or []),
            tuple(chemsys or []),
            tuple(material_ids or []),
            cutoff,
            limit,
        )
        if cache_key in self._query_cache:
            return self._query_cache[cache_key]

        try:
            from mp_api.client import MPRester
        except ImportError as exc:
            raise ImportError("Please install the mp-api package.") from exc

        query = {
            "deprecated": False,
            "fields": [
                "material_id",
                "formula_pretty",
                "symmetry",
                "energy_above_hull",
                "structure",
                "theoretical",
            ],
        }
        if formulas:
            query["formula"] = formulas
        if chemsys:
            query["chemsys"] = chemsys
        if material_ids:
            query["material_ids"] = material_ids
        if not material_ids:
            query["energy_above_hull"] = (0, cutoff)
            # Bound broad element-system queries before downloading thousands
            # of structures. Low-energy entries are the most useful first pass.
            query["num_chunks"] = 1
            query["chunk_size"] = limit
            query["_sort_fields"] = "energy_above_hull"

        logger.info(
            "Querying Materials Project reference structures "
            "(%d chemical systems, maximum %d structures)...",
            len(chemsys or formulas or material_ids or []),
            limit,
        )
        with MPRester(api_key=api_key, mute_progress_bars=True, timeout=60) as mpr:
            docs = mpr.materials.summary.search(**query)

        logger.info(
            "Materials Project returned %d structures. Deduplicating...", len(docs)
        )
        docs = self._deduplicate_documents(docs)
        if limit is not None and len(docs) > limit:
            logger.warning(
                "Materials Project returned %s unique structures; keeping the best %s.",
                len(docs),
                limit,
            )
            docs = docs[:limit]
        logger.info("Retained %d unique Materials Project structures.", len(docs))
        self._query_cache[cache_key] = docs
        return docs

    def count_by_chemsys(
        self,
        elements: list[str] | set[str] | str,
        e_hull_filter: float | None = None,
        limit: int | None = None,
        timeout_s: float = 15,
    ) -> list[dict]:
        """Lightweight MP query for run-time estimates (no ``structure`` field).

        Uses the same filter as :meth:`_query` for a chemical-system request, but
        only downloads scalar metadata (formula, space group, volume, e-hull), so
        it is much faster than a real reference download.

        Raises:
            RuntimeError: ``MP_API_KEY`` is not set.
        """
        api_key = os.environ.get("MP_API_KEY")
        if not api_key:
            raise RuntimeError(
                "MP_API_KEY is not set; Materials Project structures cannot be queried."
            )
        names = sorted(set(elements.split("-") if isinstance(elements, str) else elements))
        systems = [
            "-".join(combo)
            for size in range(1, len(names) + 1)
            for combo in itertools.combinations(names, size)
        ]
        cutoff = self.e_hull_filter if e_hull_filter is None else e_hull_filter
        limit = self.max_structures if limit is None else limit

        from mp_api.client import MPRester

        with MPRester(api_key=api_key, mute_progress_bars=True, timeout=timeout_s) as mpr:
            docs = mpr.materials.summary.search(
                deprecated=False,
                chemsys=systems,
                energy_above_hull=(0, cutoff),
                num_chunks=1,
                chunk_size=limit,
                _sort_fields="energy_above_hull",
                fields=[
                    "material_id",
                    "formula_pretty",
                    "symmetry",
                    "energy_above_hull",
                    "volume",
                    "nsites",
                    "theoretical",
                ],
            )

        rows = []
        for doc in docs:
            formula = self._doc_value(doc, "formula_pretty")
            try:
                reduced = Composition(formula).reduced_formula
            except Exception:
                reduced = str(formula)
            if reduced in COMMON_GASES:
                continue
            symmetry = self._doc_value(doc, "symmetry")
            spacegroup = (
                symmetry.get("number")
                if isinstance(symmetry, dict)
                else getattr(symmetry, "number", None)
            )
            rows.append(
                {
                    "material_id": str(self._doc_value(doc, "material_id")),
                    "formula": formula,
                    "spacegroup": spacegroup,
                    "volume": self._doc_value(doc, "volume"),
                    "nsites": self._doc_value(doc, "nsites"),
                    "e_above_hull": self._doc_value(doc, "energy_above_hull"),
                    "theoretical": self._doc_value(doc, "theoretical"),
                }
            )
        return rows

    @staticmethod
    def _doc_value(doc, name, default=None):
        return doc.get(name, default) if isinstance(doc, dict) else getattr(doc, name, default)

    def _deduplicate_documents(self, docs):
        """Prefer experimental, low-energy representatives of matching MP structures."""
        docs = sorted(
            docs,
            key=lambda doc: (
                bool(self._doc_value(doc, "theoretical", True)),
                self._energy_above_hull(doc),
                str(self._doc_value(doc, "material_id", "")),
            ),
        )
        matcher = StructureMatcher()
        representatives = []
        structures_by_formula: dict[str, list] = {}
        for doc in docs:
            structure = self._doc_value(doc, "structure")
            if structure is None:
                continue
            formula = structure.composition.reduced_formula
            known = structures_by_formula.setdefault(formula, [])
            try:
                duplicate = any(matcher.fit(structure, other) for other in known)
            except Exception:
                duplicate = False
            if duplicate:
                continue
            known.append(structure)
            representatives.append(doc)
        return representatives

    def _energy_above_hull(self, doc) -> float:
        value = self._doc_value(doc, "energy_above_hull")
        return float("inf") if value is None else float(value)

    def _document_to_cif(self, doc) -> Cif:
        structure = self._doc_value(doc, "structure")
        material_id = str(self._doc_value(doc, "material_id"))
        formula = structure.composition.reduced_formula
        symmetry = self._doc_value(doc, "symmetry")
        spacegroup = (
            symmetry.get("number")
            if isinstance(symmetry, dict)
            else getattr(symmetry, "number", None)
        )
        if spacegroup is None:
            try:
                spacegroup = structure.get_space_group_info()[1]
            except Exception:
                spacegroup = "unknown"
        e_hull = self._doc_value(doc, "energy_above_hull")
        e_hull_mev = round(1000 * e_hull) if e_hull is not None else None
        filename = f"{formula}_{spacegroup}_(mp_{material_id})-{e_hull_mev}"
        return Cif.from_structure(structure, filename=filename)

    def _write_documents(
        self, docs, copy_files: bool, dest_dir: str, exclude_gases: bool = True
    ):
        if exclude_gases:
            docs = [
                doc
                for doc in docs
                if self._doc_value(doc, "structure").composition.reduced_formula
                not in COMMON_GASES
            ]
        cifs = [self._document_to_cif(doc) for doc in docs]
        if copy_files:
            destination = Path(dest_dir)
            destination.mkdir(parents=True, exist_ok=True)
            for cif in cifs:
                cif.to_file(destination / f"{cif.filename}.cif")
        return [str(self._doc_value(doc, "material_id")) for doc in docs]


_COD_LOCK = threading.Lock()
_COD_INSTANCE: CODDatabase | None = None


def shared_cod_database() -> CODDatabase:
    """One lazily created ``CODDatabase`` (loading its index takes several seconds)."""
    global _COD_INSTANCE
    with _COD_LOCK:
        if _COD_INSTANCE is None:
            _COD_INSTANCE = CODDatabase()
        return _COD_INSTANCE


def cod_candidate_rows(
    elements: list[str],
    precursors: list[str],
    e_hull_filter: float,
    *,
    database: StructureDatabase | None = None,
    formulas_only: bool = False,
) -> list[tuple]:
    """COD index rows ``(formula, cod_id, sg, e_hull)`` a job would copy, without copying.

    Mirrors ``get_cifs_by_chemsys`` (every sub-system of ``elements``) plus
    ``get_cifs_by_formulas`` (reduced-formula match of ``precursors``) followed by the
    gas and energy-above-hull filters of ``_generate_file_map``. Rows are unique by
    COD id. With ``formulas_only`` the element query is skipped (known-mixture mode).
    """
    db = database if database is not None else shared_cod_database()
    info = db.preparsed_info
    data: list[tuple] = []
    if not formulas_only:
        names = sorted(set(elements or []))
        for size in range(1, len(names) + 1):
            for combo in itertools.combinations(names, size):
                data.extend(info.get("-".join(combo), []))
    for formula in precursors or []:
        try:
            composition = Composition(formula)
        except Exception:
            continue
        reduced = composition.reduced_formula
        data.extend(
            row for row in info.get(composition.chemical_system, []) if row[0] == reduced
        )

    rows: list[tuple] = []
    seen: set = set()
    for formula, code, sg, e_hull in data:
        if formula in COMMON_GASES:
            continue
        if e_hull is not None and e_hull > e_hull_filter:
            continue
        if code in seen:
            continue
        seen.add(code)
        rows.append((formula, code, sg, e_hull))
    return rows


def deduplicate_cif_paths(cif_paths: list[Path]) -> tuple[list[Path], dict]:
    """Remove crystallographically equivalent references while preserving provenance.

    User-uploaded CIFs have first priority, followed by COD's experimental
    references and then MP's DFT-relaxed structures. Files are not deleted.
    """

    def source(path: Path) -> str:
        name = path.stem.lower()
        if "_(cod_" in name:
            return "cod"
        if "_(mp_" in name:
            return "mp"
        return "user"

    priority = {"user": 0, "cod": 1, "mp": 2}
    ordered = sorted(cif_paths, key=lambda path: (priority[source(path)], path.name))
    matcher = StructureMatcher()
    representatives: list[Path] = []
    structures_by_formula: dict[str, list] = {}

    for path in ordered:
        try:
            structure = Cif.from_file(path).to_structure()
            formula = structure.composition.reduced_formula
            known = structures_by_formula.setdefault(formula, [])
            if any(matcher.fit(structure, other) for other in known):
                continue
            known.append(structure)
        except Exception as exc:
            logger.warning("Could not compare CIF %s for duplicates: %s", path, exc)
        representatives.append(path)

    def count_by_source(paths: list[Path]) -> dict[str, int]:
        return {
            name: sum(source(path) == name for path in paths)
            for name in ("user", "cod", "mp")
        }

    summary = {
        "before": len(cif_paths),
        "after": len(representatives),
        "removed_duplicates": len(cif_paths) - len(representatives),
        "by_source_before": count_by_source(cif_paths),
        "by_source_after": count_by_source(representatives),
    }
    return representatives, summary


class ICSDDatabase(StructureDatabase):
    """Class to interact with CIF files acquired from the ICSD database. This class uses
    a parsed/filtered list of ICSD_IDs that are automatically selected from when doing
    phase searches (see data/icsd_filtered_info_2024_v2.json.gz). Here we assume you
    have a folder containing relevant downloaded ICSD CIFs located at the
    default path in DaraSettings. You can also configure the path in dara.yaml.

    Each CIF file should be named "icsd_<id>.cif" where <id> is the ICSD code.

    **Legal notice**: to use the ICSD database, you must purchase a paid license that allows
    you to download and keep CIFs as a local copy. We do not provide any CIFs from the
    ICSD and discourage any unpermitted use of the database that is inconsistent
    with your license. Please visit https://icsd.products.fiz-karlsruhe.de/ for more
    information. We are not liable for any misuse of the ICSD database.
    """

    def __init__(self, path_to_cifs: Path | str | None = None):
        """
        Initialize an object for interacting with CIFs from the ICSD.

        :param path_to_icsd: Path to the ICSD database
        """
        super().__init__(path_to_cifs)
        self._preparsed_info = loadfn(
            Path(__file__).parent / "data/icsd_filtered_info_2025_v3.json.gz"
        )

    def get_file_path(self, cif_id: str | int):
        """Get the path to a CIF file in the ICSD database."""
        return self.path / f"icsd_{self._clean_icsd_code(cif_id)}.cif"

    def download_structures(
        self, ids: list[str] | None = None, save=False, default_folder=None
    ) -> list[Cif]:
        """Download structures from the ICSD database."""
        raise NotImplementedError(
            "Downloading from the online ICSD database will not be implemented. Please use a local copy of CIFs"
            " acquired legally through your agreement."
        )

    @property
    def name(self) -> str:
        """Name of the database."""
        return "icsd"

    @property
    def default_folder_path(self) -> Path:
        """Default path to the folder containing the CIFs for the ICSD database."""
        return PATH_TO_ICSD

    @staticmethod
    def _clean_icsd_code(icsd_code):
        """Add leading zeros to the ICSD code."""
        code = str(int(icsd_code))
        return (6 - len(code)) * "0" + code

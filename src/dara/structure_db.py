"""Interact with the (local) ICSD database."""

from __future__ import annotations

import itertools
import os
import tempfile
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
        file_map = {}
        all_data = []
        for formula in formulas:
            all_data.extend(self.get_formula_data(formula))

        file_map = self._generate_file_map(all_data, e_hull_filter, exclude_gases)

        if copy_files:
            copy_and_rename_files(file_map, dest_dir)

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

        file_map = self._generate_file_map(all_data, e_hull_filter, exclude_gases)

        if copy_files:
            copy_and_rename_files(file_map, dest_dir)

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
        download_folder="dara_downloaded_cifs",
    ):
        """A mapping of database file paths to new file names with structure metadata included."""
        if not self.local_copy_found:
            logger.warning(
                "Local copy of database not found. Attempting to download structures..."
            )
            _ = self.download_structures(
                [data[1] for data in all_data],
                save=True,
                default_folder=download_folder,
            )

        file_map = {}
        for formula, code, sg, e_hull in all_data:
            if exclude_gases and formula in COMMON_GASES:
                logger.info("Skipping common gas: %s", formula)
                continue

            if e_hull is not None and e_hull > e_hull_filter:
                print(
                    f"Skipping high-energy phase: {code} ({formula}, {sg}): e_hull = {e_hull}"
                )
                continue

            e_hull_value = round(1000 * e_hull) if e_hull is not None else None

            fp = (
                f"{self.get_file_path(code)}"
                if self.local_copy_found
                else f"{download_folder}/{code}.cif"
            )
            file_map[fp] = f"{formula}_{sg}_({self.name}_{code})-{e_hull_value}.cif"

        return file_map

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

    def download_structures(
        self,
        ids: list[str] | None = None,
        save=False,
        default_folder="downloaded_cod_cifs",
    ) -> list[Cif]:
        """Download structures from the COD database. Note that this downloads directly
        from the COD website, so it may be slow. Please do not abuse this feature.

        Args:
            ids: List of COD IDs to download.
        """
        cifs = thread_map(
            self._download_cod,
            ids,
            chunksize=1,
            max_workers=12,
            desc="Downloading CIFs from COD...",
        )
        if save:
            logger.info(f"Saving downloaded CIFs to {default_folder}")
            default_folder = Path(default_folder)
            if not default_folder.exists():
                default_folder.mkdir()

            for cif in cifs:
                cif.to_file((default_folder / cif.filename).with_suffix(".cif"))

        return cifs

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

from __future__ import annotations

import os
import re
from pathlib import Path

from .building_schema import BuildingDatasetInfo


ROOT_DIR = Path(__file__).resolve().parents[4]
BACKEND_DIR = Path(__file__).resolve().parents[3]


def _resolve_data_dir(raw_value: str | None, default_relative: str) -> Path:
    candidates: list[Path] = []
    if raw_value:
        p = Path(raw_value)
        if p.is_absolute():
            candidates.append(p)
        else:
            candidates.append((BACKEND_DIR / p).resolve())
            candidates.append((ROOT_DIR / p).resolve())
    candidates.append((ROOT_DIR / default_relative).resolve())
    for item in candidates:
        if item.exists():
            return item
    return candidates[0]


def default_raw_dir() -> Path:
    return _resolve_data_dir(os.getenv("BUILDING_RAW_DIR"), "data/raw/buildings")


def default_processed_dir() -> Path:
    return _resolve_data_dir(os.getenv("BUILDING_PROCESSED_DIR"), "data/processed/buildings")


def _read_cpg(path: Path | None) -> str | None:
    if not path or not path.exists():
        return None
    value = path.read_text(encoding="utf-8", errors="ignore").strip()
    return value or None


def _region_code_from_name(name: str) -> str:
    # District-level files: F_FAC_BUILDING_<5-digit gu code>_<date>
    # Province-level files: F_FAC_BUILDING_<2-digit sido code>_<date>
    match = re.search(r"F_FAC_BUILDING_(\d{2,5})_\d{6}", name)
    if match:
        return match.group(1)
    match = re.search(r"_(\d{5})_", name)
    if match:
        return match.group(1)
    return "unknown"


def scan_building_datasets(raw_dir: Path | None = None) -> list[BuildingDatasetInfo]:
    raw_dir = raw_dir or default_raw_dir()
    datasets: list[BuildingDatasetInfo] = []
    if not raw_dir.exists():
        return datasets

    for shp in sorted(raw_dir.glob("**/*.shp")):
        stem = shp.stem
        folder = shp.parent
        shx = folder / f"{stem}.shx"
        dbf = folder / f"{stem}.dbf"
        prj = folder / f"{stem}.prj"
        cpg = folder / f"{stem}.cpg"
        if not (shx.exists() and dbf.exists()):
            continue
        datasets.append(
            BuildingDatasetInfo(
                folder=folder,
                stem=stem,
                region_code=_region_code_from_name(stem),
                shp=shp,
                shx=shx,
                dbf=dbf,
                prj=prj if prj.exists() else None,
                cpg=cpg if cpg.exists() else None,
                encoding=_read_cpg(cpg if cpg.exists() else None),
            )
        )
    return datasets


def building_scan_status(raw_dir: Path | None = None) -> dict:
    raw_dir = raw_dir or default_raw_dir()
    datasets = scan_building_datasets(raw_dir)
    warnings = []
    if not raw_dir.exists():
        warnings.append(f"Building raw directory does not exist: {raw_dir}")
    return {
        "raw_dir": str(raw_dir),
        "raw_folders_count": len({str(item.folder) for item in datasets}),
        "datasets_count": len(datasets),
        "warnings": warnings,
    }

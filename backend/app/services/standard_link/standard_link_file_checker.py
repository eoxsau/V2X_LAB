from __future__ import annotations

from pathlib import Path


REQUIRED_LINK_FILES = [
    "MOCT_LINK.shp",
    "MOCT_LINK.shx",
    "MOCT_LINK.dbf",
    "MOCT_LINK.prj",
    "MOCT_LINK.cpg",
]

REQUIRED_NODE_FILES = [
    "MOCT_NODE.shp",
    "MOCT_NODE.shx",
    "MOCT_NODE.dbf",
    "MOCT_NODE.prj",
    "MOCT_NODE.cpg",
]


def default_raw_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "data" / "raw" / "standard_link"


def check_standard_link_files(raw_dir: Path | None = None) -> dict:
    raw_dir = raw_dir or default_raw_dir()
    files = {name: (raw_dir / name) for name in REQUIRED_LINK_FILES + REQUIRED_NODE_FILES}
    ready = {name: path.exists() for name, path in files.items()}
    warnings = []
    if not raw_dir.exists():
        warnings.append(f"Raw standard-link directory not found: {raw_dir}")
    for optional_name in ["MULTILINK.dbf", "TURNINFO.dbf"]:
        if not (raw_dir / optional_name).exists():
            warnings.append(f"Optional file missing: {optional_name}")
    return {
        "raw_dir": str(raw_dir),
        "link_files_ready": all(ready[name] for name in REQUIRED_LINK_FILES),
        "node_files_ready": all(ready[name] for name in REQUIRED_NODE_FILES),
        "files": {name: str(path) for name, path in files.items()},
        "exists": ready,
        "warnings": warnings,
    }


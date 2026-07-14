"""
region_service.py — 행정구역 DB 조회 + 로컬 PBF에서 OSM 추출

핵심 기능:
1. get_regions(level, parent_osm_id) — 행정구역 목록 반환
2. get_region(osm_id)               — 단일 행정구역 정보 반환
3. extract_osm_for_region(osm_id)   — PBF에서 해당 구역 OSM 추출 → .osm 파일 반환
"""

import sqlite3
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "regions.db"
DEFAULT_PBF = Path.home() / "Desktop" / "south-korea-260711.osm.pbf"
NETWORKS_DIR = Path(__file__).parent.parent.parent.parent / "networks"

# OSM 도로 타입 필터 (V2X 시뮬레이션에 관련된 도로만 유지)
HIGHWAY_TYPES = (
    "motorway", "motorway_link",
    "trunk", "trunk_link",
    "primary", "primary_link",
    "secondary", "secondary_link",
    "tertiary", "tertiary_link",
    "residential", "living_street",
    "unclassified", "service",
)


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def db_available() -> bool:
    return DB_PATH.exists()


def get_sido_list() -> list[dict]:
    """도/특별시/광역시 목록 (admin_level=4)"""
    if not db_available():
        return []
    with _get_db() as conn:
        rows = conn.execute("""
            SELECT osm_id, name_ko, name_en, admin_level, min_lat, max_lat, min_lon, max_lon
            FROM regions WHERE admin_level = 4
            ORDER BY name_ko
        """).fetchall()
    return [dict(r) for r in rows]


def get_sigungu_list(parent_osm_id: Optional[int] = None) -> list[dict]:
    """시/군/구 목록 (admin_level=5,6). parent_osm_id로 필터링 가능."""
    if not db_available():
        return []
    with _get_db() as conn:
        if parent_osm_id:
            rows = conn.execute("""
                SELECT osm_id, name_ko, name_en, admin_level, parent_osm_id,
                       min_lat, max_lat, min_lon, max_lon
                FROM regions
                WHERE admin_level IN (5, 6) AND parent_osm_id = ?
                ORDER BY name_ko
            """, (parent_osm_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT osm_id, name_ko, name_en, admin_level, parent_osm_id,
                       min_lat, max_lat, min_lon, max_lon
                FROM regions WHERE admin_level IN (5, 6)
                ORDER BY name_ko
            """).fetchall()
    return [dict(r) for r in rows]


def get_dong_list(parent_osm_id: Optional[int] = None) -> list[dict]:
    """읍/면/동/리 목록 (admin_level=7,8). parent_osm_id로 필터링 가능."""
    if not db_available():
        return []
    with _get_db() as conn:
        if parent_osm_id:
            rows = conn.execute("""
                SELECT osm_id, name_ko, name_en, admin_level, parent_osm_id,
                       min_lat, max_lat, min_lon, max_lon
                FROM regions
                WHERE admin_level IN (7, 8) AND parent_osm_id = ?
                ORDER BY name_ko
            """, (parent_osm_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT osm_id, name_ko, name_en, admin_level, parent_osm_id,
                       min_lat, max_lat, min_lon, max_lon
                FROM regions WHERE admin_level IN (7, 8)
                ORDER BY name_ko
            """).fetchall()
    return [dict(r) for r in rows]


def get_region(osm_id: int) -> Optional[dict]:
    """단일 행정구역 정보"""
    if not db_available():
        return None
    with _get_db() as conn:
        row = conn.execute("""
            SELECT osm_id, name_ko, name_en, admin_level, level_label, parent_osm_id,
                   min_lat, max_lat, min_lon, max_lon, network_file, network_built_at
            FROM regions WHERE osm_id = ?
        """, (osm_id,)).fetchone()
    return dict(row) if row else None


def get_children(parent_osm_id: int) -> list[dict]:
    """특정 구역의 하위 행정구역 목록"""
    if not db_available():
        return []
    with _get_db() as conn:
        rows = conn.execute("""
            SELECT osm_id, name_ko, name_en, admin_level, level_label, parent_osm_id,
                   min_lat, max_lat, min_lon, max_lon
            FROM regions WHERE parent_osm_id = ?
            ORDER BY name_ko
        """, (parent_osm_id,)).fetchall()
    return [dict(r) for r in rows]


def get_region_by_bbox(lat: float, lon: float, level: int = 6) -> Optional[dict]:
    """좌표로 행정구역 찾기"""
    if not db_available():
        return None
    with _get_db() as conn:
        rows = conn.execute("""
            SELECT osm_id, name_ko, name_en, admin_level, min_lat, max_lat, min_lon, max_lon
            FROM regions
            WHERE admin_level = ?
              AND min_lat <= ? AND max_lat >= ?
              AND min_lon <= ? AND max_lon >= ?
            ORDER BY (max_lat - min_lat) * (max_lon - min_lon) ASC
            LIMIT 1
        """, (level, lat, lat, lon, lon)).fetchall()
    return dict(rows[0]) if rows else None


def extract_osm_from_pbf(
    osm_id: int,
    pbf_path: Path = DEFAULT_PBF,
    output_dir: Path = None,
    margin_deg: float = 0.001,
) -> Path:
    """
    osmium extract를 사용해 PBF에서 해당 구역 OSM 추출.
    반환값: 생성된 .osm 파일 경로

    osmium이 없으면 pyosmium으로 fallback.
    """
    region = get_region(osm_id)
    if not region:
        raise ValueError(f"구역 osm_id={osm_id}를 DB에서 찾을 수 없습니다.")
    if region["min_lat"] is None:
        raise ValueError(f"구역 '{region['name_ko']}'의 bbox 정보가 없습니다.")

    out_dir = output_dir or (Path(tempfile.gettempdir()) / "v2x_regions")
    out_dir.mkdir(parents=True, exist_ok=True)

    # bbox에 약간의 여백 추가
    s = region["min_lat"] - margin_deg
    w = region["min_lon"] - margin_deg
    n = region["max_lat"] + margin_deg
    e = region["max_lon"] + margin_deg

    safe_name = region["name_ko"].replace("/", "_").replace(" ", "_")
    out_file = out_dir / f"region_{osm_id}_{safe_name}.osm"

    if out_file.exists():
        return out_file

    osmium_bin = shutil.which("osmium")
    if osmium_bin:
        _extract_with_osmium(osmium_bin, pbf_path, out_file, s, w, n, e)
    else:
        _extract_with_pyosmium(pbf_path, out_file, s, w, n, e)

    return out_file


def _extract_with_osmium(osmium_bin: str, pbf_path: Path, out_file: Path,
                          s: float, w: float, n: float, e: float) -> None:
    """osmium extract --bbox를 사용한 고속 추출"""
    bbox_str = f"{w:.6f},{s:.6f},{e:.6f},{n:.6f}"
    cmd = [
        osmium_bin, "extract",
        "--bbox", bbox_str,
        "--strategy", "complete_ways",
        str(pbf_path),
        "-o", str(out_file),
        "--overwrite",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"osmium extract 실패: {result.stderr}")


def _extract_with_pyosmium(pbf_path: Path, out_file: Path,
                             s: float, w: float, n: float, e: float) -> None:
    """pyosmium으로 bbox 내 노드/웨이/릴레이션 필터링 후 OSM XML 저장"""
    import osmium
    import osmium.io

    class BboxFilter(osmium.SimpleHandler):
        def __init__(self, writer, s, w, n, e):
            super().__init__()
            self.writer = writer
            self.s, self.w, self.n, self.e = s, w, n, e
            self.valid_nodes = set()

        def node(self, nd):
            if not nd.location.valid():
                return
            lat, lon = nd.location.lat, nd.location.lon
            if self.s <= lat <= self.n and self.w <= lon <= self.e:
                self.valid_nodes.add(nd.id)
                self.writer.add_node(nd)

        def way(self, w):
            node_ids = [n.ref for n in w.nodes]
            if any(nid in self.valid_nodes for nid in node_ids):
                self.writer.add_way(w)

    writer = osmium.SimpleWriter(str(out_file))
    flt = BboxFilter(writer, s, w, n, e)
    flt.apply_file(str(pbf_path), locations=True)
    writer.close()


def get_area_km2(region: dict) -> float:
    """행정구역 면적(km²) 근사치"""
    lat_c = (region["min_lat"] + region["max_lat"]) / 2
    dlat = (region["max_lat"] - region["min_lat"]) * 111.0
    dlon = (region["max_lon"] - region["min_lon"]) * 111.0 * abs(
        __import__("math").cos(lat_c * 3.14159 / 180)
    )
    return round(dlat * dlon, 2)


def mark_network_built(osm_id: int, network_file: str) -> None:
    """region에 생성된 net_file 경로 업데이트"""
    if not db_available():
        return
    from datetime import datetime
    with _get_db() as conn:
        conn.execute("""
            UPDATE regions SET network_file = ?, network_built_at = ?
            WHERE osm_id = ?
        """, (network_file, datetime.utcnow().isoformat(), osm_id))
        conn.commit()

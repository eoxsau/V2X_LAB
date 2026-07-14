"""
build_region_index.py — 전국 OSM PBF에서 행정구역 인덱스 생성

사용법:
    python scripts/build_region_index.py [--pbf /path/to/korea.osm.pbf]

출력:
    backend/data/regions.db (SQLite)

한국 OSM admin_level 매핑:
    4 = 도 / 특별시 / 광역시 / 특별자치시 / 특별자치도  (17개)
    5 = 자치구 내 구 (서울·부산·광역시 내 일부)
    6 = 시 / 군 / 구  (229개)
    7 = 읍 / 면        (1,176개)
    8 = 동 / 리         (~3,500개)
"""

import sys
import os
import sqlite3
import math
import argparse
from pathlib import Path

try:
    import osmium
except ImportError:
    print("ERROR: pyosmium이 설치되어 있지 않습니다. 'pip install osmium'을 실행하세요.")
    sys.exit(1)

DEFAULT_PBF = Path.home() / "Desktop" / "south-korea-260711.osm.pbf"
DB_PATH = Path(__file__).parent.parent / "data" / "regions.db"

DISPLAY_LEVELS = {
    4: "도/특별시/광역시",
    5: "시/군/구",
    6: "시/군/구",
    7: "읍/면",
    8: "동/리",
}

LEVEL_LABEL = {
    4: "sido",      # 도, 특별시, 광역시
    5: "sigungu",   # 자치구 내 구
    6: "sigungu",   # 시, 군, 구
    7: "eupmyeon",  # 읍, 면
    8: "dong",      # 동, 리
}


class RelationCollector(osmium.SimpleHandler):
    """Pass 1: 행정경계 relation 메타데이터 수집"""

    def __init__(self):
        super().__init__()
        self.regions = {}       # osm_id → dict
        self.parent_map = {}    # osm_id → parent_osm_id (from is_in / admin_centre)

    def relation(self, r):
        if r.tags.get("type") != "boundary":
            return
        if r.tags.get("boundary") != "administrative":
            return

        level_str = r.tags.get("admin_level", "")
        try:
            level = int(level_str)
        except (ValueError, TypeError):
            return

        if level < 4 or level > 8:
            return

        name_ko = r.tags.get("name:ko") or r.tags.get("name", "")
        if not name_ko:
            return

        # relation에서 직접 부모 ID를 추출 (없으면 나중에 bbox로 추론)
        parent_id = None
        for m in r.members:
            if m.type == "r" and m.role in ("subarea", "outer"):
                # subarea 멤버가 있으면 이 relation이 부모
                pass
        # ISO 3166-2 코드로 부모 추론 가능
        iso = r.tags.get("ISO3166-2", "")

        self.regions[r.id] = {
            "osm_id": r.id,
            "name_ko": name_ko,
            "name_en": r.tags.get("name:en", ""),
            "admin_level": level,
            "level_label": LEVEL_LABEL.get(level, "unknown"),
            "iso": iso,
            "min_lat": None, "max_lat": None,
            "min_lon": None, "max_lon": None,
            "parent_osm_id": None,
        }


class BboxCalculator(osmium.SimpleHandler):
    """Pass 2: relation의 bbox를 way/node 위치에서 계산"""

    def __init__(self, target_osm_ids: set):
        super().__init__()
        self.target_ids = target_osm_ids
        # osm_id → [min_lat, max_lat, min_lon, max_lon]
        self.bboxes = {oid: [90.0, -90.0, 180.0, -180.0] for oid in target_osm_ids}
        self._current_relation_ids = set()

    def node(self, n):
        pass  # 노드는 way에서 처리

    def way(self, w):
        pass  # way는 relation에서 처리


class RelationBboxHandler(osmium.SimpleHandler):
    """
    pyosmium의 NodeLocationsForWays를 이용해 relation bbox를 계산.
    apply_with_location을 사용해야 한다.
    """

    def __init__(self, regions: dict):
        super().__init__()
        self.regions = regions  # osm_id → region_dict (in-place 수정)
        self._rel_to_ways = {}  # relation_id → [way_id, ...]
        self._way_nodes = {}    # way_id → [(lat, lon), ...]

    def relation(self, r):
        if r.id not in self.regions:
            return
        ways = [m.ref for m in r.members if m.type == "w"]
        self._rel_to_ways[r.id] = ways

    def way(self, w):
        if not w.nodes or not w.nodes[0].location.valid():
            return
        coords = []
        for n in w.nodes:
            if n.location.valid():
                coords.append((n.location.lat, n.location.lon))
        if coords:
            self._way_nodes[w.id] = coords

    def finalize(self):
        """모든 relation의 bbox를 계산"""
        for rel_id, way_ids in self._rel_to_ways.items():
            lats, lons = [], []
            for wid in way_ids:
                if wid in self._way_nodes:
                    for lat, lon in self._way_nodes[wid]:
                        lats.append(lat)
                        lons.append(lon)
            if lats:
                r = self.regions[rel_id]
                r["min_lat"] = min(lats)
                r["max_lat"] = max(lats)
                r["min_lon"] = min(lons)
                r["max_lon"] = max(lons)


def infer_parent_relations(regions: dict) -> None:
    """
    bbox 포함 관계로 부모-자식 추론.
    한국 행정구역은 레벨이 비연속적 (예: 서울 4→6→8, 경기 4→6→7→8).
    각 child의 center를 모든 상위 level에서 찾아 bbox가 가장 작은(=가장 구체적인) 것을 부모로 설정.
    """
    by_level: dict[int, list] = {}
    for r in regions.values():
        lv = r["admin_level"]
        by_level.setdefault(lv, []).append(r)

    levels_sorted = sorted(by_level.keys())

    for idx, child_level in enumerate(levels_sorted):
        if idx == 0:
            continue  # 최상위 레벨은 부모 없음
        children = by_level[child_level]
        # 현재 레벨보다 낮은(더 상위) 모든 레벨의 region들을 대상으로 탐색
        potential_parents = []
        for plv in levels_sorted[:idx]:
            potential_parents.extend(by_level[plv])

        for child in children:
            if child["min_lat"] is None:
                continue
            c_lat = (child["min_lat"] + child["max_lat"]) / 2
            c_lon = (child["min_lon"] + child["max_lon"]) / 2

            best_parent = None
            best_area = float("inf")
            for parent in potential_parents:
                if parent["min_lat"] is None:
                    continue
                if (parent["min_lat"] <= c_lat <= parent["max_lat"] and
                        parent["min_lon"] <= c_lon <= parent["max_lon"]):
                    area = ((parent["max_lat"] - parent["min_lat"]) *
                            (parent["max_lon"] - parent["min_lon"]))
                    if area < best_area:
                        best_area = area
                        best_parent = parent

            if best_parent:
                child["parent_osm_id"] = best_parent["osm_id"]


def save_to_db(regions: dict, db_path: Path) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS regions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            osm_id      INTEGER UNIQUE NOT NULL,
            name_ko     TEXT NOT NULL,
            name_en     TEXT,
            admin_level INTEGER NOT NULL,
            level_label TEXT NOT NULL,
            parent_osm_id INTEGER,
            min_lat     REAL,
            max_lat     REAL,
            min_lon     REAL,
            max_lon     REAL,
            network_file TEXT,
            network_built_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_regions_level ON regions(admin_level);
        CREATE INDEX IF NOT EXISTS idx_regions_parent ON regions(parent_osm_id);
    """)

    inserted = 0
    for r in regions.values():
        if r["min_lat"] is None:
            continue  # bbox 없으면 저장 안 함
        c.execute("""
            INSERT OR REPLACE INTO regions
                (osm_id, name_ko, name_en, admin_level, level_label,
                 parent_osm_id, min_lat, max_lat, min_lon, max_lon)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            r["osm_id"], r["name_ko"], r["name_en"],
            r["admin_level"], r["level_label"],
            r["parent_osm_id"],
            r["min_lat"], r["max_lat"], r["min_lon"], r["max_lon"],
        ))
        inserted += 1

    conn.commit()
    conn.close()
    return inserted


def build_index(pbf_path: Path, db_path: Path) -> None:
    print(f"[1/4] PBF 파일 읽는 중: {pbf_path} ({pbf_path.stat().st_size / 1e6:.0f} MB)")

    # Pass 1: relation 메타데이터
    collector = RelationCollector()
    collector.apply_file(str(pbf_path))
    print(f"[2/4] 행정경계 relation {len(collector.regions)}개 수집 완료")

    if not collector.regions:
        print("ERROR: 행정경계를 찾지 못했습니다. PBF 파일을 확인하세요.")
        return

    # Pass 2: way/node 위치로 bbox 계산 (NodeLocationsForWays 사용)
    print(f"[3/4] Bbox 계산 중... (시간이 걸릴 수 있습니다)")
    bbox_handler = RelationBboxHandler(collector.regions)
    bbox_handler.apply_file(str(pbf_path), locations=True, idx="flex_mem")
    bbox_handler.finalize()

    # 부모-자식 추론
    infer_parent_relations(collector.regions)

    # DB 저장
    count = save_to_db(collector.regions, db_path)
    print(f"[4/4] DB 저장 완료: {count}개 행정구역 → {db_path}")

    # 통계 출력
    by_level = {}
    for r in collector.regions.values():
        if r["min_lat"] is not None:
            lv = r["admin_level"]
            by_level[lv] = by_level.get(lv, 0) + 1

    print("\n=== 레벨별 통계 ===")
    level_names = {4: "도/특별시/광역시", 5: "자치구내 구", 6: "시/군/구", 7: "읍/면", 8: "동/리"}
    for lv in sorted(by_level):
        print(f"  admin_level {lv} ({level_names.get(lv, '?')}): {by_level[lv]}개")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="전국 OSM PBF → 행정구역 SQLite 인덱스 생성")
    parser.add_argument("--pbf", default=str(DEFAULT_PBF), help="PBF 파일 경로")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite DB 출력 경로")
    args = parser.parse_args()

    pbf = Path(args.pbf)
    db = Path(args.db)

    if not pbf.exists():
        print(f"ERROR: PBF 파일을 찾을 수 없습니다: {pbf}")
        sys.exit(1)

    build_index(pbf, db)

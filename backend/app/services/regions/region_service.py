"""
region_service.py — 행정구역 DB 조회 + 로컬 PBF에서 OSM 추출

핵심 기능:
1. get_regions(level, parent_osm_id) — 행정구역 목록 반환
2. get_region(osm_id)               — 단일 행정구역 정보 반환
3. extract_osm_for_region(osm_id)   — PBF에서 해당 구역 OSM 추출 → .osm 파일 반환
"""

import json
import os
import sqlite3
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "regions.db"
NETWORKS_DIR = Path(__file__).parent.parent.parent.parent / "networks"

# 전국 OSM PBF — 구역 드래그·행정구역 선택 때 인터넷 없이 여기서 잘라 쓴다.
# ⚠️ 파일명에 배포 날짜가 들어간다(south-korea-YYMMDD.osm.pbf). 예전에는 그 날짜를 코드에
#    박아뒀는데, 새 파일을 받으면 이름이 안 맞아 **조용히 다운로드 모드로 떨어졌다**
#    (2026-08-12: 코드는 260711을 찾는데 실제 파일은 260811이었다). 게다가 같은 경로가
#    네 군데에 흩어져 있어 한 곳만 고치면 나머지가 어긋났다. 그래서 이름을 박지 않고
#    아래 순서로 찾는다 — 이 함수가 PBF 경로의 **유일한 출처**다.
_PBF_ROOT = Path(__file__).resolve().parents[4]          # v2x_lab/
_PBF_SEARCH_DIRS = [
    _PBF_ROOT,                       # 작업폴더 바로 아래 (지금 파일이 여기 있다)
    _PBF_ROOT / "data" / "raw",      # 예전 코드가 기대하던 자리
    Path.home() / "Desktop",         # 그보다 더 예전 자리 — 남아 있으면 계속 쓴다
]


def resolve_local_pbf() -> Optional[Path]:
    """쓸 수 있는 전국 PBF 경로. 없으면 None(호출부가 Overpass 다운로드로 폴백).

    `LOCAL_PBF_PATH` 환경변수가 있으면 그것만 쓴다. 없으면 위 폴더들에서
    `south-korea-*.osm.pbf`를 찾아 **가장 최근 파일**을 고른다.
    """
    env = os.getenv("LOCAL_PBF_PATH")
    if env:
        p = Path(env)
        return p if p.exists() else None
    newest: Optional[Path] = None
    for d in _PBF_SEARCH_DIRS:
        try:
            for p in d.glob("south-korea-*.osm.pbf"):
                if newest is None or p.stat().st_mtime > newest.stat().st_mtime:
                    newest = p
        except OSError:
            continue
    return newest


# 예전 이름 유지 — 호출부가 `pbf_path=DEFAULT_PBF` 기본값으로 쓰고 있다.
# 존재하지 않을 수도 있으므로 호출부는 반드시 .exists()를 확인한다.
DEFAULT_PBF = resolve_local_pbf() or (_PBF_ROOT / "south-korea.osm.pbf")

# ── 시도별 미리 자른 조각 ─────────────────────────────────────────────────────
# 전국 PBF에는 위치 색인이 없어서 구역 하나를 뽑아도 272MB를 통째로 훑어야 한다.
# 시도별로 한 번 잘라두면(scripts/build_osm_slices.py) 그 다음부터는 해당 시도 조각만
# 읽으면 되므로 훨씬 빠르다 — 거친 색인을 직접 만들어 두는 셈이다.
SLICE_DIR = Path(__file__).parent.parent.parent.parent / "data" / "osm_slices"
SLICE_INDEX = SLICE_DIR / "index.json"


def resolve_source_pbf(s: float, w: float, n: float, e: float) -> Path:
    """이 구역을 뽑는 데 **가장 작은** 원본을 고른다.

    구역을 통째로 담고 있는 시도 조각이 있으면 그것을, 없으면(조각을 아직 안 만들었거나
    구역이 시도 경계를 걸치면) 전국 파일을 돌려준다. 조각은 도로만 담고 있고, 이 앱은
    도로만 쓰므로 결과는 같다.
    """
    best: Optional[Path] = None
    best_area = float("inf")
    try:
        index = json.loads(SLICE_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_PBF
    for meta in index.values():
        if not (meta["s"] <= s and meta["n"] >= n and meta["w"] <= w and meta["e"] >= e):
            continue                       # 구역을 다 못 덮으면 쓸 수 없다
        f = SLICE_DIR / meta["file"]
        if not f.exists():
            continue
        area = (meta["n"] - meta["s"]) * (meta["e"] - meta["w"])
        if area < best_area:
            best, best_area = f, area
    return best or DEFAULT_PBF

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


def _is_ascii_path(p: Path) -> bool:
    try:
        str(p).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _ascii_work_dir() -> Path:
    """osmium 작업용 **순수 ASCII 경로** 폴더.

    ⚠️ Windows의 libosmium은 경로에 한글이 섞이면 파일을 열지 못한다 — 읽기·쓰기 모두
    `Open failed ... unknown error`로 죽는다(2026-08-12 실측). 그런데 이 프로젝트는
    통째로 `C:\\Users\\<한글 이름>\\` 아래에 있어서, 전국 PBF도 결과 .osm도 전부
    한글 경로다. 그래서 osmium이 실제로 만지는 파일만 여기로 옮겨 놓고 작업한다.
    (임시폴더도 %LOCALAPPDATA%\\Temp라 한글이므로 쓸 수 없다.)
    """
    d = Path(os.environ.get("SystemDrive", "C:") + "\\") / "v2x_osm_work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stage_pbf_for_osmium(pbf_path: Path) -> Path:
    """PBF를 ASCII 경로에서 볼 수 있게 만든다. 이미 ASCII면 그대로 쓴다.

    같은 드라이브면 하드링크라 **즉시 끝나고 용량도 안 먹는다**(원본 그대로를 가리킨다).
    드라이브가 다르면 어쩔 수 없이 복사한다(수백 MB — 처음 한 번만).
    """
    if _is_ascii_path(pbf_path):
        return pbf_path
    dst = _ascii_work_dir() / pbf_path.name
    try:
        if dst.exists():
            if dst.stat().st_size == pbf_path.stat().st_size:
                return dst          # 이미 준비돼 있음
            dst.unlink()
        os.link(pbf_path, dst)      # 하드링크
    except OSError:
        if not (dst.exists() and dst.stat().st_size == pbf_path.stat().st_size):
            shutil.copy2(pbf_path, dst)
    return dst


def _extract_with_osmium(osmium_bin: str, pbf_path: Path, out_file: Path,
                          s: float, w: float, n: float, e: float) -> None:
    """osmium extract --bbox를 사용한 고속 추출"""
    # CLI도 내부는 같은 libosmium이라 한글 경로에서 같은 문제를 겪는다 — 같은 방식으로 우회.
    src = _stage_pbf_for_osmium(Path(pbf_path))
    out_file = Path(out_file)
    stage_out = not _is_ascii_path(out_file)
    real_out = out_file
    if stage_out:
        out_file = _ascii_work_dir() / f"_extract_{os.getpid()}.osm"
    bbox_str = f"{w:.6f},{s:.6f},{e:.6f},{n:.6f}"
    cmd = [
        osmium_bin, "extract",
        "--bbox", bbox_str,
        "--strategy", "complete_ways",
        str(src),
        "-o", str(out_file),
        "--overwrite",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"osmium extract 실패: {result.stderr}")
    if stage_out:
        real_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(out_file), str(real_out))


def _extract_with_pyosmium(pbf_path: Path, out_file: Path,
                             s: float, w: float, n: float, e: float,
                             low_memory: bool = False) -> None:
    """전국 PBF에서 bbox 안의 **도로망**을 뽑아 OSM XML로 저장한다.

    ⚡ 왜 두 번 읽나 — 파이썬 콜백을 없애기 위해서다.
    예전에는 SimpleHandler로 한 번에 훑었는데, 그러면 **전국 노드 1억 개가 하나씩
    파이썬으로 올라와** 구역 크기와 무관하게 4~5분이 걸렸다(2026-08-12 실측 250초).
    지금은 걸러내는 일을 전부 C++(osmium 필터)에 맡기고, 파이썬은 실제로 필요한
    것만 받는다 — 같은 구역이 **44초**로 줄었다(도로 1,259개 / 노드 4,333개).

      패스 1: 도로(highway)만. 노드는 마스크에 넣되 EntityFilter로 걸러 파이썬까지
              오지 않게 하고, with_locations()로 각 노드 좌표만 C++ 캐시에 담는다.
              그 좌표로 bbox 판정을 해서 남길 도로와 필요한 노드 id를 모은다.
      패스 2: 그 노드 id만 IdFilter로 걸러 기록한다.

    도로가 아닌 way(건물·용도지역 등)는 버린다 — 예전 결과 6,930개 중 실제 도로는
    1,218개뿐이었고, 나머지는 이 앱의 어느 단계도 쓰지 않는다(건물 차폐는 PostGIS/
    parquet에서 따로 읽는다). 그 덕에 파일이 7.3MB → 0.83MB로 줄어 netconvert도 빨라진다.

    ⚠️ 남긴 도로의 노드만 기록하므로 결과 파일은 **스스로 완결된다**. 예전에는 경계를
       걸친 도로가 저장하지 않은 노드를 가리켜(38,201개 중 2,431개) osmnx가 읽다 죽었고,
       구역 설정이 마지막 단계에서 500으로 끝났다.
    """
    import osmium
    import osmium.filter as _F

    # 전국 파일 대신 이 구역을 담고 있는 **시도 조각**이 있으면 그걸 읽는다(훨씬 작다).
    # 조각을 만드는 중(build_osm_slices)에는 pbf_path가 이미 전국 파일이고 조각이 아직
    # 없으므로 그대로 전국을 읽는다 — 자기 자신을 원본으로 삼는 일은 생기지 않는다.
    chosen = Path(pbf_path)
    if chosen == DEFAULT_PBF:
        chosen = resolve_source_pbf(s, w, n, e)

    # 한글 경로 우회 — 읽는 PBF와 쓰는 .osm 둘 다 ASCII 경로여야 한다(위 _ascii_work_dir 설명).
    src = _stage_pbf_for_osmium(chosen)
    out_file = Path(out_file)
    stage_out = not _is_ascii_path(out_file)
    real_out = out_file
    if stage_out:
        out_file = _ascii_work_dir() / f"_extract_{os.getpid()}{out_file.suffix}"
    if out_file.exists():
        out_file.unlink()

    def _way_pass():
        """도로만 올라오는 반복자 — 노드는 C++에서 걸러져 파이썬까지 오지 않는다."""
        return (osmium.FileProcessor(str(src), osmium.osm.NODE | osmium.osm.WAY)
                .with_locations()
                .with_filter(_F.EntityFilter(osmium.osm.WAY))
                .with_filter(_F.KeyFilter("highway")))

    def _touches(way) -> tuple[bool, list[int]]:
        refs, inside = [], False
        for nd in way.nodes:
            refs.append(nd.ref)
            loc = nd.location
            if loc.valid() and s <= loc.lat <= n and w <= loc.lon <= e:
                inside = True
        return inside, refs

    # ── 패스 1: 남길 도로가 쓰는 노드 id
    # low_memory면 **id만** 모은다. 도(道) 하나처럼 넓은 구역에서는 도로의 좌표·태그까지
    # 들고 있으면 메모리가 1.7GB를 넘어간다(2026-08-13 실측, 강원도에서 확인). 대신 도로를
    # 한 번 더 읽는다 — 스캔 1회를 더 쓰는 대신 메모리가 id 집합 하나로 줄어든다.
    kept_ways: list[tuple[int, list[int], dict]] = []
    needed: set[int] = set()
    for way in _way_pass():
        inside, refs = _touches(way)
        if not (inside and len(refs) >= 2):
            continue
        needed.update(refs)
        if not low_memory:
            kept_ways.append((way.id, refs, dict(way.tags)))

    # ── 패스 2: 필요한 노드만 기록(신호등·횡단보도 태그도 원본 그대로 보존된다)
    writer = osmium.SimpleWriter(str(out_file))
    written: set[int] = set()
    if needed:
        fp2 = (osmium.FileProcessor(str(src), osmium.osm.NODE)
               .with_filter(_F.IdFilter(sorted(needed))))
        for nd in fp2:
            writer.add_node(nd)
            written.add(nd.id)
    del needed

    # ── 패스 3(low_memory일 때만): 도로를 다시 읽어 바로 기록
    if low_memory:
        for way in _way_pass():
            inside, refs = _touches(way)
            if not inside:
                continue
            kept = [r for r in refs if r in written]
            if len(kept) >= 2:
                writer.add_way(osmium.osm.mutable.Way(id=way.id, nodes=kept,
                                                      tags=dict(way.tags)))
    else:
        for wid, refs, tags in kept_ways:
            kept = [r for r in refs if r in written]
            if len(kept) >= 2:
                writer.add_way(osmium.osm.mutable.Way(id=wid, nodes=kept, tags=tags))
    writer.close()

    if stage_out:
        real_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(out_file), str(real_out))


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

"""교통량조사 원본(xlsx + shapefile) → 정규화 parquet 2개.

산출물 (`data/processed/traffic_survey/`):
    survey_points.parquet  — 지점 마스터. 위치 + **표준노드링크 등급(road_rank)**
    survey_hourly.parquet  — 지점 × 방향 × 시각 실측 대수 (차종 10개 원본 보존)
    survey_metadata.json   — 조인·스냅 성공률 등 재현 정보

기존 `preprocess_traffic_survey.py`(전국 등급 평균 CSV)를 대체하지 않는다. 그쪽은
시간대 프로파일을 만들고 실제로 쓰이고 있으므로 그대로 둔다. 이 모듈이 추가하는 것은
**위치**와 그로부터 얻는 **실제 도로 등급**이다.

── 원본을 다루며 알아낸 것 (전부 실측, 2026-07-30) ──────────────────────────────

1. **조인키는 `NEW_NO`다.** 기관 답변: "교통량자료(엑셀)의 JJ_CODE와 지점자료(GIS)의
   New_no가 매칭된다." 경기도만 컬럼명이 `CODE2`이고 값 형식은 동일(`RO001A030`).
   16개 시도 549지점 중 546개(99%) 매칭.

2. **`.prj`는 경기도에만 있다.** 나머지 15개 시도는 좌표계 정보가 없다. 경기 `.prj`
   (Tokyo 데이텀 · 중앙자오선 128° · scale 0.9999 · 원점가산 400000/600000)를 그대로
   적용하면 15개 시도 전부 제 위치에 떨어진다(서울 2km · 대전 1km · 부산 6km 오차,
   도 단위는 조사지점이 기하 중심에 없어 20~50km는 정상). 표준 한국 벨트(EPSG:5174/5186
   등)는 전부 100km 이상 틀린다 — 별도 원점이므로 `.prj`가 유일한 근거다.

3. **xlsx `jj_kind`로는 도로 등급을 못 얻는다.** 서울은 숫자 코드('104','107'…)라
   기존 `classify_grade()`가 전부 기본값 `시도·간선`으로 떨어뜨린다. 그래서 좌표로
   표준노드링크에 스냅해 실제 `road_rank`를 읽는다 — 이게 이 모듈의 핵심 값어치다.

4. **셀 타입 속성 순서에 주의.** `<c r="A2" s="1" t="s">`처럼 스타일(s)이 타입(t) 앞에
   올 수 있다. 직접 XML을 파싱하면 타입을 놓쳐 공유문자열 인덱스를 숫자로 착각한다
   (그래서 여기서는 openpyxl을 쓴다).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

import geopandas as gpd
import pandas as pd

from .repository import (
    CAR_COLUMNS,
    MAX_SNAP_DIST_M,
    default_processed_dir,
    default_raw_dir,
    standard_links_path,
)

# 표준노드링크 스냅 시 지점 근방만 잘라 쓸 여유(도). 약 3km.
_LINK_PAD_DEG = 0.03


def _log_noop(_: str) -> None:
    pass


# ── 지점 위치 (shapefile) ─────────────────────────────────────────────────────

def _reference_crs(raw_dir: Path):
    """좌표계 기준 — 경기도 `.prj`. 이것만이 근거다(모듈 docstring 2번)."""
    shp = sorted((raw_dir / "14_경기도").glob("*.shp"))
    if not shp:
        raise FileNotFoundError(
            f"좌표계 기준인 경기도 shapefile을 찾을 수 없습니다: {raw_dir / '14_경기도'}\n"
            f"  경기도만 .prj를 갖고 있고 나머지 15개 시도가 그 좌표계를 물려받습니다."
        )
    crs = gpd.read_file(shp[0], encoding="cp949", rows=1).crs
    if crs is None:
        raise RuntimeError(f"경기도 shapefile에 좌표계가 없습니다: {shp[0]}")
    return crs


def _read_points(region_dir: Path, crs, log: Callable[[str], None]) -> Optional[gpd.GeoDataFrame]:
    """시도 하나의 지점 shapefile → survey_code·point_name·lanes·geom(4326)."""
    shp = sorted(region_dir.glob("*.shp"))
    if not shp:
        return None
    g = gpd.read_file(shp[0], encoding="cp949")
    code_col = next((c for c in g.columns if c.upper() in ("NEW_NO", "CODE2")), None)
    if code_col is None:
        log(f"⚠️ {region_dir.name}: 지점 코드 컬럼(NEW_NO/CODE2)이 없어 건너뜁니다")
        return None
    g = g[g.geometry.notna() & g[code_col].notna()].copy()
    if g.empty:
        return None

    g["survey_code"] = g[code_col].astype(str).str.strip()
    g = g[g["survey_code"].str.match(r"^[A-Z]{2}\d", na=False)]
    name_col = next((c for c in g.columns if c in ("지점명", "ROAD_NAME")), None)
    g["point_name"] = g[name_col].astype(str) if name_col else None
    lane_col = next((c for c in g.columns if c.upper() == "LANES"), None)
    # 0차로는 "없음"이지 실제 값이 아니다 — 그대로 두면 용량 계산에서 0으로 곱해진다
    g["lanes"] = (pd.to_numeric(g[lane_col], errors="coerce").replace(0, pd.NA)
                  if lane_col else pd.NA)
    g["region_code"] = region_dir.name.split("_")[0]

    # 경기는 이미 4326 컬럼을 갖고 있지만 geometry는 투영좌표다 — 일관되게 변환한다
    g = g.set_crs(crs, allow_override=True).to_crs("EPSG:4326")
    return g[["survey_code", "region_code", "point_name", "lanes", "geometry"]]


# ── 시간대별 교통량 (xlsx) ────────────────────────────────────────────────────

def _read_volumes(region_dir: Path, log: Callable[[str], None]) -> Optional[pd.DataFrame]:
    """시도 하나의 교통량 xlsx → survey_code·direction·hour·car1..car10.

    `jj_code`가 `RO004H027-1` 형태로 **방향 접미사**를 갖는다. 방향은 살려서 저장하고
    합산은 조회 쪽(`repository.rank_baseline`)에 맡긴다 — 원본을 잃지 않기 위해서다.
    """
    xls = sorted(region_dir.glob("교통량*.xlsx"))
    if not xls:
        return None
    df = pd.read_excel(xls[0], engine="openpyxl")
    if df.shape[1] < 15:
        log(f"⚠️ {region_dir.name}: 열이 {df.shape[1]}개뿐입니다(15개 필요) — 건너뜁니다")
        return None

    cols = list(df.columns)
    out = pd.DataFrame({
        "raw_code": df[cols[0]].astype(str).str.strip(),
        "josa_date": df[cols[3]],
        "hour": df[cols[4]].astype(str).str.extract(r"(\d{1,2})")[0],
    })
    for i, name in enumerate(CAR_COLUMNS):
        out[name] = pd.to_numeric(df[cols[5 + i]], errors="coerce").fillna(0).astype("int32")

    split = out["raw_code"].str.split("-", n=1, expand=True)
    out["survey_code"] = split[0].str.strip()
    out["direction"] = pd.to_numeric(split[1] if split.shape[1] > 1 else None,
                                     errors="coerce").fillna(1).astype("int8")
    out["hour"] = pd.to_numeric(out["hour"], errors="coerce")
    out = out[out["survey_code"].str.match(r"^[A-Z]{2}\d", na=False)]
    out = out[out["hour"].between(0, 23)]
    if out.empty:
        return None
    out["hour"] = out["hour"].astype("int8")
    out["survey_date"] = _parse_survey_date(out["josa_date"])
    out["region_code"] = region_dir.name.split("_")[0]
    return out.drop(columns=["raw_code", "josa_date"])


def _parse_survey_date(col: pd.Series) -> pd.Series:
    """`josa_date` → 날짜.

    ⚠️ 셀 서식에 따라 **두 형태로 들어온다.** openpyxl이 날짜 서식 셀은 Timestamp로
    변환해 주지만(전 시도 실측: 2010-10-28 등), 서식이 없으면 엑셀 일련번호
    (40485 → 2010-11-01)로 남는다. 일련번호로만 해석하면 Timestamp 열이 통째로 NaT가
    되고 `survey_year`가 None이 된다(2026-07-30에 그렇게 나왔다).
    """
    if pd.api.types.is_datetime64_any_dtype(col):
        return pd.to_datetime(col, errors="coerce")
    parsed = pd.to_datetime(col, errors="coerce")
    if parsed.notna().any():
        return parsed
    return pd.to_datetime(pd.to_numeric(col, errors="coerce"),
                          unit="D", origin="1899-12-30", errors="coerce")


# ── 표준노드링크 등급 스냅 ────────────────────────────────────────────────────

def _snap_road_rank(points: gpd.GeoDataFrame, log: Callable[[str], None]) -> gpd.GeoDataFrame:
    """각 지점을 가장 가까운 표준노드링크에 붙여 `road_rank`·`link_id`·`snap_dist_m`를 채운다.

    ⚠️ `MAX_SNAP_DIST_M`를 넘으면 `road_rank`를 **NULL로 남긴다.** 잘못 스냅된 등급이
       기준표를 오염시키는 것이 지점 몇 개를 잃는 것보다 나쁘다(사용자 결정 2026-07-30).
       실측은 중앙 5m / p90 10m로 아주 좋아서 실제로 걸리는 지점은 거의 없다.
    """
    lp = standard_links_path()
    if not lp.exists():
        log(f"⚠️ 표준노드링크가 없어 등급 스냅을 건너뜁니다: {lp}\n"
            f"   scripts/preprocess_standard_links.py 를 먼저 실행하세요.")
        for c in ("road_rank", "link_id", "snap_dist_m"):
            points[c] = pd.NA
        return points

    links = gpd.read_parquet(lp, columns=["link_id", "road_rank", "geometry"])
    log(f"표준노드링크 {len(links):,}개 — 시도별로 근방만 잘라 스냅")

    out = []
    for region, sub in points.groupby("region_code", sort=True):
        minx, miny, maxx, maxy = sub.total_bounds
        near = links.cx[minx - _LINK_PAD_DEG:maxx + _LINK_PAD_DEG,
                        miny - _LINK_PAD_DEG:maxy + _LINK_PAD_DEG]
        if near.empty:
            log(f"⚠️ {region}: 근방에 표준링크가 없어 등급을 비웁니다")
            sub = sub.assign(road_rank=pd.NA, link_id=pd.NA, snap_dist_m=pd.NA)
            out.append(sub)
            continue
        # 미터 좌표계에서 재야 거리가 m로 나온다(중부원점 GRS80)
        j = gpd.sjoin_nearest(sub.to_crs(5186), near.to_crs(5186),
                              how="left", distance_col="snap_dist_m")
        # 같은 거리의 링크가 둘이면 sjoin_nearest가 행을 복제한다 → 지점당 하나만
        j = j.sort_values("snap_dist_m").drop_duplicates(subset=["survey_code"])
        out.append(j.drop(columns=["index_right"], errors="ignore").to_crs("EPSG:4326"))

    snapped = gpd.GeoDataFrame(pd.concat(out, ignore_index=True), crs="EPSG:4326")
    far = snapped["snap_dist_m"] > MAX_SNAP_DIST_M
    if far.any():
        log(f"⚠️ {int(far.sum())}개 지점이 표준링크에서 {MAX_SNAP_DIST_M:.0f}m 넘게 떨어져 "
            f"등급을 비웁니다(최대 {snapped.loc[far, 'snap_dist_m'].max():.0f}m)")
        snapped.loc[far, ["road_rank", "link_id"]] = pd.NA
    ok = snapped["road_rank"].notna()
    log(f"등급 스냅 {int(ok.sum())}/{len(snapped)}개 | 거리 중앙 "
        f"{snapped['snap_dist_m'].median():.0f}m / p90 {snapped['snap_dist_m'].quantile(0.9):.0f}m")
    return snapped


# ── 오케스트레이터 ────────────────────────────────────────────────────────────

def preprocess_traffic_survey(
    raw_dir: Optional[Path] = None,
    processed_dir: Optional[Path] = None,
    log: Optional[Callable[[str], None]] = None,
) -> dict:
    """원본 → `survey_points.parquet` + `survey_hourly.parquet` + 메타데이터.

    PostGIS를 쓰지 않는다 — 지점이 549개뿐이라 parquet + geopandas로 충분하고,
    건물·표준노드링크와 같은 방식이라 PostGIS 없는 환경에서도 동작한다.
    """
    log = log or print
    raw_dir = Path(raw_dir) if raw_dir else default_raw_dir()
    processed_dir = Path(processed_dir) if processed_dir else default_processed_dir()
    processed_dir.mkdir(parents=True, exist_ok=True)
    if not raw_dir.exists():
        raise FileNotFoundError(f"원본 디렉터리가 없습니다: {raw_dir}")

    crs = _reference_crs(raw_dir)
    log(f"좌표계 기준(경기 .prj): {str(crs)[:70]}…")

    pt_frames: list[gpd.GeoDataFrame] = []
    vol_frames: list[pd.DataFrame] = []
    regions: list[dict] = []
    for region_dir in sorted(p for p in raw_dir.iterdir() if p.is_dir()):
        pts = _read_points(region_dir, crs, log)
        vols = _read_volumes(region_dir, log)
        if pts is None or vols is None:
            continue
        gis_codes = set(pts["survey_code"])
        xls_codes = set(vols["survey_code"])
        matched = gis_codes & xls_codes
        regions.append({
            "region": region_dir.name,
            "gis_points": len(gis_codes),
            "xlsx_points": len(xls_codes),
            "matched": len(matched),
            "unmatched_xlsx": sorted(xls_codes - gis_codes)[:10],
        })
        log(f"  {region_dir.name:<16} GIS {len(gis_codes):>3} · xlsx {len(xls_codes):>3} "
            f"· 매칭 {len(matched):>3} ({len(matched) / max(len(xls_codes), 1) * 100:.0f}%)")
        pt_frames.append(pts[pts["survey_code"].isin(matched)])
        vol_frames.append(vols[vols["survey_code"].isin(matched)])

    if not pt_frames:
        raise RuntimeError(f"쓸 수 있는 시도 자료가 없습니다: {raw_dir}")

    points = gpd.GeoDataFrame(pd.concat(pt_frames, ignore_index=True), crs="EPSG:4326")
    points = points.drop_duplicates(subset=["survey_code"]).reset_index(drop=True)
    hourly = pd.concat(vol_frames, ignore_index=True)
    hourly = hourly.drop_duplicates(subset=["survey_code", "direction", "hour"])
    log(f"지점 {len(points)}개 / 시간대 행 {len(hourly):,}개")

    points = _snap_road_rank(points, log)
    year = pd.to_datetime(hourly["survey_date"], errors="coerce").dt.year
    points["survey_year"] = int(year.mode().iloc[0]) if year.notna().any() else None

    points = points[[
        "survey_code", "region_code", "point_name", "lanes",
        "road_rank", "link_id", "snap_dist_m", "survey_year", "geometry",
    ]]
    hourly = hourly[["survey_code", "region_code", "direction", "hour",
                     "survey_date", *CAR_COLUMNS]]

    pt_file = processed_dir / "survey_points.parquet"
    hr_file = processed_dir / "survey_hourly.parquet"
    points.to_parquet(pt_file, index=False)
    hourly.to_parquet(hr_file, index=False)

    meta = {
        "raw_dir": str(raw_dir),
        "processed_dir": str(processed_dir),
        "reference_crs": str(crs),
        "crs_source": "14_경기도/*.prj — 유일한 좌표계 근거(다른 15개 시도엔 .prj 없음)",
        "join_key": "xlsx JJ_CODE(방향 접미사 제거) == GIS NEW_NO / 경기 CODE2",
        "n_points": int(len(points)),
        "n_hourly_rows": int(len(hourly)),
        "n_points_with_rank": int(points["road_rank"].notna().sum()),
        "max_snap_dist_m": MAX_SNAP_DIST_M,
        "snap_dist_median_m": round(float(points["snap_dist_m"].median()), 2)
        if points["snap_dist_m"].notna().any() else None,
        # numpy 정수는 json이 못 다룬다 — 반드시 파이썬 int로 내린다
        "survey_year": (int(points["survey_year"].iloc[0])
                        if len(points) and pd.notna(points["survey_year"].iloc[0]) else None),
        "regions": regions,
    }
    (processed_dir / "survey_metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"저장 완료 → {pt_file.name} / {hr_file.name} / survey_metadata.json")
    return meta

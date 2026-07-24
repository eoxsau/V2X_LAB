"""배정 준비 — radiation OD를 SUMO od2trips 입력으로 변환 (문서 §7·§8).

두 산출물:
  1) TAZ 파일  : 격자 존 → 그 존 안의 도로 엣지 목록(길이 가중). 존 내부에서 어느 엣지로
                 출발/도착할지 od2trips가 가중 랜덤으로 뽑는다(§7 disaggregation).
  2) OD 파일   : radiation T_ij 를 SUMO O-format(VISUM)으로.

존↔엣지 정렬: build_zones와 동일한 cell_of(ref_lat, origin_shift) 로 셀 인덱스를 맞춰야
              같은 (ix,iy)에 건물 질량과 도로가 함께 담긴다.

이후 CLI 파이프라인(스크립트/오케스트레이션에서):
  od2trips  --net-file NET --taz-files TAZ --od-matrix-files OD --output-file TRIPS
  duarouter --net-file NET --route-files TRIPS --output-file ROUTES
  sumo      --net-file NET --route-files ROUTES --edgedata-output EDGEDATA ...
"""
from __future__ import annotations

import os
from typing import Optional

from .grid_mass import Zone, cell_of


def build_taz(
    net_file: str,
    cell_size_m: float,
    ref_lat: float,
    origin_shift_m: tuple[float, float] = (0.0, 0.0),
    vclass: str = "passenger",
) -> dict[str, list[tuple[str, float]]]:
    """net.xml 도로 엣지를 격자 셀(TAZ)로 묶는다.

    Returns: { "ix_iy": [(edge_id, length_m), ...] }  — vclass 통행 가능한 엣지만.
    """
    import sumolib  # 지연 임포트 (sumolib 없는 환경 보호)

    net = sumolib.net.readNet(net_file)
    taz: dict[str, list[tuple[str, float]]] = {}
    for e in net.getEdges():
        try:
            if not e.allows(vclass):
                continue
        except Exception:
            pass
        shape = e.getShape()
        if not shape:
            continue
        mx, my = shape[len(shape) // 2]
        lng, lat = net.convertXY2LonLat(mx, my)
        ix, iy = cell_of(lat, lng, cell_size_m, ref_lat, origin_shift_m)
        taz.setdefault(f"{ix}_{iy}", []).append((e.getID(), e.getLength()))
    return taz


def zone_taz_id(z: Zone) -> str:
    """존 → TAZ id (셀 인덱스 문자열). build_taz의 키와 동일 규칙."""
    return f"{z.ix}_{z.iy}"


def write_taz_xml(taz: dict[str, list[tuple[str, float]]], path: str) -> None:
    """SUMO TAZ 파일. 존 내 엣지를 길이 가중(tazSource/tazSink weight)으로."""
    with open(path, "w", encoding="utf-8") as f:
        f.write('<tazs>\n')
        for tid, edges in taz.items():
            f.write(f'  <taz id="{tid}">\n')
            for eid, w in edges:
                f.write(f'    <tazSource id="{eid}" weight="{w:.2f}"/>\n')
                f.write(f'    <tazSink id="{eid}" weight="{w:.2f}"/>\n')
            f.write('  </taz>\n')
        f.write('</tazs>\n')


def write_od_o_format(
    flows,
    zone_taz: dict[int, Optional[str]],
    path: str,
    begin_h: float = 7.0,
    end_h: float = 8.0,
    factor: float = 1.0,
) -> int:
    """radiation ODFlow 목록 → SUMO O-format(VISUM $OR;D2).

    zone_taz: 존 인덱스 → TAZ id(엣지 있는 존만; 없으면 None → 스킵).
    시간(begin_h~end_h)은 시(hour) 단위. 여러 시간대 프로파일은 슬라이스별로 여러 파일.
    Returns: 기록된 OD 라인 수.
    """
    lines: list[tuple[str, str, float]] = []
    for fl in flows:
        o = zone_taz.get(fl.i)
        d = zone_taz.get(fl.j)
        if o is None or d is None or o == d:
            continue
        lines.append((o, d, fl.trips))
    with open(path, "w", encoding="utf-8") as f:
        f.write('$OR;D2\n')
        f.write('* From-Time  To-Time\n')
        f.write(f'{begin_h:.2f} {end_h:.2f}\n')
        f.write('* Factor\n')
        f.write(f'{factor:.2f}\n')
        f.write('* Origin  Destination  Count\n')
        for o, d, t in lines:
            f.write(f'{o} {d} {t:.4f}\n')
    return len(lines)


def map_zones_to_taz(zones: list[Zone], taz: dict) -> dict[int, Optional[str]]:
    """존 인덱스 → TAZ id. 해당 셀에 도로 엣지가 있는 존만(§3-5: 도로 없는 질량셀은 None)."""
    return {i: (zone_taz_id(z) if zone_taz_id(z) in taz else None)
            for i, z in enumerate(zones)}

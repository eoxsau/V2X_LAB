"""도시 규모 지리 좌표에 대한 순수 파이썬 공간 해시 그리드.

scipy 의존 없이(venv에 미설치) 최근접·반경 조회를 O(1) 평균으로 제공한다.
lat/lng를 국소 등거리(equirectangular) 평면(m)으로 투영해 유클리드 거리로 계산하며,
도시 규모(수십 km)에서는 haversine과의 오차가 0.1% 미만이라 배치 비교·라우팅 용도로 충분하다.

사용 패턴
---------
- SA 배치 최적화: 수요점(고정) 그리드를 1회 만들어 `_delta_cost`의 영향수요 스캔을,
  배치(가변) 그리드로 `_assign_demand`의 최근접-BS 조회를 O(N)→O(1)로.
- RL 환경: BS(에피소드 동안 고정) 그리드를 1회 만들어 매 스텝 `_nearest_bs`를 O(N)→O(1)로.

cell_size_m 는 "전형적 조회 반경"과 비슷하게 잡을수록 조회당 훑는 셀 수가 적어 빠르다.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Optional

# 위도 1°당 미터. 경도는 cos(lat)로 축소해 반영한다.
_M_PER_DEG_LAT = 111_320.0


class SpatialGrid:
    """지리 좌표 점들에 대한 균일 셀 공간 해시.

    Parameters
    ----------
    items : list
        인덱싱할 임의 객체 목록(BS dict, 후보 인덱스 등).
    coords_fn : callable(item) -> (lat, lng)
        각 item에서 (위도, 경도)를 뽑는 함수.
    cell_size_m : float
        셀 한 변 길이(m). 전형적 조회 반경과 비슷하게 잡는다.
    """

    __slots__ = (
        "_items", "_coords_fn", "_cell", "_lat0", "_coslat",
        "_grid", "_xy", "_min_cx", "_max_cx", "_min_cy", "_max_cy",
    )

    def __init__(
        self,
        items: list[Any],
        coords_fn: Callable[[Any], tuple[float, float]],
        cell_size_m: float = 300.0,
        ref_lat: Optional[float] = None,
    ) -> None:
        self._items = list(items)
        self._coords_fn = coords_fn
        self._cell = max(float(cell_size_m), 1.0)

        # 경도→미터 축척용 기준 위도. 빈 상태로 시작해 add()로 채우는 경우(블루노이즈 배치)에도
        # 투영이 어긋나지 않도록 ref_lat를 명시로 받을 수 있게 한다.
        if ref_lat is not None:
            self._lat0 = float(ref_lat)
        elif self._items:
            self._lat0 = sum(coords_fn(it)[0] for it in self._items) / len(self._items)
        else:
            self._lat0 = 0.0
        self._coslat = math.cos(math.radians(self._lat0)) or 1e-6

        self._grid: dict[tuple[int, int], list[int]] = {}
        self._xy: list[tuple[float, float]] = []
        self._min_cx = self._min_cy = 1 << 30
        self._max_cx = self._max_cy = -(1 << 30)

        for i, it in enumerate(self._items):
            lat, lng = coords_fn(it)
            self._insert_xy(i, *self._project(lat, lng))

    def _insert_xy(self, idx: int, x: float, y: float) -> None:
        self._xy.append((x, y))
        cx, cy = int(x // self._cell), int(y // self._cell)
        self._grid.setdefault((cx, cy), []).append(idx)
        if cx < self._min_cx: self._min_cx = cx
        if cx > self._max_cx: self._max_cx = cx
        if cy < self._min_cy: self._min_cy = cy
        if cy > self._max_cy: self._max_cy = cy

    def add(self, item: Any) -> int:
        """점을 하나 증분 추가한다(블루노이즈 배치: 배치점이 늘어나며 최근접 조회).
        추가된 item의 인덱스를 반환."""
        idx = len(self._items)
        self._items.append(item)
        lat, lng = self._coords_fn(item)
        self._insert_xy(idx, *self._project(lat, lng))
        return idx

    # ── 내부 ────────────────────────────────────────────────────────────────
    def _project(self, lat: float, lng: float) -> tuple[float, float]:
        return (lng * self._coslat * _M_PER_DEG_LAT, lat * _M_PER_DEG_LAT)

    def _max_ring(self) -> int:
        """그리드가 비어있지 않을 때, 링 확장의 안전 상한(전체 범위를 덮는 값)."""
        if not self._items:
            return 0
        return max(self._max_cx - self._min_cx, self._max_cy - self._min_cy) + 1

    # ── 공개 API ────────────────────────────────────────────────────────────
    def nearest(
        self,
        lat: float,
        lng: float,
        max_radius_m: Optional[float] = None,
    ) -> tuple[Optional[Any], float]:
        """가장 가까운 item과 그 거리(m)를 반환. 없으면 (None, inf).

        max_radius_m 를 주면 그 반경 밖은 무시(수요→커버리지 내 최근접 BS 배정에 사용).
        링을 바깥으로 확장하며, "다음 링에서 나올 수 있는 최소 거리 ≥ 현재 최선"이면 종료해
        정확한 최근접을 보장한다.
        """
        if not self._items:
            return None, float("inf")

        qx, qy = self._project(lat, lng)
        cx, cy = int(qx // self._cell), int(qy // self._cell)
        cell = self._cell
        grid = self._grid
        xy = self._xy

        best_i: Optional[int] = None
        best_d2 = float("inf")
        max_ring = self._max_ring()
        r = 0
        while True:
            # 링 r(셸)만 훑는다: 이전 링 내부는 이미 검사됨
            lo_x, hi_x = cx - r, cx + r
            lo_y, hi_y = cy - r, cy + r
            for gx in range(lo_x, hi_x + 1):
                on_x_edge = (gx == lo_x or gx == hi_x)
                if on_x_edge:
                    y_iter = range(lo_y, hi_y + 1)
                else:
                    # 좌우 가장자리가 아니면 위/아래 변만
                    y_iter = (lo_y, hi_y) if r > 0 else (cy,)
                for gy in y_iter:
                    bucket = grid.get((gx, gy))
                    if not bucket:
                        continue
                    for i in bucket:
                        x, y = xy[i]
                        d2 = (x - qx) * (x - qx) + (y - qy) * (y - qy)
                        if d2 < best_d2:
                            best_d2 = d2
                            best_i = i

            # 다음 링(r+1)의 셀들이 가질 수 있는 질의점까지 최소 거리 = r*cell.
            # 현재 최선이 그보다 가까우면 더 볼 필요 없음.
            if best_i is not None and (r * cell) * (r * cell) >= best_d2:
                break
            # 반경 제한: 다음 링 최소거리가 이미 반경을 넘으면 그 밖엔 후보 없음
            if max_radius_m is not None and (r * cell) > max_radius_m:
                break
            r += 1
            if r > max_ring:
                break

        if best_i is None:
            return None, float("inf")
        d = math.sqrt(best_d2)
        if max_radius_m is not None and d > max_radius_m:
            return None, float("inf")
        return self._items[best_i], d

    def within(self, lat: float, lng: float, radius_m: float) -> list[tuple[Any, float]]:
        """반경 radius_m(m) 안의 모든 (item, 거리) 목록. BS 커버리지 내 수요 조회에 사용."""
        if not self._items:
            return []
        qx, qy = self._project(lat, lng)
        cell = self._cell
        cx, cy = int(qx // self._cell), int(qy // self._cell)
        span = int(radius_m // cell) + 1
        r2 = radius_m * radius_m
        out: list[tuple[Any, float]] = []
        for gx in range(cx - span, cx + span + 1):
            for gy in range(cy - span, cy + span + 1):
                bucket = self._grid.get((gx, gy))
                if not bucket:
                    continue
                for i in bucket:
                    x, y = self._xy[i]
                    d2 = (x - qx) * (x - qx) + (y - qy) * (y - qy)
                    if d2 <= r2:
                        out.append((self._items[i], math.sqrt(d2)))
        return out

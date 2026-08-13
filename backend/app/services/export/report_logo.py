"""
report_logo.py — 시뮬레이터 좌상단 로고를 Word 워터마크용 PNG로 만든다.

프런트엔드에는 이미지 파일이 없고 로고가 **인라인 SVG**로만 있다
(frontend/ui.jsx의 `Icon.route` + styles.css의 `.nav-logo`). 그래서 그 도형을
여기서 그대로 다시 그린다. 원본이 바뀌면 아래 좌표도 같이 고쳐야 한다.

원본 정의 (24×24 viewBox, stroke 1.7, 둥근 끝):
    circle(6,19,r2)  circle(18,5,r2)
    path M8 19h6a3 3 0 0 0 3-3V8          ← 실선 경로
    path M16 5h-6a3 3 0 0 0-3 3v8         ← 점선(대체 경로)
배경: 8px 라운드 사각형, linear-gradient(150deg, #1E3A5F → #2E75B6)

사용자가 진짜 로고 파일을 넣으면 그쪽이 우선한다 — `logo_png()` 주석 참조.
"""
from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Optional

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, FancyBboxPatch, PathPatch
    from matplotlib.path import Path as MPath
    from matplotlib.colors import LinearSegmentedColormap
    import numpy as np
    _OK = True
except ImportError:                       # pragma: no cover
    _OK = False


BRAND_1 = "#1E3A5F"      # styles.css --brand
BRAND_2 = "#2E75B6"      # styles.css --brand-2

# 사용자가 넣어둘 수 있는 실제 로고 파일. 있으면 그림 대신 이걸 쓴다.
ASSET_DIR  = Path(__file__).parent / "assets"
LOGO_FILES = ("logo.png", "logo.jpg", "logo.jpeg")


def _svg_y(y: float) -> float:
    """SVG는 y가 아래로 자란다. matplotlib은 위로 자란다 — 뒤집는다."""
    return 24.0 - y


def _draw_route_icon(ax, color: str, lw: float) -> None:
    """Icon.route를 24×24 좌표계에 그대로 그린다."""
    ax.add_patch(Circle((6, _svg_y(19)), 2, fill=False, ec=color, lw=lw, zorder=5))
    ax.add_patch(Circle((18, _svg_y(5)), 2, fill=False, ec=color, lw=lw, zorder=5))

    # M8 19 h6 a3 3 0 0 0 3 -3 V8  — 오른쪽으로 갔다가 위로 꺾이는 실선
    solid = MPath(
        [(8, _svg_y(19)), (14, _svg_y(19)),
         (15.66, _svg_y(19)), (17, _svg_y(17.66)), (17, _svg_y(16)),
         (17, _svg_y(8))],
        [MPath.MOVETO, MPath.LINETO,
         MPath.CURVE4, MPath.CURVE4, MPath.CURVE4,
         MPath.LINETO],
    )
    ax.add_patch(PathPatch(solid, fill=False, ec=color, lw=lw,
                           capstyle="round", joinstyle="round", zorder=5))

    # M16 5 h-6 a3 3 0 0 0 -3 3 v8 — 왼쪽으로 갔다가 아래로 꺾이는 점선
    dashed = MPath(
        [(16, _svg_y(5)), (10, _svg_y(5)),
         (8.34, _svg_y(5)), (7, _svg_y(6.34)), (7, _svg_y(8)),
         (7, _svg_y(16))],
        [MPath.MOVETO, MPath.LINETO,
         MPath.CURVE4, MPath.CURVE4, MPath.CURVE4,
         MPath.LINETO],
    )
    ax.add_patch(PathPatch(dashed, fill=False, ec=color, lw=lw,
                           linestyle=(0, (0.1, 2.2)),
                           capstyle="round", joinstyle="round", zorder=5))


def render_logo(px: int = 900, watermark: bool = False) -> Optional[bytes]:
    """로고 PNG 바이트.

    watermark=True 이면 배경 사각형 없이 **연한 회색 선화**만 남긴다.
    Word 워터마크는 본문 글자 뒤에 깔리므로, 색이 진하면 글을 못 읽는다.
    """
    if not _OK:
        return None

    fig = plt.figure(figsize=(px / 100, px / 100), dpi=100)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, 24)
    ax.set_ylim(0, 24)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.patch.set_alpha(0.0)

    if watermark:
        _draw_route_icon(ax, "#C8CFDA", lw=1.9)
    else:
        # 둥근 사각형 + 대각선 그라디언트(150deg) — .nav-logo 재현
        grad = LinearSegmentedColormap.from_list("brand", [BRAND_1, BRAND_2])
        g = np.linspace(0, 1, 256).reshape(1, -1)
        g = (g + g.T * 0.6) / 1.6                      # 대각선 방향
        box = FancyBboxPatch(
            (1.2, 1.2), 21.6, 21.6,
            boxstyle="round,pad=0,rounding_size=6.4",
            linewidth=0, transform=ax.transData,
        )
        ax.add_patch(box)
        im = ax.imshow(g, extent=(0, 24, 0, 24), cmap=grad,
                       vmin=0, vmax=1, zorder=1, aspect="auto")
        im.set_clip_path(box)
        _draw_route_icon(ax, "#FFFFFF", lw=1.9)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, transparent=True,
                bbox_inches=None, pad_inches=0)
    plt.close(fig)
    return buf.getvalue()


def logo_png(watermark: bool = False) -> Optional[bytes]:
    """워터마크에 쓸 로고 바이트.

    우선순위: `assets/logo.png` 같은 **실제 파일이 있으면 그것**, 없으면 코드로 그린다.
    실제 로고 파일을 나중에 넣기만 하면 코드 수정 없이 교체된다.
    """
    for name in LOGO_FILES:
        p = ASSET_DIR / name
        if p.exists():
            try:
                return p.read_bytes()
            except OSError:
                pass
    return render_logo(watermark=watermark)

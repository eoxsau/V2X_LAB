"""
report_charts.py — Word 보고서에 넣을 그림을 matplotlib으로 그린다.

각 함수는 **PNG 바이트**를 돌려주고, 그릴 데이터가 모자라면 `None`을 돌려준다.
호출부(report_doc_builder.generate_docx)는 None이면 그 그림만 조용히 건너뛴다 —
그림 하나 때문에 보고서 전체가 실패하면 안 되기 때문이다.

matplotlib이 없으면 `CHARTS_AVAILABLE = False`가 되고 모든 함수가 None을 준다.
보고서는 표만으로도 완결되므로 이건 오류가 아니라 기능 축소로 취급한다.

⚠️ 한글 폰트를 지정하지 않으면 축·범례의 한글이 전부 네모(두부)로 깨진다.
   Word 쪽 폰트 설정과는 완전히 별개라, 여기서 따로 잡아야 한다.
"""
from __future__ import annotations

import io
from typing import Any, Optional

try:
    import matplotlib
    matplotlib.use("Agg")            # 서버에 화면이 없으므로 파일 백엔드로 강제
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    CHARTS_AVAILABLE = True
except ImportError:                  # pragma: no cover
    CHARTS_AVAILABLE = False


# ── 색 — Word 표(DOCX_TBL_*)와 같은 계열로 맞춘다 ─────────────────────────────
NAVY        = "#1F3864"   # 강조(선택된 알고리즘, 개선)
NAVY_LIGHT  = "#8496B0"   # 비교군
GREY        = "#B9C2D0"   # 배경 막대
RED         = "#C0392B"   # 나쁨(악화, 연결 실패)
GRID        = "#D8DEE8"

DPI         = 200         # 인쇄 품질. 150이면 A4에서 글자가 흐릿하다.

# 한글 폰트 후보 — 앞에서부터 설치돼 있는 것을 고른다
_KO_FONT_CANDIDATES = ["Malgun Gothic", "NanumGothic", "Batang", "Gulim", "Dotum"]

_font_ready = False


def _ensure_font() -> None:
    """한글 폰트와 공통 스타일을 한 번만 적용한다."""
    global _font_ready
    if _font_ready or not CHARTS_AVAILABLE:
        return
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in _KO_FONT_CANDIDATES:
        if name in installed:
            plt.rcParams["font.family"] = name
            break
    # 한글 폰트는 유니코드 마이너스(−) 자리가 비어 있는 경우가 많다.
    # 끄지 않으면 음수 부호가 네모로 깨진다 — 개선/악화 그림에서 치명적이다.
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.facecolor"]   = "white"
    plt.rcParams["axes.edgecolor"]     = NAVY_LIGHT
    plt.rcParams["axes.labelcolor"]    = "#333333"
    plt.rcParams["text.color"]         = "#333333"
    plt.rcParams["xtick.color"]        = "#555555"
    plt.rcParams["ytick.color"]        = "#555555"
    _font_ready = True


def _save(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None       # NaN 거르기


# ──────────────────────────────────────────────────────────────────────────────
# 그림 1 — 알고리즘별 총 경로 비용 (가로 막대)
# ──────────────────────────────────────────────────────────────────────────────

def algorithm_cost_bar(algorithms: list[dict], selected: str) -> Optional[bytes]:
    """알고리즘별 총 비용을 가로 막대로. 선택된 알고리즘만 네이비로 강조.

    가로 막대인 이유: 알고리즘 이름이 `baseline_dijkstra`처럼 길어서
    세로 막대로 두면 x축 이름이 겹치거나 기울어진다.
    """
    if not CHARTS_AVAILABLE or not algorithms:
        return None
    _ensure_font()

    rows = [(str(a.get("algorithm", "—")), _num(a.get("total_cost")))
            for a in algorithms]
    rows = [(n, c) for n, c in rows if c is not None]
    if not rows:
        return None
    rows.sort(key=lambda r: r[1])               # 비용 낮은 것이 위로
    names  = [r[0] for r in rows]
    costs  = [r[1] for r in rows]
    colors = [NAVY if n == selected else NAVY_LIGHT for n in names]

    fig, ax = plt.subplots(figsize=(7.0, 0.5 * len(rows) + 1.3))
    bars = ax.barh(names, costs, color=colors, height=0.62, zorder=3)
    ax.invert_yaxis()

    span = max(costs) - min(costs)
    ax.set_xlim(0, max(costs) * 1.14)
    for bar, cost, name in zip(bars, costs, names):
        label = f"{cost:,.1f}" + ("  ◀ 선택" if name == selected else "")
        ax.text(bar.get_width() + max(costs) * 0.012,
                bar.get_y() + bar.get_height() / 2, label,
                va="center", fontsize=9,
                fontweight="bold" if name == selected else "normal",
                color=NAVY if name == selected else "#555555")

    ax.set_xlabel("총 경로 비용 (낮을수록 좋음)", fontsize=9)
    ax.xaxis.grid(True, color=GRID, linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="y", length=0, labelsize=9)
    ax.tick_params(axis="x", labelsize=8)

    # 막대 길이 차이가 1% 미만이면 눈으로 구분이 안 된다 — 숫자를 믿으라고 알린다.
    if max(costs) > 0 and span / max(costs) < 0.01:
        ax.set_title("※ 알고리즘 간 비용 차이가 1% 미만입니다 — 막대 길이보다 수치를 보십시오",
                     fontsize=8, color=RED, pad=8)
    return _save(fig)


# ──────────────────────────────────────────────────────────────────────────────
# 그림 2 — 구간 지연 누적분포 (CDF)
# ──────────────────────────────────────────────────────────────────────────────

def latency_cdf(latencies_ms: list, outage_ms: float = 1000.0) -> Optional[bytes]:
    """경로 **전체 구간**의 지연 CDF. (상위 20개 표본이 아니라 전부를 받아야 한다)

    이 프로젝트의 지연은 **연속 분포가 아니다.** 연결되면 몇 ms, SINR이 기준에 못 미치면
    고정값 1000ms(연결 실패 표시값)로 딱 갈린다. 그래서 평균 하나로 말하면
    "망이 느리다"는 잘못된 인상을 준다. CDF는 그 계단을 그대로 보여준다.
    """
    if not CHARTS_AVAILABLE or not latencies_ms:
        return None
    _ensure_font()

    vals = sorted(v for v in (_num(x) for x in latencies_ms) if v is not None)
    if not vals:
        return None
    n = len(vals)
    ys = [(i + 1) / n * 100 for i in range(n)]
    conn = [v for v in vals if v < outage_ms]
    n_out = n - len(conn)

    fig, ax = plt.subplots(figsize=(7.0, 3.6))
    ax.step(vals, ys, where="post", color=NAVY, linewidth=2.0, zorder=3)
    ax.set_xscale("symlog", linthresh=10)      # 4ms와 1000ms를 한 화면에 담으려면 로그
    ax.set_xlabel("구간 지연 (ms, 로그 눈금)", fontsize=9)
    ax.set_ylabel("누적 구간 비율 (%)", fontsize=9)
    ax.set_ylim(0, 103)
    ax.grid(True, color=GRID, linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(labelsize=8)

    if n_out:
        pct_conn = len(conn) / n * 100
        ax.axvline(outage_ms, color=RED, linestyle="--", linewidth=1.2, zorder=4)
        ax.text(outage_ms, 50, f"  연결 실패 {n_out}개 ({100 - pct_conn:.0f}%)",
                color=RED, fontsize=8.5, va="center", rotation=90)
        ax.axhline(pct_conn, color=NAVY_LIGHT, linestyle=":", linewidth=1.0, zorder=2)
        if conn:
            avg = sum(conn) / len(conn)
            ax.text(vals[0], pct_conn + 2.5,
                    f"연결된 {len(conn)}개 구간 평균 {avg:.1f} ms",
                    color=NAVY, fontsize=8.5, va="bottom")
    return _save(fig)


# ──────────────────────────────────────────────────────────────────────────────
# 그림 5 — 기준선 대비 변화 (0 기준 양방향 막대)
# ──────────────────────────────────────────────────────────────────────────────

# (표시 이름, doc.improvement의 키, 부호 보정, 기준선 절대값 키, 단위)
#
# ⚠️ 부호 규칙이 항목마다 다르다. "비용 개선 (%)"만 **+가 좋음**(개선율)이고
#    나머지 셋은 **−가 좋음**(변화량)이다. 그대로 그리면 좋은 것과 나쁜 것이
#    같은 방향을 가리켜 그림이 거짓말을 한다. 그래서 비용만 −1을 곱해
#    전부 "줄면 좋다"로 통일한다 → 왼쪽 = 개선, 오른쪽 = 악화.
#
# ⚠️ 단위가 %·ms·m·회로 제각각이라 절대값을 한 축에 놓으면 큰 값이 작은 값을 뭉갠다
#    (실측: 거리 −1560m 옆에서 비용 −3.6%와 핸드오버 +7회의 막대가 보이지 않았다).
#    그래서 **전부 기준선 대비 변화율(%)로 환산**해 한 축에 올리고, 실제 값은 라벨에 적는다.
_DELTA_FIELDS: list[tuple[str, str, int, str, str]] = [
    ("총 비용",    "비용 개선 (%)",   -1, "",                  "%"),
    ("평균 지연",  "지연 변화 (ms)",  +1, "average_latency_ms", "ms"),
    ("이동 거리",  "거리 변화 (m)",   +1, "total_distance_m",   "m"),
    ("핸드오버",   "핸드오버 변화",   +1, "handover_count",     "회"),
]


def improvement_delta_bar(improvement: dict,
                          algorithms: Optional[list[dict]] = None,
                          selected: str = "", baseline: str = "") -> Optional[bytes]:
    """기준선 대비 변화를 0을 축으로 좌우로 그린다. x축 단위는 **변화율(%)** 하나로 통일.

    변화율 = 변화량 ÷ 기준선 절대값 × 100. 기준선 값은 `algorithms`에서 baseline 행을
    찾아 쓴다. 그 행이 없거나 값이 0이면 그 항목은 **그리지 않는다** — 억지로 0으로
    채우면 "변화 없음"이라는 거짓 정보가 되기 때문이다.

    색은 좋고 나쁨으로 칠한다 — 왼쪽(감소)이 네이비, 오른쪽(증가)이 빨강.
    """
    if not CHARTS_AVAILABLE or not improvement:
        return None
    _ensure_font()

    base_row: dict = {}
    for a in (algorithms or []):
        if str(a.get("algorithm", "")) == baseline:
            base_row = a
            break

    items: list[tuple[str, float, str]] = []   # (라벨, 변화율%, 실제값 표기)
    for label, key, sign, base_key, unit in _DELTA_FIELDS:
        raw = _num(improvement.get(key))
        if raw is None:
            continue
        raw *= sign                            # 보정 후 항상 "−가 좋음"
        if not base_key:                        # 이미 %로 들어온 항목(총 비용)
            items.append((label, raw, f"{raw:+.1f}%"))
            continue
        base_v = _num(base_row.get(base_key))
        if base_v is None or base_v == 0:
            continue                            # 환산 불가 → 조용히 뺀다
        pct = raw / abs(base_v) * 100.0
        num = f"{raw:+,.0f}" if abs(raw) >= 100 else f"{raw:+,.1f}"
        items.append((label, pct, f"{pct:+.1f}%  ({num} {unit})"))

    if not items:
        return None

    labels = [i[0] for i in items]
    pcts   = [i[1] for i in items]
    colors = [NAVY if v < 0 else RED for v in pcts]

    fig, ax = plt.subplots(figsize=(7.0, 0.6 * len(items) + 1.5))
    bars = ax.barh(labels, pcts, color=colors, height=0.6, zorder=3)
    ax.invert_yaxis()
    ax.axvline(0, color="#444444", linewidth=1.2, zorder=4)

    span = max(abs(v) for v in pcts) or 1.0
    ax.set_xlim(-span * 1.75, span * 1.75)
    for bar, (label, pct, note) in zip(bars, items):
        w = bar.get_width()
        off = span * 0.05 if w >= 0 else -span * 0.05
        ax.text(w + off, bar.get_y() + bar.get_height() / 2, note,
                va="center", ha="left" if w >= 0 else "right",
                fontsize=8.5, fontweight="bold",
                color=NAVY if pct < 0 else RED)

    ax.set_xlabel("← 감소(개선)        기준선 대비 변화율 (%)        증가(악화) →", fontsize=9)
    ax.xaxis.grid(True, color=GRID, linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(axis="y", length=0, labelsize=9)
    ax.tick_params(axis="x", labelsize=8)

    if selected and baseline:
        ax.set_title(f"{baseline}  →  {selected}", fontsize=9.5, color=NAVY, pad=10)
    return _save(fig)

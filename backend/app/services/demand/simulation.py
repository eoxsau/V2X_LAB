"""동적 SUMO 실행 — 생성된 경로를 실제로 굴려 정체를 만들고, 피크 시점의 엣지별 교통량을 뽑는다.

    routes.xml → sumo(예열 → 측정) → ① 시간별 주행 대수 곡선  ② 피크 구간 엣지별 교통량
                                                                        → 배치 수요(DemandPoint)

왜 duarouter로 끝내면 안 되나 (문서 §5 6단계):
    duarouter는 **다른 차가 한 대도 없다고 가정하고** 최단경로를 뽑는다. 새벽 3시 기준
    지도 앱과 같다. 실제로 굴려야 차들이 서로 막고 신호에 걸려 줄이 생기고, 그 줄이
    어디에 생기는지가 곧 통신 수요가 몰리는 곳이다.

예열(warm-up):
    t=0의 도로는 완전히 비어 있어 비현실적이다. 현실의 아침 8시 도로엔 이미 차가 있다.
    그래서 앞의 일정 구간은 **굴리되 측정에서 뺀다.** 부수 효과로 od2trips의 첫 구간
    부풀림(§7, 초과분이 첫 인터벌에 몰림)도 함께 흡수된다.

teleport (⚠️ 여기서 문서의 기존 지침을 뒤집었다 — 2026-07-27 실측):
    "정체 연구엔 `--time-to-teleport -1`이 필수"라고 적혀 있었지만, **완전히 끄면 교차로
    교착이 영영 안 풀려 시뮬 자체가 무의미해진다.** 영등포 4000통행 실측:

        --time-to-teleport -1   → 1024대 영구 교착. 3878대 중 2302대만 도착. teleport 0건
        --time-to-teleport 300  → 피크 112대(정지 49%), **전원 도착**. teleport 단 20건(0.5%)

    차량 0.5%가 전체 1024대를 잡고 있었던 것이다. 교착은 용량 포화와 달리 이산 사건이라
    수요량에 비단조로 반응한다(4250통행은 멀쩡한데 4000·4500은 잠김) — 즉 teleport를 끈 채로는
    N* 튜닝이 불가능하다. 무엇을 튜닝하는지 알 수 없기 때문.

    → **긴 유한값(기본 300초)** 을 쓴다. 이 네트워크에서 정상적인 줄서기는 5분을 넘지 않으므로
      진짜 정체는 그대로 남고 교착만 풀린다. `teleports` 건수를 결과에 담아두었으니
      **이 값이 커지면(수 % 이상) 튜닝 결과를 의심할 것.**

조기 종료 (2026-07-28 추가 — N* 보정이 사실상 끝나지 않던 문제):
    실측(안양 5.5km², 211 lane-km, 20,321통행): 판정에 필요한 정보는 **2분**이면 다 나오는데
    실행은 **55분**이 걸렸다. 곡선이 이랬다 —

        t=7.58h  주행 3,923  정지 85%  도착 2,878  순간이동 321  (tele/arr = 0.11)  ← 탈락 확정
        t=9.17h  주행10,441  정지 87%  도착 6,505  순간이동 7,405 (tele/arr = 1.14)

    `MAX_TELEPORT_RATIO=0.10`을 t=7.58h에 이미 넘었다. 나머지 53분은 **아는 답을 다시
    확인하려고** 태운 시간이다. 게다가 이런 실행이 6회 반복되니 클릭 한 번에 3~5시간이었다.

    → `abort_check` 콜백으로 summary를 돌아가는 중에 훔쳐보고, 판정이 확정되면 끊는다.
      반대쪽 낭비(차가 다 빠진 뒤의 빈 꼬리 구간, 여기선 4시간)는 `stop_when_drained`가 자른다.
      **어느 쪽도 결과를 바꾸지 않는다** — 확정된 판정을 앞당길 뿐이고, 빈 스텝은 어차피
      아무 일도 일어나지 않는다.
"""
from __future__ import annotations

import math
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .assignment import read_net
from .pipeline import sumo_binary

DEFAULT_WARMUP_S = 900.0     # 15분. 문서 §5 "15~30분 예열 후 측정"
DEFAULT_INTERVAL_S = 300.0   # 5분 집계. 피크를 이 해상도로 찾는다
DEFAULT_STEP_LENGTH = 0.5    # main.py의 실시간 시뮬과 동일
DEFAULT_IGNORE_JUNCTION_BLOCKER_S = 60.0   # 교차로를 막고 선 차를 무시하기까지의 시간
DEFAULT_TIME_TO_TELEPORT_S = 300.0   # -1(끔)이면 교착이 영구화된다 — 모듈 상단 설명 참조
DEFAULT_POLL_S = 2.0         # 조기 종료 판단 주기. summary는 이보다 훨씬 드물게 갱신된다

# summary 기록 주기 — **엣지 집계 주기(`interval_s`)와 분리한다** (2026-07-28).
#
# sumo는 출력을 ~4KB 버퍼 단위로 흘린다(1.27에 unbuffered 옵션이 없다). summary 한 줄이
# ~340B이므로 period=300이면 **한 번 흘릴 때마다 시뮬 1시간치**가 뭉쳐 나온다 — 조기 종료가
# 그만큼 늦어진다(실측: tele/arr이 0.087까지 오른 걸 보고도 다음 갱신까지 12분을 더 기다림).
# 60초로 줄이면 같은 4KB가 시뮬 12분치가 되어 판정이 5배 빨라진다.
#
# 엣지 집계는 그대로 `interval_s`를 쓴다 — 그쪽은 구간당 표본이 충분해야 하므로 잘게 쪼개면 안 된다.
# summary는 시간 곡선·피크 '시각' 탐색용이라 잘게 쓰는 편이 오히려 정확하다.
SUMMARY_PERIOD_S = 60.0


def _log_noop(_: str) -> None:
    pass


@dataclass
class EdgeLoad:
    """한 집계 구간에서 엣지 하나가 실은 교통량."""
    edge_id: str
    lat: float
    lng: float
    vehicle_count: float   # 구간 평균 동시 주행 대수 (sampledSeconds / 구간길이)
    entered: float         # 구간 동안 진입한 차량 수
    density: float         # 대/km
    mean_speed_kph: float
    occupancy: float       # 0~1


@dataclass
class SimResult:
    """동적 시뮬레이션 1회 결과."""
    net_file: str
    routes_file: str
    summary_file: str
    edgedata_file: str

    begin_s: float
    end_s: float
    warmup_s: float
    interval_s: float

    # 시간 곡선 (예열 포함 전체)
    times_s: list[float] = field(default_factory=list)
    running: list[int] = field(default_factory=list)     # 동시 주행 대수
    halting: list[int] = field(default_factory=list)     # 정지(<0.1m/s) 대수
    teleports: list[int] = field(default_factory=list)   # 누적 순간이동 건수
    ended: list[int] = field(default_factory=list)       # 누적 도착 차량 수
    mean_speed_kph: list[float] = field(default_factory=list)

    peak_time_s: float = 0.0        # 예열 이후 구간에서 가장 붐빈 시각
    peak_running: int = 0
    peak_edges: list[EdgeLoad] = field(default_factory=list)   # 피크 구간의 엣지별 교통량

    # 조기 종료 — end_s까지 안 가고 중간에 끊었다는 뜻. `abort_reason`으로 무엇인지 구분한다:
    #   "gridlock: ..."  판정이 이미 확정돼 끊음 → 호출부가 실패로 처리할 것 (수치 신뢰 불가)
    #   "drained: ..."   차가 다 빠져 남은 구간이 빈 스텝뿐이라 끊음 → **정상 완주와 동등**
    aborted: bool = False
    abort_reason: str = ""

    stats: dict = field(default_factory=dict)

    @property
    def gridlocked(self) -> bool:
        """교착이 확정돼 중단된 실행인가. True면 이 결과의 수치는 쓸 수 없다."""
        return self.aborted and self.abort_reason.startswith("gridlock")

    @property
    def congestion_ratio(self) -> float:
        """피크 시점 정지 차량 비율. 0에 가까우면 정체가 안 생긴 것(§5-1 N* 튜닝 신호)."""
        return self.stats.get("peak_halting_ratio", 0.0)


# ── 실행 ──────────────────────────────────────────────────────────────────────

def run_simulation(
    net_file: str,
    routes_file: str,
    out_dir: str,
    *,
    begin_s: Optional[float] = None,
    end_s: Optional[float] = None,
    warmup_s: float = DEFAULT_WARMUP_S,
    interval_s: float = DEFAULT_INTERVAL_S,
    step_length: float = DEFAULT_STEP_LENGTH,
    ignore_junction_blocker_s: float = DEFAULT_IGNORE_JUNCTION_BLOCKER_S,
    time_to_teleport_s: float = DEFAULT_TIME_TO_TELEPORT_S,
    abort_check: Optional[Callable[[dict], Optional[str]]] = None,
    stop_when_drained: bool = True,
    poll_s: float = DEFAULT_POLL_S,
    prefix: str = "sim",
    log: Optional[Callable[[str], None]] = None,
) -> SimResult:
    """경로파일을 SUMO로 굴리고 시간곡선 + 피크 엣지 교통량을 돌려준다.

    Parameters
    ----------
    begin_s, end_s : 시뮬레이션 구간(초). None이면 routes.xml의 출발시각에서 자동으로 잡고,
        마지막 출발 이후 여유를 둬 주행 중인 차가 빠질 시간을 준다.
    warmup_s : 앞쪽 이 구간은 **굴리되 피크 탐색·측정에서 제외**한다.
    interval_s : 엣지 집계 구간. 피크를 이 해상도로 찾으므로 너무 크면 뭉개진다.
    time_to_teleport_s : 이 시간을 넘게 막힌 차를 순간이동. -1이면 끔.
        **끄지 말 것** — 교착이 영구화된다(모듈 상단 실측 근거). 결과의
        `stats["teleports"]`가 차량 수의 수 %를 넘으면 정체 수치를 신뢰하지 말 것.
    abort_check : `poll_s`마다 summary의 **최신 스텝**을 받아 중단 사유를 돌려주는 콜백.
        None을 주면 계속 진행. N* 보정처럼 "판정만 필요한" 실행에서 쓴다 — 자세한
        근거는 모듈 상단 §조기 종료.
    stop_when_drained : 마지막 차가 출발한 뒤 도로가 비면 남은 구간을 굴리지 않고 끊는다.
        남은 건 빈 스텝뿐이므로 결과가 달라지지 않는다(`still_running_at_end`도 0으로 동일).

    Returns
    -------
    SimResult — `peak_edges`가 곧 배치 수요의 재료다(`edge_loads_to_demand`로 변환).
        `aborted`면 조기 종료된 것이고, `gridlocked`면 수치를 쓰면 안 된다.
    """
    log = log or _log_noop
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    depart_min, depart_max = _depart_range(routes_file)
    if begin_s is None:
        begin_s = math.floor(depart_min)
    if end_s is None:
        # 마지막 출발 뒤 **창 길이의 2배**(최소 1시간)를 더 굴린다.
        #
        # ⚠️ 넉넉해야 한다 (2026-07-27 실측). 예전엔 창의 0.5배(최소 10분)였는데,
        # 영등포 5,394통행에서 종료 10:00 기준 잔류 643대 → **"정체가 안 풀렸다"고 오판**했다.
        # 같은 실행을 12:00까지 굴리면 **잔류 0, 5,202대 전원 도착**이다. 그냥 아직
        # 빠져나가는 중이었을 뿐. 이 오판이 N* 보정을 통째로 흔들었다
        # (같은 수준이 실행마다 통과/실패로 갈리는 것처럼 보임).
        # 빈 스텝은 SUMO에서 거의 공짜라 넉넉히 잡는 비용은 작다 — 느려지는 건
        # 진짜로 교착된 실행뿐이고, 그건 어차피 걸러내야 할 대상이다.
        tail = max(3600.0, (depart_max - depart_min) * 2.0)
        end_s = math.ceil(depart_max + tail)
    log(f"출발 {depart_min / 3600:.2f}h~{depart_max / 3600:.2f}h → "
        f"시뮬 {begin_s / 3600:.2f}h~{end_s / 3600:.2f}h (예열 {warmup_s / 60:.0f}분)")

    summary_f = out / f"{prefix}.summary.xml"
    edgedata_f = out / f"{prefix}.edgedata.xml"
    # 이전 실행의 잔재를 남겨두면 조기 종료 판정이 옛 곡선을 읽는다 — 반드시 지우고 시작한다.
    for stale in (summary_f, edgedata_f):
        stale.unlink(missing_ok=True)

    t_wall = time.perf_counter()
    aborted, abort_reason = _run_sumo(
        net_file, routes_file, summary_f, edgedata_f,
        begin_s, end_s, interval_s, step_length, ignore_junction_blocker_s,
        time_to_teleport_s, abort_check,
        depart_max if stop_when_drained else None, poll_s, log)

    res = SimResult(
        net_file=str(net_file), routes_file=str(routes_file),
        summary_file=str(summary_f), edgedata_file=str(edgedata_f),
        begin_s=begin_s, end_s=end_s, warmup_s=warmup_s, interval_s=interval_s,
        aborted=aborted, abort_reason=abort_reason,
    )
    _parse_summary(summary_f, res)
    if not res.times_s:
        raise RuntimeError(f"summary 출력이 비었습니다: {summary_f}")
    if aborted:
        log(f"조기 종료 ({abort_reason}) — {time.perf_counter() - t_wall:.0f}s, "
            f"t={res.times_s[-1] / 3600:.2f}h / 예정 {end_s / 3600:.2f}h")

    # 피크 = 예열 이후에서 동시 주행 대수 최대 시각
    measure_from = begin_s + warmup_s
    cand = [(t, r, h) for t, r, h in zip(res.times_s, res.running, res.halting) if t >= measure_from]
    if not cand:
        cand = list(zip(res.times_s, res.running, res.halting))
        log("⚠️ 예열 구간이 시뮬 전체보다 깁니다 — 전체에서 피크를 찾습니다.")
    res.peak_time_s, res.peak_running, peak_halting = max(cand, key=lambda x: x[1])

    # 교착으로 끊은 실행은 수치를 어차피 버리므로 엣지 집계(수십 MB 파싱)를 건너뛴다.
    if res.gridlocked:
        res.peak_edges = []
    else:
        net = read_net(str(net_file))
        res.peak_edges = _parse_edgedata(edgedata_f, net, res.peak_time_s)

    res.stats = {
        "n_intervals": int(math.ceil((end_s - begin_s) / interval_s)),
        "peak_time_h": round(res.peak_time_s / 3600, 3),
        "peak_running": res.peak_running,
        "peak_halting": peak_halting,
        "peak_halting_ratio": round(peak_halting / res.peak_running, 3) if res.peak_running else 0.0,
        "mean_speed_at_peak_kph": round(
            res.mean_speed_kph[res.times_s.index(res.peak_time_s)], 1) if res.peak_time_s in res.times_s else 0.0,
        "edges_with_traffic": len(res.peak_edges),
        "max_edge_vehicles": round(max((e.vehicle_count for e in res.peak_edges), default=0.0), 2),
        "teleports": res.teleports[-1] if res.teleports else 0,
        "arrived": res.ended[-1] if res.ended else 0,
        "still_running_at_end": res.running[-1] if res.running else 0,
    }
    log(f"피크 {res.peak_time_s / 3600:.2f}h | 동시주행 {res.peak_running}대 "
        f"(정지 {peak_halting}대 = {res.stats['peak_halting_ratio'] * 100:.0f}%) "
        f"| 교통 있는 엣지 {len(res.peak_edges)}개")
    if res.stats["peak_halting_ratio"] < 0.05:
        log("⚠️ 피크에도 정지 차량이 5% 미만 — 정체가 안 생겼습니다. "
            "total_trips를 올리거나(N* 튜닝) 신호등 수를 확인하세요(§5).")
    arrived = res.stats["arrived"]
    if arrived and res.stats["teleports"] > arrived * 0.03:
        log(f"⚠️ 순간이동 {res.stats['teleports']}건이 도착 차량의 3%를 넘습니다 — "
            f"정체 수치를 그대로 믿지 말 것(교착이 많다는 뜻).")
    if res.stats["still_running_at_end"] > max(arrived * 0.05, 10):
        log(f"⚠️ 시뮬 종료 시점에 {res.stats['still_running_at_end']}대가 남아 있습니다 — "
            f"정체가 풀리지 않았습니다. total_trips를 낮추거나 end_s를 늘리세요.")
    return res


def _run_sumo(
    net_file: str, routes_file: str, summary_f: Path, edgedata_f: Path,
    begin_s: float, end_s: float, interval_s: float, step_length: float,
    ignore_junction_blocker_s: float, time_to_teleport_s: float,
    abort_check: Optional[Callable[[dict], Optional[str]]],
    drain_after_s: Optional[float], poll_s: float,
    log: Callable[[str], None],
) -> tuple[bool, str]:
    """sumo 실행 + `poll_s`마다 summary를 훔쳐보며 조기 종료 판단.

    **`sumo` 본체는 한글 경로에서 정상**이다(2026-07-27 실측: `C:\\Users\\최동혁\\...`에서 rc=0).
    한글 경로에서 죽는 건 od2trips뿐이므로, 여기서는 ASCII 스테이징 없이 제자리에서 돌린다
    (출력이 그대로 남아 디버깅도 쉽다).

    stdout/stderr을 파이프가 아니라 **파일로** 받는 이유: 실행 중에 파이프를 안 읽으면
    버퍼가 차서 자식이 멈춘다. 예전 `subprocess.run(capture_output=True)`은 끝날 때까지
    기다렸으니 문제가 없었지만, 지금은 돌아가는 중에 폴링하므로 파일이어야 안전하다.

    Returns: (aborted, reason) — reason은 "gridlock: ..." | "drained: ..." | "".
    """
    add_f = summary_f.parent / f"{summary_f.name.split('.')[0]}.add.xml"
    # 엣지 집계는 additional 파일로만 period 지정이 가능하다(--edgedata-output엔 period 옵션이 없음)
    add_f.write_text(
        '<additional>\n'
        f'  <edgeData id="ed" file="{edgedata_f.name}" period="{interval_s:.0f}" '
        f'begin="{begin_s:.0f}" end="{end_s:.0f}" excludeEmpty="true"/>\n'
        '</additional>\n', encoding="utf-8")

    # cwd를 출력 폴더로 옮기므로 입력은 전부 절대경로여야 한다
    cmd = [
        sumo_binary("sumo"),
        "-n", str(Path(net_file).resolve()),
        "-r", str(Path(routes_file).resolve()),
        "-a", str(add_f.resolve()),
        "--summary-output", str(summary_f.resolve()),
        # ⚠️ interval_s가 아니다 — 위 SUMMARY_PERIOD_S 주석 참조(버퍼 때문에 분리했다)
        "--summary-output.period", f"{min(interval_s, SUMMARY_PERIOD_S):.0f}",
        "--begin", f"{begin_s:.0f}",
        "--end", f"{end_s:.0f}",
        "--step-length", f"{step_length}",
        # teleport는 끄지 않는다 — 끄면 교착이 영구화된다(모듈 상단 실측 근거).
        # 긴 유한값이라 정상적인 줄서기는 그대로 남고 교착만 풀린다.
        "--time-to-teleport", f"{time_to_teleport_s:.0f}",
        "--ignore-junction-blocker", f"{ignore_junction_blocker_s:.0f}",
        "--collision.action", "none",
        "--no-step-log", "--no-warnings",
    ]
    stdout_f = summary_f.parent / f"{summary_f.name.split('.')[0]}.sumo.log"
    aborted, reason = False, ""

    with open(stdout_f, "w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                cwd=str(summary_f.parent))
        try:
            while True:
                try:
                    proc.wait(timeout=poll_s)
                    break                      # 정상 종료
                except subprocess.TimeoutExpired:
                    pass
                snap = _peek_summary(summary_f)
                if not snap:
                    continue
                r = abort_check(snap) if abort_check else None
                if r is None and drain_after_s is not None:
                    # 마지막 차가 출발했고 · 도로가 비었고 · 삽입 대기줄도 없다
                    # = 남은 건 빈 스텝뿐. `waiting`을 꼭 봐야 한다 — 출발 엣지가 막혀
                    # 대기 중인 차가 있으면 running이 0이어도 아직 끝난 게 아니다.
                    if (snap.get("time", 0.0) >= drain_after_s
                            and snap.get("running", 1) == 0
                            and snap.get("waiting", 1) == 0
                            and snap.get("arrived", 0) > 0):
                        r = f"drained: t={snap['time'] / 3600:.2f}h에 전원 도착"
                if r:
                    aborted, reason = True, r
                    proc.terminate()
                    try:
                        proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    if not aborted and proc.returncode != 0:
        tail = ""
        try:
            tail = stdout_f.read_text(encoding="utf-8", errors="replace")[-2000:]
        except OSError:
            pass
        raise RuntimeError(f"sumo 실패 (rc={proc.returncode}):\n{tail}")
    if not aborted:
        log("sumo OK")

    if not edgedata_f.exists():
        edgedata_f.write_text('<meandata/>\n', encoding="utf-8")
    return aborted, reason


# ── 파싱 ──────────────────────────────────────────────────────────────────────

def _depart_range(routes_file: str) -> tuple[float, float]:
    """경로파일의 출발시각 최소·최대(초)."""
    deps: list[float] = []
    for _, el in ET.iterparse(routes_file, events=("end",)):
        if el.tag == "vehicle":
            d = el.get("depart")
            if d is not None:
                try:
                    deps.append(float(d))
                except ValueError:
                    pass
            el.clear()
    if not deps:
        raise RuntimeError(f"경로파일에 vehicle이 없습니다: {routes_file}")
    return min(deps), max(deps)


_STEP_RE = re.compile(r"<step\s([^<>]*?)/>")
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')

# summary에서 실제로 읽는 필드만 숫자로 변환한다(나머지는 문자열로 남겨도 무해).
_NUM_KEYS = ("time", "running", "halting", "teleports", "ended", "arrived",
             "loaded", "inserted", "waiting", "meanSpeed")


def _read_steps(path: Path) -> list[dict]:
    """summary의 `<step .../>`를 정규식으로 읽는다 — **잘린 파일도 읽히는 게 요점**이다.

    조기 종료는 sumo를 TerminateProcess로 끊으므로 닫는 태그가 없다. `ET.iterparse`는
    그때 ParseError로 통째로 실패한다. `<step>`은 자기완결 태그라 정규식으로 뽑으면
    마지막 한 줄이 깨져 있어도 그 앞까지 온전히 얻는다.
    """
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict] = []
    for m in _STEP_RE.finditer(txt):
        d: dict = {}
        for k, v in _ATTR_RE.findall(m.group(1)):
            if k in _NUM_KEYS:
                try:
                    d[k] = float(v) if k in ("time", "meanSpeed") else int(float(v))
                except ValueError:
                    continue
            else:
                d[k] = v
        if "time" in d:
            out.append(d)
    return out


def _peek_summary(path: Path) -> Optional[dict]:
    """돌아가는 중인 sumo의 summary에서 **최신 스텝** 하나. 못 읽으면 None(다음 폴링에 재시도)."""
    steps = _read_steps(path)
    return steps[-1] if steps else None


def _parse_summary(path: Path, res: SimResult) -> None:
    for d in _read_steps(path):
        res.times_s.append(d.get("time", 0.0))
        res.running.append(d.get("running", 0))
        res.halting.append(d.get("halting", 0))
        res.mean_speed_kph.append(max(d.get("meanSpeed", -1.0), 0.0) * 3.6)
        res.teleports.append(d.get("teleports", 0))
        res.ended.append(d.get("ended", 0))


def _load_xml_tolerant(path: Path, root_tag: str) -> Optional[ET.Element]:
    """XML을 읽되, 조기 종료로 **잘려 있으면 마지막 완결 `</interval>`까지만** 살려서 판다.

    바이트로 읽는 게 중요하다 — sumo 출력에는 `<?xml ... encoding="UTF-8"?>` 선언이 있어
    str로 넘기면 ElementTree가 ValueError를 낸다.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        pass
    cut = raw.rfind(b"</interval>")
    if cut == -1:
        return None
    repaired = raw[:cut + len(b"</interval>")] + f"\n</{root_tag}>\n".encode()
    try:
        return ET.fromstring(repaired)
    except ET.ParseError:
        return None


def _parse_edgedata(path: Path, net, peak_time_s: float) -> list[EdgeLoad]:
    """피크 시각이 속한 집계 구간의 엣지별 교통량.

    `sampledSeconds`(엣지 위에서 보낸 차량-초)를 구간 길이로 나누면 **그 구간의 평균
    동시 주행 대수**가 된다. 배치 수요는 "그 자리에 몇 대가 있나"이므로 이 값이 맞다
    (`entered`는 통과량이라 짧은 엣지에서 과대평가된다).
    """
    root = _load_xml_tolerant(path, "meandata")
    if root is None:
        return []
    best: Optional[ET.Element] = None
    for iv in root.findall("interval"):
        b, e = float(iv.get("begin", 0)), float(iv.get("end", 0))
        if b <= peak_time_s < e:
            best = iv
            break
    if best is None:
        intervals = root.findall("interval")
        if not intervals:
            return []
        best = min(intervals, key=lambda iv: abs(float(iv.get("begin", 0)) - peak_time_s))

    span = max(float(best.get("end", 0)) - float(best.get("begin", 0)), 1e-9)
    loads: list[EdgeLoad] = []
    for e in best.findall("edge"):
        eid = e.get("id")
        if not eid:
            continue
        try:
            edge = net.getEdge(eid)
        except Exception:
            continue
        shape = edge.getShape()
        if not shape:
            continue
        mx, my = shape[len(shape) // 2]
        lng, lat = net.convertXY2LonLat(mx, my)
        sampled = float(e.get("sampledSeconds", 0) or 0)
        loads.append(EdgeLoad(
            edge_id=eid,
            lat=lat, lng=lng,
            vehicle_count=sampled / span,
            entered=float(e.get("entered", 0) or 0),
            density=float(e.get("density", 0) or 0),
            mean_speed_kph=float(e.get("speed", 0) or 0) * 3.6,
            occupancy=float(e.get("occupancy", 0) or 0) / 100.0,
        ))
    loads.sort(key=lambda x: x.vehicle_count, reverse=True)
    return loads


# ── 배치 수요로 변환 ──────────────────────────────────────────────────────────

def edge_loads_to_demand(loads: list[EdgeLoad], min_vehicles: float = 0.0):
    """`EdgeLoad` → 배치 최적화가 쓰는 `DemandPoint` 목록.

    `sa_placement.build_demand_from_graph`가 모든 엣지에 균일하게 5.0을 주던 자리를
    이걸로 대체한다. 균일 수요에서는 최적화가 사실상 "골고루 뿌리기"가 되어버려
    간선·교차로 집중이라는 결론이 나올 수 없다(§8-1).
    """
    from app.services.placement.sa_placement import DemandPoint

    return [
        DemandPoint(id=f"edge-{ld.edge_id}", lat=ld.lat, lng=ld.lng,
                    vehicle_count=ld.vehicle_count)
        for ld in loads if ld.vehicle_count > min_vehicles
    ]


def pick_target_depart_time(
    res: SimResult,
    max_halting: float = 0.6,
    min_running_frac: float = 0.15,
) -> float:
    """타겟 차량을 언제 출발시킬지 — **정체는 있되 움직일 수는 있는** 시각.

    왜 피크에 출발시키면 안 되나 (2026-07-27 실측):
        피크에는 정지 비율이 88%까지 올라가 도로가 사실상 멈춘다. 그 상태에서는
        타겟 차량이 **출발 엣지에 삽입조차 되지 않는다** — 2,400스텝(20분)을 기다려도
        자리가 안 나서 시뮬이 중단됐다. "정체를 뚫고 주행"이 아니라 "출발을 못 함"이다.

        같은 실행의 곡선을 보면 07:45에 이미 221대가 굴러가고 정지 57%다.
        이 정도면 타겟이 진짜 막힘을 겪으면서도 도로에 올라탈 수 있다.

    규칙: 피크 이전 구간에서 **정지 비율이 `max_halting` 이하인 가장 늦은 시각**.
        (늦을수록 혼잡하므로 "가장 늦은"이 곧 "가장 빡빡하되 주행 가능한" 지점이다.)
        조건에 맞는 표본이 없으면 예열 종료 시각으로 되돌아간다.

    min_running_frac : 피크 대비 이 비율보다 주행 대수가 적으면 "아직 한산"으로 보고 제외.
        너무 이른 시각(도로가 텅 빈 때)이 뽑히는 것을 막는다.
    """
    fallback = res.begin_s + res.warmup_s
    if not res.times_s or not res.peak_running:
        return fallback

    floor = res.peak_running * min_running_frac
    best = None
    for t, run, halt in zip(res.times_s, res.running, res.halting):
        if t > res.peak_time_s or t < fallback or run <= 0:
            continue
        if run < floor:
            continue
        if halt / run <= max_halting:
            best = t
    return best if best is not None else fallback


def congestion_summary(res: SimResult, top_n: int = 10) -> str:
    """정체가 생겼다 풀리는지 + 어디에 몰리는지 한눈에."""
    lines = [
        f"  시뮬 {res.begin_s / 3600:.2f}h~{res.end_s / 3600:.2f}h "
        f"(예열 {res.warmup_s / 60:.0f}분, 집계 {res.interval_s / 60:.0f}분)",
        f"  피크 {res.peak_time_s / 3600:.2f}h | 동시주행 {res.peak_running}대 "
        f"| 정지 비율 {res.congestion_ratio * 100:.1f}%",
        "  시간 곡선 (동시주행 / 정지):",
    ]
    peak = max(res.running) or 1
    for t, r, h in zip(res.times_s, res.running, res.halting):
        mark = " ←피크" if t == res.peak_time_s else ("  (예열)" if t < res.begin_s + res.warmup_s else "")
        lines.append(f"    {t / 3600:5.2f}h  {r:5d} / {h:4d}  "
                     f"{'#' * int(round(r / peak * 30))}{mark}")
    if res.peak_edges:
        lines.append(f"  피크 구간 상위 {top_n} 엣지 (평균 동시 대수):")
        for ld in res.peak_edges[:top_n]:
            lines.append(f"    {ld.edge_id:20s} {ld.vehicle_count:6.2f}대 "
                         f"| {ld.mean_speed_kph:5.1f}km/h | 점유 {ld.occupancy * 100:4.1f}%")
    return "\n".join(lines)

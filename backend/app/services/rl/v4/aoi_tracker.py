"""
Age of Information (AoI) tracker for V2X communications.

AoI(t) = t − t_last_received(t)  [simulation ms]

The AoI increases linearly between successful packet receptions and drops
on each successful reception — the characteristic "sawtooth" waveform.

Simulation time convention:
  Each environment step represents SIM_STEP_MS (100 ms) of simulated time.
  Call tick() once per env.step() to advance the sim clock.
  This avoids BUG-1: wall-clock time is hardware-dependent and collapses
  within a single Python process (monotonic() advances ~microseconds per
  step, not the 100ms the agent is supposed to experience).

Peak AoI threshold: 100 ms (ETSI EN 302 637-2 §6.1.2.3 — CAM max interval)
Average AoI target: ≤ 50 ms (3GPP TR 22.886 §6.3 safety service)

References:
  [1] Kaul, S., Yates, R., Gruteser, M., "Real-Time Status: How Often Should
      One Update?", IEEE INFOCOM 2012.
  [2] Abd-Elmagid, M. et al., "On the Role of Age of Information in the
      Internet of Things", IEEE Communications Magazine, 2019.
  [3] ETSI EN 302 637-2 V1.4.1 (2019-04), §6.1.2.3.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

AOI_THRESHOLD_MS: float = 100.0   # ETSI CAM max update interval
AOI_AVG_TARGET_MS: float = 50.0   # 3GPP TR 22.886 safety service target
SIM_STEP_MS: float = 100.0        # simulated ms per env step


@dataclass
class _LinkState:
    last_update_step: int = 0
    n_updates: int = 0
    peak_aoi_ms: float = 0.0


class AoITracker:
    """
    Per-link AoI tracker using sim-step time (not wall-clock).

    Call tick() once per env.step() to advance the internal sim clock.
    Call update(key) on successful packet reception for a link.
    Call aoi_normalized(key) to get AoI ∈ [0, 1] for the reward.

    Usage inside the RL env:
        tracker = AoITracker()
        tracker.reset()
        ...
        tracker.tick()                    # called in env.step()
        tracker.update("BS-01")          # on successful packet reception
        aoi_n = tracker.aoi_normalized("BS-01")   # ∈ [0, 1]
    """

    def __init__(self, threshold_ms: float = AOI_THRESHOLD_MS) -> None:
        self.threshold_ms = threshold_ms
        self._sim_step: int = 0
        self._links: dict[str, _LinkState] = {}

    # ── lifecycle ──────────────────────────────────────────────────────────────
    def reset(self) -> None:
        """Call at the start of each episode."""
        self._sim_step = 0
        self._links.clear()

    def tick(self) -> None:
        """Advance simulation time by one step (SIM_STEP_MS ms). Call once per env.step()."""
        self._sim_step += 1

    def _state(self, key: str) -> _LinkState:
        if key not in self._links:
            self._links[key] = _LinkState(last_update_step=self._sim_step)
        return self._links[key]

    def _aoi_ms(self, key: str) -> float:
        """Current AoI in simulated ms."""
        s = self._state(key)
        return (self._sim_step - s.last_update_step) * SIM_STEP_MS

    # ── update / query ─────────────────────────────────────────────────────────
    def update(self, key: str) -> float:
        """
        Record a successful packet reception for `key`.

        Returns the inter-reception interval (IRI) in simulated ms, which
        equals the AoI at the moment just before this reception.
        """
        s = self._state(key)
        iri_ms = (self._sim_step - s.last_update_step) * SIM_STEP_MS
        s.peak_aoi_ms = max(s.peak_aoi_ms, iri_ms)
        s.last_update_step = self._sim_step
        s.n_updates += 1
        return iri_ms

    def sample_aoi(self, key: str) -> float:
        """Current AoI in simulated ms (instantaneous)."""
        return self._aoi_ms(key)

    def aoi_normalized(self, key: str) -> float:
        """
        Normalized AoI ∈ [0, 1].

        0 = AoI at or below threshold (fresh data).
        1 = AoI ≥ 2× threshold (stale; linear between).

        The env multiplies this by _W_AOI directly — no internal scaling here.
        Fixes BUG-5: previous reward_penalty() applied 0.5 internally, so the
        effective weight was _W_AOI × 0.5 = 0.05, not the intended 0.1.
        """
        aoi = self._aoi_ms(key)
        if aoi <= self.threshold_ms:
            return 0.0
        return min((aoi - self.threshold_ms) / self.threshold_ms, 1.0)

    def reward_penalty(self, key: str) -> float:
        """Backward-compatible wrapper. Returns −aoi_normalized(key) ∈ [−1, 0]."""
        return -self.aoi_normalized(key)

    # ── statistics ─────────────────────────────────────────────────────────────
    def episode_stats(self, key: Optional[str] = None) -> dict:
        """
        Per-link or aggregate statistics.

        Returns dict with keys: avg_aoi_ms, peak_aoi_ms, n_updates,
                                current_aoi_ms, sim_step.
        """
        if key is not None:
            s = self._links.get(key)
            if s is None:
                return {"sim_step": self._sim_step, "n_updates": 0}
            aoi = self._aoi_ms(key)
            return {
                "avg_aoi_ms": round(s.peak_aoi_ms / max(s.n_updates, 1), 2),
                "peak_aoi_ms": round(s.peak_aoi_ms, 2),
                "n_updates": s.n_updates,
                "current_aoi_ms": round(aoi, 2),
                "aoi_normalized": round(self.aoi_normalized(key), 4),
                "sim_step": self._sim_step,
                "pct_above_threshold": 100.0 if aoi > self.threshold_ms else 0.0,
            }

        if not self._links:
            return {"n_links": 0, "sim_step": self._sim_step}

        aois = [self._aoi_ms(k) for k in self._links]
        above = sum(1 for a in aois if a > self.threshold_ms)
        peaks = [s.peak_aoi_ms for s in self._links.values()]
        return {
            "n_links": len(self._links),
            "avg_aoi_ms": round(sum(aois) / len(aois), 2),
            "peak_aoi_ms": round(max(peaks), 2),
            "pct_above_threshold": round(above / len(aois) * 100.0, 1),
            "sim_step": self._sim_step,
        }

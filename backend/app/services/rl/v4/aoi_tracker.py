"""
Age of Information (AoI) tracker for V2X communications.

AoI(t) = t − t_last_received(t)  [ms]

The AoI increases linearly between successful packet receptions and drops
to zero (or to the one-way delay) on each successful reception — forming
the characteristic "sawtooth" waveform.

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

import time
from dataclasses import dataclass, field
from typing import Optional

AOI_THRESHOLD_MS: float = 100.0   # ETSI CAM max update interval
AOI_AVG_TARGET_MS: float = 50.0   # 3GPP TR 22.886 safety service target
AOI_PENALTY_SCALE: float = 0.5    # reward penalty weight


@dataclass
class _LinkState:
    last_update_ms: float = 0.0
    n_updates: int = 0
    peak_aoi_ms: float = 0.0
    sum_aoi_ms: float = 0.0
    n_samples: int = 0


class AoITracker:
    """
    Per-link AoI tracker.  A "link" is identified by an arbitrary string key
    (e.g. "veh_123:BS-01" or "BS-01" when tracking a single vehicle).

    Usage inside the RL env reward:
        tracker = AoITracker()
        tracker.reset()
        ...
        tracker.update("BS-01")   # on successful packet reception
        penalty = tracker.reward_penalty("BS-01")  # in [−1, 0]
    """

    def __init__(self, threshold_ms: float = AOI_THRESHOLD_MS) -> None:
        self.threshold_ms = threshold_ms
        self._t0_ms: float = 0.0
        self._links: dict[str, _LinkState] = {}

    # ── lifecycle ──────────────────────────────────────────────────────────────
    def reset(self) -> None:
        """Call at the start of each episode."""
        self._t0_ms = self._now()
        self._links.clear()

    def _now(self) -> float:
        return time.monotonic() * 1000.0

    def _state(self, key: str) -> _LinkState:
        if key not in self._links:
            self._links[key] = _LinkState(last_update_ms=self._t0_ms)
        return self._links[key]

    # ── update / query ─────────────────────────────────────────────────────────
    def update(self, key: str) -> float:
        """
        Record a successful packet reception for `key`.

        Returns the inter-reception interval (IRI) in ms, which equals
        the peak AoI at the moment just before this reception.
        """
        now = self._now()
        s = self._state(key)
        iri = now - s.last_update_ms
        s.peak_aoi_ms = max(s.peak_aoi_ms, iri)
        s.last_update_ms = now
        s.n_updates += 1
        return iri

    def sample_aoi(self, key: str) -> float:
        """Current AoI in ms (instantaneous, not peak)."""
        return self._now() - self._state(key).last_update_ms

    def reward_penalty(self, key: str) -> float:
        """
        Reward term ∈ [−AOI_PENALTY_SCALE, 0].

        Zero when AoI ≤ threshold; linearly negative when AoI > threshold,
        capped at −AOI_PENALTY_SCALE when AoI ≥ 2 × threshold.
        """
        aoi = self.sample_aoi(key)
        if aoi <= self.threshold_ms:
            return 0.0
        excess_ratio = min((aoi - self.threshold_ms) / self.threshold_ms, 1.0)
        return -excess_ratio * AOI_PENALTY_SCALE

    # ── statistics ─────────────────────────────────────────────────────────────
    def episode_stats(self, key: Optional[str] = None) -> dict:
        """
        Per-link or aggregate statistics.

        Returns
        -------
        dict with keys:
          avg_aoi_ms, peak_aoi_ms, n_updates, pct_above_threshold
        """
        if key is not None:
            s = self._links.get(key)
            if s is None:
                return {}
            aoi = self.sample_aoi(key)
            return {
                "avg_aoi_ms": round(s.peak_aoi_ms / max(s.n_updates, 1), 2),
                "peak_aoi_ms": round(s.peak_aoi_ms, 2),
                "n_updates": s.n_updates,
                "current_aoi_ms": round(aoi, 2),
                "pct_above_threshold": 100.0 if aoi > self.threshold_ms else 0.0,
            }

        if not self._links:
            return {"n_links": 0}

        aois = [self._now() - s.last_update_ms for s in self._links.values()]
        above = sum(1 for a in aois if a > self.threshold_ms)
        peaks = [s.peak_aoi_ms for s in self._links.values()]
        return {
            "n_links": len(self._links),
            "avg_aoi_ms": round(sum(aois) / len(aois), 2),
            "peak_aoi_ms": round(max(peaks), 2),
            "pct_above_threshold": round(above / len(aois) * 100.0, 1),
        }

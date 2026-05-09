from __future__ import annotations

from app.core.config import settings
from app.simulation.mock_mobility_simulator import MockMobilitySimulator


class MockSumoAdapter:
    def __init__(self) -> None:
        self.simulator = MockMobilitySimulator()

    def reset(self) -> None:
        self.simulator.reset()

    def step(self, message: str) -> dict[str, object]:
        return self._with_metadata(self.simulator.step(), message)

    def snapshot(self, message: str) -> dict[str, object]:
        return self._with_metadata(self.simulator.snapshot(), message)

    def _with_metadata(self, snapshot: dict[str, object], message: str) -> dict[str, object]:
        snapshot["mode"] = "mock_sumo_fallback"
        snapshot["adapter"] = "mock_fallback"
        snapshot["simulation_label"] = "Mock Simulation Mode"
        snapshot["sumo_enabled"] = settings.sumo_enabled
        snapshot["sumo_gui_enabled"] = settings.sumo_gui_enabled
        snapshot["message"] = message
        return snapshot

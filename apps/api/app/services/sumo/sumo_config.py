from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class SumoRuntimeConfig:
    enabled: bool
    binary: str
    config_path: str
    host: str
    port: int
    gui_enabled: bool

    @classmethod
    def from_settings(cls) -> "SumoRuntimeConfig":
        binary = settings.sumo_binary
        if settings.sumo_gui_enabled and binary == "sumo":
            binary = "sumo-gui"

        return cls(
            enabled=settings.sumo_enabled,
            binary=binary,
            config_path=settings.sumo_config_path,
            host=settings.sumo_host,
            port=settings.sumo_port,
            gui_enabled=settings.sumo_gui_enabled,
        )

    def disabled_reason(self) -> str | None:
        if not self.enabled:
            return "SUMO_ENABLED=false; using mock simulation mode."
        if not self.config_path:
            return "SUMO_CONFIG_PATH is empty; using mock simulation mode."
        return None

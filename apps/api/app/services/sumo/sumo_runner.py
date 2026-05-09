from __future__ import annotations

import shutil
import subprocess

from app.services.sumo.sumo_config import SumoRuntimeConfig


class SumoUnavailableError(RuntimeError):
    pass


class SumoProcessRunner:
    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None

    def start(self, config: SumoRuntimeConfig) -> None:
        disabled_reason = config.disabled_reason()
        if disabled_reason:
            raise SumoUnavailableError(disabled_reason)

        if not shutil.which(config.binary):
            raise SumoUnavailableError(f"SUMO binary '{config.binary}' was not found; using mock simulation mode.")

        self.stop()
        self._process = subprocess.Popen(
            [
                config.binary,
                "-c",
                config.config_path,
                "--remote-port",
                str(config.port),
                "--start",
                "--quit-on-end",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
        self._process = None

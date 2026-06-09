from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ITSCache:
    records: list = field(default_factory=list)
    matches: list = field(default_factory=list)
    enriched_links: list[dict] = field(default_factory=list)
    last_sync_time: str | None = None
    last_bbox: dict | None = None
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def update(self, *, records: list, matches: list, enriched_links: list[dict], bbox: dict, warnings: list[str], stats: dict) -> None:
        self.records = records
        self.matches = matches
        self.enriched_links = enriched_links
        self.last_bbox = bbox
        self.warnings = warnings
        self.stats = stats
        self.last_sync_time = datetime.utcnow().isoformat() + "Z"


ITS_CACHE = ITSCache()


"""Which proximity adverts are yours, and the current reading per model.

AirPods advertise from a resolvable private address that changes about
every 15 minutes, and the advert carries no identity a computer outside
the owner's iCloud account can check (the identifying part is encrypted).
So the plugin decides with what it can know:

1. An advert BlueZ resolved to a device **paired with this computer** is
   yours, at any distance.
2. Otherwise the advert's model must be one of **your models**: the
   models of the Apple audio devices paired with this computer (product id
   from their ``Modalias``) plus the model you confirmed in the settings.
3. And it must be **nearby**: at least as strong as the threshold
   (default -70 dBm, roughly the same room or desk).
4. Of several matching adverts of one model the strongest of the last
   20 seconds wins (a resolved one always wins).

Everything else is dropped on arrival: never stored, logged or shown.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from blueferry_airpods.bluez import Sighting
from blueferry_airpods.proximity import MODELS, Proximity, choice_models, decode

#: Strongest advert of this window wins.
WINDOW = 20.0
#: A reading older than this shows "n min ago".
FRESH = 120.0
#: A reading older than this is dropped ("not nearby").
STALE = 30 * 60.0


@dataclass(frozen=True, slots=True)
class Reading:
    proximity: Proximity
    rssi: int | None
    seen_at: float
    resolved: bool
    path: str

    def summary(self) -> tuple:
        """What the user sees (no RSSI, no address)."""
        p = self.proximity
        return (p.model_id, p.left, p.right, p.case, p.lid_open, self.resolved)


def wanted_models(paired: frozenset[int], choice: str) -> frozenset[int]:
    """Your models: paired AirPods plus the confirmed choice."""
    return frozenset(m for m in paired if m in MODELS) | choice_models(choice)


class Tracker:
    """Thread-safe: fed on the main loop, read from worker threads."""

    def __init__(self, nearby: int, wanted: frozenset[int] = frozenset()) -> None:
        self._lock = threading.Lock()
        self.nearby = nearby
        self.wanted = wanted
        # model id -> path -> reading (only accepted adverts)
        self._candidates: dict[int, dict[str, Reading]] = {}

    def configure(self, *, nearby: int, wanted: frozenset[int]) -> None:
        with self._lock:
            self.nearby, self.wanted = nearby, wanted
            for model in list(self._candidates):
                if model not in wanted:
                    self._candidates[model] = {
                        path: reading for path, reading in self._candidates[model].items()
                        if reading.resolved
                    }
                    if not self._candidates[model]:
                        del self._candidates[model]

    def accepts(self, proximity: Proximity, sighting: Sighting) -> bool:
        if sighting.paired:
            return True
        if proximity.model_id not in self.wanted:
            return False
        return sighting.rssi is not None and sighting.rssi >= self.nearby

    def feed(self, sighting: Sighting) -> bool:
        """Take an advert; True when it was accepted as yours."""
        proximity = decode(sighting.data)
        if proximity is None or proximity.model is None:
            return False
        with self._lock:
            if not self.accepts(proximity, sighting):
                return False
            per_model = self._candidates.setdefault(proximity.model_id, {})
            per_model[sighting.path] = Reading(
                proximity, sighting.rssi, sighting.seen_at, sighting.paired, sighting.path,
            )
            self._prune_locked(sighting.seen_at)
            return True

    def expire(self, now: float) -> None:
        """Drop stale readings."""
        with self._lock:
            self._prune_locked(now)

    def readings(self, now: float) -> dict[int, Reading]:
        with self._lock:
            return self._best_locked(now)

    def summary(self, now: float) -> tuple:
        with self._lock:
            return self._summary_locked(now)

    # ---- internals (lock held) -----------------------------------------------------

    def _prune_locked(self, now: float) -> None:
        for model in list(self._candidates):
            per_model = self._candidates[model]
            for path in [p for p, r in per_model.items() if now - r.seen_at > STALE]:
                del per_model[path]
            if not per_model:
                del self._candidates[model]

    def _best_locked(self, now: float) -> dict[int, Reading]:
        best: dict[int, Reading] = {}
        for model, per_model in self._candidates.items():
            live = [r for r in per_model.values() if now - r.seen_at <= STALE]
            if not live:
                continue
            recent = [r for r in live if now - r.seen_at <= WINDOW] or live
            best[model] = max(recent, key=_rank)
        return best

    def _summary_locked(self, now: float) -> tuple:
        """What the card shows, to tell whether it changed (fresh or "n min ago")."""
        return tuple(
            (reading.summary(), now - reading.seen_at <= FRESH)
            for _model, reading in sorted(self._best_locked(now).items())
        )


def _rank(reading: Reading) -> tuple[Any, ...]:
    rssi = reading.rssi if reading.rssi is not None else -200
    return (reading.resolved, rssi, reading.seen_at)

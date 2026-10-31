"""The plugin process: AirPods battery as a card, and on a small D-Bus
interface for other desktop tools.

Threads: D-Bus calls from BlueZ, the timer and the accessories interface
run on the GLib main loop; ``GetCardItems`` and the settings hooks run on
the SDK's worker threads and only read the tracker (under its lock). No
call on the main loop blocks: every BlueZ call is asynchronous.

Nothing about adverts that are not yours is kept or logged, and no
Bluetooth address ever leaves the process (not even in the log).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import dbus
import dbus.service
from blueferry.plugin_api.config import ConfigError
from blueferry.plugin_api.manifest import PluginManifest
from blueferry.plugin_api.service import CardService, PluginCallError
from blueferry.plugin_api.surfaces import CardItem

from blueferry_airpods.bluez import BluezWatcher, Sighting
from blueferry_airpods.i18n import t
from blueferry_airpods.matching import FRESH, Reading, Tracker, wanted_models
from blueferry_airpods.proximity import MODELS, Battery, Pod, model_name
from blueferry_airpods.settings import (
    NEARBY_MAX,
    NEARBY_MIN,
    PAIRED,
    Settings,
    SettingsError,
    SettingsStore,
    blueferry_disabled,
)

log = logging.getLogger(__name__)

#: The interface other tools (e.g. the DMS widget "Device Batteries") read.
ACCESSORIES_INTERFACE = "io.github.joshii_h.BlueFerryAccessories1"
ACCESSORIES_VERSION = 1
TICK_SECONDS = 30

Every = Callable[[int, Callable[[], bool]], object]
WatcherFactory = Callable[..., Any]


def _every(seconds: int, callback: Callable[[], bool]) -> object:
    from gi.repository import GLib

    return GLib.timeout_add_seconds(seconds, callback)


def _system_watcher(on_sighting, on_paired, on_state) -> BluezWatcher:
    return BluezWatcher(dbus.SystemBus(), on_sighting, on_paired, on_state)


def model_groups(models: set[int] | frozenset[int]) -> list[list[int]]:
    """Models grouped by their settings choice (Pro 2 Lightning and USB-C are
    one entry until an advert tells which), in table order."""
    groups: dict[str, list[int]] = {}
    for model in sorted(models, key=lambda m: (list(MODELS).index(m) if m in MODELS else 999, m)):
        key = MODELS[model].choice if model in MODELS else f"{model:04x}"
        groups.setdefault(key, []).append(model)
    return list(groups.values())


def percent_text(battery: Battery | None) -> str:
    if battery is None or battery.percent is None:
        return "–"
    return f"{battery.percent} %"


def battery_json(battery: Battery | None) -> dict[str, object] | None:
    if battery is None or battery.percent is None:
        return None
    data: dict[str, object] = {"percent": battery.percent, "charging": battery.charging}
    if isinstance(battery, Pod):
        data["in_ear"] = battery.in_ear
    return data


class AirPodsService(CardService):
    """Capability ``card`` plus ``io.github.joshii_h.BlueFerryAccessories1``."""

    def __init__(
        self,
        manifest: PluginManifest,
        bus: Any = None,
        *,
        settings: SettingsStore | None = None,
        watcher_factory: WatcherFactory = _system_watcher,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        every: Every | None = _every,
        disabled: Callable[[], bool] = blueferry_disabled,
        **kwargs: Any,
    ) -> None:
        super().__init__(manifest, bus, **kwargs)
        self._settings = settings or SettingsStore()
        self._watcher_factory = watcher_factory
        self._monotonic = monotonic
        self._now = now
        self._every = every
        self._disabled = disabled
        self._lock = threading.Lock()          # guards the fields below
        self._paired: frozenset[int] = frozenset()
        self._paired_known = False
        self._state = "starting"
        self._settings_error = ""
        current = self._load_settings()
        self._choice = current.model
        self.tracker = Tracker(current.nearby, wanted_models(frozenset(), current.model))
        self._watcher: Any = None
        self._shown: tuple | None = None

    # ---- lifecycle (main loop) ---------------------------------------------------

    def start(self) -> AirPodsService:
        if self._every is not None:
            self._every(TICK_SECONDS, self.tick)
        self.tick()
        return self

    def tick(self) -> bool:
        """Every 30 s: follow BlueFerry's kill switch, expire, stay alive while watching."""
        if self._disabled():
            if self._watcher is not None:
                self._watcher.stop()
                self._watcher = None
            self._set_state("disabled")
        elif self._watcher is None:
            self._watcher = self._watcher_factory(
                self._sighting, self._paired_changed, self._set_state,
            )
            self._watcher.start()
        if self._watcher is not None and self._watching():
            # Adverts only arrive while the process runs: do not idle out.
            self.last_activity = self._clock()
        self.tracker.expire(self._monotonic())
        self._maybe_changed()
        return True

    def _watching(self) -> bool:
        with self._lock:
            return bool(self.tracker.wanted) or not self._paired_known

    def stop(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None

    # ---- BlueZ callbacks (main loop) -----------------------------------------------

    def _sighting(self, sighting: Sighting) -> None:
        if self.tracker.feed(sighting):
            self._maybe_changed()

    def _paired_changed(self, models: frozenset[int]) -> None:
        with self._lock:
            self._paired = models
            self._paired_known = True
            choice = self._choice
        self.tracker.configure(nearby=self.tracker.nearby, wanted=wanted_models(models, choice))
        self._maybe_changed(force=True)

    def _set_state(self, state: str) -> None:
        with self._lock:
            if state == self._state:
                return
            self._state = state
        log.info("bluetooth state: %s", state)
        self._maybe_changed(force=True)

    def _maybe_changed(self, *, force: bool = False) -> None:
        """Emit CardChanged and Changed when what the user sees changed."""
        with self._lock:
            shown = (self._state, self.tracker.wanted, self.tracker.summary(self._monotonic()))
        if shown == self._shown and not force:
            return
        self._shown = shown
        self.emit_card_changed()
        self._to_main(self.Changed)

    # ---- card (worker thread) ---------------------------------------------------------

    def card_items(self) -> list[CardItem]:
        with self._lock:
            state, error, wanted = self._state, self._settings_error, self.tracker.wanted
        if error:
            return [CardItem("error", t("settings_unreadable"), "audio-headphones", error)]
        if state == "disabled":
            return [CardItem("state", t("disabled"), "audio-headphones")]
        if state in ("no-adapter", "powered-off", "unavailable"):
            return [CardItem("state", t(state.replace("-", "_")), "bluetooth-disabled")]
        now = self._monotonic()
        readings = self.tracker.readings(now)
        groups = model_groups(set(wanted) | set(readings))
        if not groups:
            return [CardItem("setup", t("setup_title"), "audio-headphones", t("setup_hint"))]
        items = []
        for group in groups:
            found = [readings[model] for model in group if model in readings]
            if not found:
                subtitle = t("not_nearby") if state != "starting" else t("waiting")
                items.append(CardItem(f"m{group[0]:04x}", model_name(group[0]),
                                      "audio-headphones", subtitle))
            for reading in found:
                model = reading.proximity.model_id
                items.append(CardItem(f"m{model:04x}", model_name(model), "audio-headphones",
                                      self._subtitle(reading, now)))
        items = items[:8]
        if state == "listening" and len(items) < 8:
            items.append(CardItem("listening", t("listening_title"), "dialog-information",
                                  t("listening_hint")))
        return items

    def _subtitle(self, reading: Reading, now: float) -> str:
        p = reading.proximity
        parts = []
        if p.model is not None and p.model.single:
            parts.append(f"{t('battery')} {self._level(p.left or p.right)}")
        else:
            parts.append(f"{t('left')} {self._level(p.left)}")
            parts.append(f"{t('right')} {self._level(p.right)}")
            if p.case is not None and p.case.percent is not None:
                parts.append(f"{t('case')} {self._level(p.case)}")
        if p.lid_open:
            parts.append(t("lid_open"))
        age = now - reading.seen_at
        if age > FRESH:
            parts.append(t("minutes_ago", minutes=int(age // 60)))
        return " · ".join(parts)

    @staticmethod
    def _level(battery: Battery | None) -> str:
        text = percent_text(battery)
        extra = []
        if battery is not None and battery.charging:
            extra.append(t("charging"))
        if isinstance(battery, Pod) and battery.in_ear:
            extra.append(t("in_ear"))
        return text + (f" ({', '.join(extra)})" if extra else "")

    # ---- accessories JSON ----------------------------------------------------------------

    def batteries(self) -> dict[str, object]:
        with self._lock:
            state = self._state
        now_mono, now = self._monotonic(), self._now()
        devices = []
        for model, reading in sorted(self.tracker.readings(now_mono).items()):
            p = reading.proximity
            single = p.model is not None and p.model.single
            age = max(0, int(now_mono - reading.seen_at))
            entry: dict[str, object] = {
                "id": f"airpods-{model:04x}",
                "model_id": f"0x{model:04x}",
                "name": model_name(model),
                "kind": "headphones" if single else "earbuds",
                "battery": battery_json(p.left) if single else None,
                "left": None if single else battery_json(p.left),
                "right": None if single else battery_json(p.right),
                "case": battery_json(p.case),
                "lid_open": p.lid_open,
                "matched_by": "paired" if reading.resolved else "model",
                "age_seconds": age,
                "seen_at": datetime.fromtimestamp(now.timestamp() - age, timezone.utc)
                .isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
            devices.append(entry)
        return {"version": ACCESSORIES_VERSION, "state": state, "devices": devices}

    # ---- status and settings (worker thread) ------------------------------------------

    def status(self) -> dict[str, object]:
        with self._lock:
            state, error, wanted = self._state, self._settings_error, self.tracker.wanted
        if error:
            return {"state": "error", "detail": error}
        if state == "disabled":
            return {"state": "unconfigured", "detail": t("disabled")}
        if state in ("no-adapter", "powered-off", "unavailable"):
            return {"state": "error", "detail": t(state.replace("-", "_"))}
        if state == "starting":
            return {"state": "busy", "detail": t("starting")}
        if not wanted:
            return {"state": "unconfigured", "detail": t("status_unconfigured")}
        if state == "listening":
            return {"state": "ok", "detail": t("status_listening")}
        names = ", ".join(model_name(group[0]) for group in model_groups(wanted))
        return {"state": "ok", "detail": t("status_monitoring", models=names)}

    def config_values(self) -> dict[str, object]:
        try:
            settings = self._settings.load()
        except SettingsError as error:
            raise PluginCallError(str(error)) from None
        return {"model": settings.model, "nearby": settings.nearby}

    def apply_config(self, values: dict[str, object]) -> None:
        model = values.get("model", PAIRED)
        nearby = values.get("nearby", self.tracker.nearby)
        if not isinstance(nearby, int) or isinstance(nearby, bool) or not (
            NEARBY_MIN <= nearby <= NEARBY_MAX
        ):
            raise ConfigError("nearby", t("nearby_range", low=NEARBY_MIN, high=NEARBY_MAX))
        try:
            settings = Settings(model=str(model), nearby=nearby)
            self._settings.save(settings)
        except (SettingsError, OSError) as error:
            raise ConfigError("", t("store_failed", error=error)) from None
        self._to_main(lambda: self._apply(settings))

    def _apply(self, settings: Settings) -> None:
        with self._lock:
            self._choice = settings.model
            self._settings_error = ""
            paired = self._paired
        self.tracker.configure(nearby=settings.nearby,
                               wanted=wanted_models(paired, settings.model))
        self.last_activity = self._clock()
        self._maybe_changed(force=True)

    def _load_settings(self) -> Settings:
        try:
            return self._settings.load()
        except SettingsError as error:
            self._settings_error = str(error)
            return Settings()

    # ---- D-Bus: io.github.joshii_h.BlueFerryAccessories1 -------------------------------

    @dbus.service.method(
        ACCESSORIES_INTERFACE, in_signature="", out_signature="s", sender_keyword="sender",
    )
    def GetBatteries(self, sender=None) -> str:
        """JSON, see the README; memory only, answers at once."""
        self.admit(sender)
        return json.dumps(self.batteries())

    @dbus.service.signal(ACCESSORIES_INTERFACE, signature="")
    def Changed(self) -> None:
        """Content-free: call GetBatteries again."""


__all__ = ["ACCESSORIES_INTERFACE", "MODELS", "AirPodsService"]

"""Passive reading of Apple proximity adverts through BlueZ (system bus).

BlueZ scans passively for as long as an ``org.bluez.AdvertisementMonitor1``
with an ``or_patterns`` filter is registered (BlueZ 5.56+; some versions
export the manager only with bluetoothd's experimental features). The
filter here matches only Apple manufacturer data that starts with the
Proximity Pairing type: AD type 0xFF, bytes ``4C 00 07``. The plugin
never starts discovery and never connects to anything; it reads
``ManufacturerData`` and ``RSSI`` of the devices BlueZ reports.

BlueZ keeps a registered monitor across adapter power cycles, so the
watcher registers once per BlueZ instance; when BlueZ releases the monitor
it registers again after a growing pause.

Without the monitor manager (older BlueZ, adapter without support) the
watcher still listens to ``PropertiesChanged``; it then sees adverts only
while another program runs a discovery.

Every D-Bus call here is asynchronous: replies come back on the GLib main
loop, nothing blocks it. Adding the signal match rules is non-blocking in
dbus-python as well.
"""
from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import dbus
import dbus.service

from blueferry_airpods.proximity import APPLE_COMPANY_ID, PROXIMITY_TYPE

log = logging.getLogger(__name__)

BLUEZ = "org.bluez"
DEVICE = "org.bluez.Device1"
ADAPTER = "org.bluez.Adapter1"
MONITOR_MANAGER = "org.bluez.AdvertisementMonitorManager1"
MONITOR = "org.bluez.AdvertisementMonitor1"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
AD_TYPE_MANUFACTURER = 0xFF
#: Apple's company id little-endian, then the Proximity Pairing type.
PATTERN = bytes([APPLE_COMPANY_ID & 0xFF, APPLE_COMPANY_ID >> 8, PROXIMITY_TYPE])
#: Devices remembered at once; strangers come and go, the cache stays small.
MAX_DEVICES = 128
MODALIAS = re.compile(r"^bluetooth:v004Cp([0-9A-Fa-f]{4})", re.ASCII)
#: BlueZ's answer to registering an application it still holds.
ALREADY_EXISTS = "org.bluez.Error.AlreadyExists"
#: Pause before registering again after a release or failure: doubles from
#: the first value up to the second (seconds).
RETRY_FIRST, RETRY_MAX = 5, 300

Timer = Callable[[int, Callable[[], bool]], object]


def _timeout(seconds: int, callback: Callable[[], bool]) -> object:
    from gi.repository import GLib

    return GLib.timeout_add_seconds(seconds, callback)


@dataclass(frozen=True, slots=True)
class Sighting:
    """One proximity advert as BlueZ reported it.

    ``path`` is BlueZ's object path (it contains the advertising address,
    which rotates; it never leaves the plugin). ``paired`` is true when
    BlueZ resolved the advert to a device paired with this computer.
    """

    path: str
    data: bytes
    rssi: int | None
    paired: bool
    seen_at: float


def apple_payload(manufacturer_data: Any) -> bytes | None:
    """Apple's manufacturer data from a ``ManufacturerData`` dict, if it is
    a Proximity Pairing message."""
    try:
        value = manufacturer_data.get(APPLE_COMPANY_ID)
        if value is None:
            value = manufacturer_data.get(dbus.UInt16(APPLE_COMPANY_ID))
    except AttributeError:
        return None
    if value is None:
        return None
    try:
        data = bytes(int(b) & 0xFF for b in value)
    except (TypeError, ValueError):
        return None
    return data if data[:1] == bytes([PROXIMITY_TYPE]) else None


def paired_model(properties: Any) -> int | None:
    """The Apple product id of a paired device (from ``Modalias``), else None."""
    if not bool(properties.get("Paired", False)):
        return None
    match = MODALIAS.match(str(properties.get("Modalias", "")))
    return int(match.group(1), 16) if match else None


def _rssi(value: Any) -> int | None:
    try:
        rssi = int(value)
    except (TypeError, ValueError):
        return None
    return rssi if -127 <= rssi <= 20 else None


class MonitorApplication(dbus.service.Object):
    """The ObjectManager root BlueZ reads the monitor from."""

    def __init__(self, monitor: Monitor) -> None:
        super().__init__()
        self.monitor = monitor

    @dbus.service.method(OBJECT_MANAGER, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self) -> dict:
        return {dbus.ObjectPath(self.monitor.path): {MONITOR: self.monitor.properties()}}


class Monitor(dbus.service.Object):
    """``org.bluez.AdvertisementMonitor1``: Apple Proximity Pairing only."""

    def __init__(self, path: str, watcher: BluezWatcher) -> None:
        super().__init__()
        self.path = path
        self.watcher = watcher

    def properties(self) -> dict:
        return {
            "Type": dbus.String("or_patterns"),
            "Patterns": dbus.Array([dbus.Struct((
                dbus.Byte(0), dbus.Byte(AD_TYPE_MANUFACTURER),
                dbus.Array([dbus.Byte(b) for b in PATTERN], signature="y"),
            ), signature="yyay")], signature="(yyay)"),
        }

    @dbus.service.method(PROPERTIES, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface: str) -> dict:
        return self.properties() if interface == MONITOR else {}

    @dbus.service.method(PROPERTIES, in_signature="ss", out_signature="v")
    def Get(self, interface: str, name: str) -> Any:
        properties = self.GetAll(interface)
        if name not in properties:
            raise dbus.exceptions.DBusException(
                "no such property", name="org.freedesktop.DBus.Error.InvalidArgs")
        return properties[name]

    @dbus.service.method(MONITOR)
    def Release(self) -> None:
        self.watcher.monitor_released()

    @dbus.service.method(MONITOR)
    def Activate(self) -> None:
        self.watcher.monitor_active()

    @dbus.service.method(MONITOR, in_signature="o")
    def DeviceFound(self, device: str) -> None:
        self.watcher.device_found(str(device))

    @dbus.service.method(MONITOR, in_signature="o")
    def DeviceLost(self, device: str) -> None:
        """Nothing to do: entries expire on their own."""


class BluezWatcher:
    """Feeds :class:`Sighting` objects and the paired models to callbacks.

    All callbacks run on the main loop. ``state`` is one of ``starting``,
    ``monitoring`` (passive scan through the monitor), ``listening`` (no
    monitor support: only while someone else scans), ``idle`` (scanning
    switched off with :meth:`set_scanning`: nothing to look for),
    ``no-adapter``, ``powered-off``, ``unavailable`` (BlueZ not on the bus)
    or ``stopped``.
    """

    def __init__(
        self,
        bus: Any,
        on_sighting: Callable[[Sighting], None],
        on_paired: Callable[[frozenset[int]], None],
        on_state: Callable[[str], None],
        *,
        app_path: str = "/io/weirdware/blueferry/airpods",
        clock: Callable[[], float] = time.monotonic,
        timer: Timer = _timeout,
    ) -> None:
        self.bus = bus
        self._on_sighting = on_sighting
        self._on_paired = on_paired
        self._on_state = on_state
        self._clock = clock
        self._timer = timer
        self.app_path = app_path
        self.state = "stopped"
        self._adapter: str | None = None
        self._registered = False
        self._registered_adapter: str | None = None
        self._registering = False
        self._released = False      # BlueZ may still hold the app: unregister first
        self._wanted = False
        self._scan = True
        self._retries = 0
        self._retry_pending = False
        self._generation = 0        # outdated retry timers see a newer value
        self._paired: dict[str, int] = {}           # path -> product id
        self._paired_known = False
        self._powered: dict[str, bool] = {}         # adapter path -> Powered
        self._devices: OrderedDict[str, tuple[bytes | None, int | None]] = OrderedDict()
        self._matches: list[Any] = []
        self._app: MonitorApplication | None = None
        self._monitor: Monitor | None = None

    # ---- lifecycle (main loop) -------------------------------------------------

    def start(self) -> None:
        """Listen to BlueZ and register the monitor; non-blocking."""
        if self._matches:
            return
        self._wanted = True
        self._generation += 1
        self._set_state("starting")
        self._matches = [
            self.bus.add_signal_receiver(
                self._properties_changed, "PropertiesChanged", PROPERTIES, BLUEZ,
                path_keyword="path",
            ),
            self.bus.add_signal_receiver(
                self._interfaces_added, "InterfacesAdded", OBJECT_MANAGER, BLUEZ,
            ),
            self.bus.add_signal_receiver(
                self._interfaces_removed, "InterfacesRemoved", OBJECT_MANAGER, BLUEZ,
            ),
            self.bus.add_signal_receiver(
                self._owner_changed, "NameOwnerChanged", "org.freedesktop.DBus",
                "org.freedesktop.DBus", "/org/freedesktop/DBus", arg0=BLUEZ,
            ),
        ]
        self.refresh()

    def stop(self) -> None:
        """Unregister the monitor (asynchronously), take the objects off the
        bus and stop listening."""
        self._wanted = False
        self._generation += 1
        self._retry_pending = False
        for match in self._matches:
            try:
                match.remove()
            except Exception:  # already gone with the connection
                pass
        self._matches = []
        self._unregister()
        # dbus.SystemBus() is shared in the process: a later watcher exports
        # its objects at the same path, which fails while these are there.
        self._unexport()
        self._devices.clear()
        self._set_state("stopped")

    def set_scanning(self, scan: bool) -> None:
        """Scan only while there is something to look for. Paired devices
        are followed either way."""
        if scan == self._scan:
            return
        self._scan = scan
        if not self._wanted:
            return
        if scan:
            self.refresh()
            return
        self._unregister()
        self._devices.clear()
        if self.state in ("starting", "monitoring", "listening"):
            self._set_state("idle")

    def refresh(self) -> None:
        """Read adapters and paired devices again (asynchronously)."""
        self.bus.call_async(
            BLUEZ, "/", OBJECT_MANAGER, "GetManagedObjects", "", (),
            self._managed_objects, self._bluez_missing, timeout=10,
        )

    @property
    def paired_models(self) -> frozenset[int]:
        return frozenset(self._paired.values())

    # ---- BlueZ state --------------------------------------------------------------

    def _bluez_missing(self, error: Exception) -> None:
        log.info("BlueZ is not reachable: %s", type(error).__name__)
        self._registered = self._registering = False
        self._registered_adapter = None
        self._set_state("unavailable")

    def _managed_objects(self, objects: Any) -> None:
        paired: dict[str, int] = {}
        adapters: list[str] = []
        managers: list[str] = []
        for path, interfaces in objects.items():
            path = str(path)
            if ADAPTER in interfaces:
                adapters.append(path)
                self._powered[path] = bool(interfaces[ADAPTER].get("Powered", False))
                if MONITOR_MANAGER in interfaces:
                    managers.append(path)
            device = interfaces.get(DEVICE)
            if device is not None:
                model = paired_model(device)
                if model is not None:
                    paired[path] = model
        self._set_paired(paired)
        if not adapters:
            self._adapter = None
            self._set_state("no-adapter")
            return
        # A powered adapter first, then one with the monitor manager.
        chosen = min(adapters, key=lambda path: (
            not self._powered.get(path, False), path not in managers, path))
        if self._registered and self._registered_adapter != chosen:
            self._unregister()
        self._adapter = chosen
        if not self._powered.get(chosen, False):
            self._set_state("powered-off")
            return
        if not self._scan:
            self._set_state("idle")
            return
        if chosen in managers:
            self._register(chosen)
        else:
            self._set_state("listening")

    def _set_paired(self, paired: dict[str, int]) -> None:
        before = self.paired_models
        self._paired = paired
        if self.paired_models != before or not self._paired_known:
            self._paired_known = True
            self._on_paired(self.paired_models)

    def _owner_changed(self, _name: str, _old: str, new: str) -> None:
        # A new bluetoothd knows nothing of the old registration.
        self._registered = self._registering = self._released = False
        self._registered_adapter = None
        self._devices.clear()
        if not self._wanted:
            return
        if new:
            self.refresh()
        else:
            self._set_state("unavailable")

    # ---- monitor ------------------------------------------------------------------

    def _register(self, adapter: str) -> None:
        if self._registering:
            return
        if self._registered:
            self._set_state("monitoring")
            return
        if not self._export():
            self._set_state("listening")
            return
        self._registering = True

        def done() -> None:
            self._registering = False
            self._registered, self._registered_adapter = True, adapter
            self._retries = 0
            if not (self._wanted and self._scan):     # stopped while the call ran
                self._unregister()
                return
            self._set_state("monitoring")

        def failed(error: Exception) -> None:
            if _error_name(error) == ALREADY_EXISTS:
                # BlueZ still has our application (it keeps it across power
                # cycles): the monitor is in place.
                done()
                return
            self._registering = False
            log.info("RegisterMonitor failed: %s", _error_name(error))
            if self._wanted and self._scan:
                # Still useful: adverts arrive while someone else discovers.
                self._set_state("listening")
                self._schedule_retry()

        def register(*_ignored: Any) -> None:
            self.bus.call_async(
                BLUEZ, adapter, MONITOR_MANAGER, "RegisterMonitor", "o",
                (dbus.ObjectPath(self.app_path),), done, failed, timeout=10,
            )

        if self._released:
            # After a release BlueZ may still hold the application without a
            # monitor; registering it again would only say AlreadyExists.
            self._released = False
            self.bus.call_async(
                BLUEZ, adapter, MONITOR_MANAGER, "UnregisterMonitor", "o",
                (dbus.ObjectPath(self.app_path),), register, register, timeout=10,
            )
        else:
            register()

    def _unregister(self) -> None:
        if not self._registered or self._registered_adapter is None:
            return
        adapter = self._registered_adapter
        self._registered, self._registered_adapter = False, None
        self.bus.call_async(
            BLUEZ, adapter, MONITOR_MANAGER, "UnregisterMonitor", "o",
            (dbus.ObjectPath(self.app_path),), lambda: None,
            lambda error: log.info("UnregisterMonitor failed: %s", _error_name(error)),
            timeout=10,
        )

    def _export(self) -> bool:
        """Put the application and its monitor on the bus (once)."""
        if self._app is not None:
            return True
        monitor = Monitor(f"{self.app_path}/monitor0", self)
        app = MonitorApplication(monitor)
        try:
            monitor.add_to_connection(self.bus, monitor.path)
            try:
                app.add_to_connection(self.bus, self.app_path)
            except Exception:
                monitor.remove_from_connection()
                raise
        except (KeyError, ValueError) as error:
            log.warning("cannot export the advertisement monitor: %s", error)
            return False
        self._monitor, self._app = monitor, app
        return True

    def _unexport(self) -> None:
        for obj in (self._app, self._monitor):
            if obj is None:
                continue
            try:
                obj.remove_from_connection()
            except LookupError:
                pass
        self._app = self._monitor = None

    def _schedule_retry(self) -> None:
        if self._retry_pending:
            return
        delay = min(RETRY_MAX, RETRY_FIRST * 2 ** min(self._retries, 8))
        self._retries += 1
        self._retry_pending = True
        generation = self._generation

        def retry() -> bool:
            if generation == self._generation:
                self._retry_pending = False
                if self._wanted and self._scan:
                    self.refresh()
            return False

        self._timer(delay, retry)

    def monitor_released(self) -> None:
        if self._registering:
            return      # the old application, dropped by our own re-registration
        self._registered, self._registered_adapter = False, None
        self._released = True
        if self._wanted and self._scan:
            self._set_state("listening")
            self._schedule_retry()

    def monitor_active(self) -> None:
        self._retries = 0
        self._set_state("monitoring")

    def device_found(self, path: str) -> None:
        if not path.startswith("/org/bluez/") or not self._scan:
            return
        cached = self._devices.get(path)
        if cached is not None:
            # Without RSSI thresholds BlueZ calls this for every matching
            # advert. Changed data and RSSI come as PropertiesChanged; here
            # only "still there" is new, so no D-Bus round trip.
            if cached[0] is not None:
                self._update(path, cached[0], cached[1], report=True)
            return
        self.bus.call_async(
            BLUEZ, path, PROPERTIES, "GetAll", "s", (DEVICE,),
            lambda properties: self._device_properties(path, properties),
            lambda _error: None, timeout=5,
        )

    # ---- signals ------------------------------------------------------------------

    def _device_properties(self, path: str, properties: Any) -> None:
        data = apple_payload(properties.get("ManufacturerData", {}))
        self._update(path, data, _rssi(properties.get("RSSI")), report=data is not None)

    def _properties_changed(self, interface: str, changed: Any, _invalidated: Any,
                            path: str = "") -> None:
        path = str(path)
        if interface == ADAPTER and "Powered" in changed:
            self._powered[path] = bool(changed["Powered"])
            # BlueZ keeps the monitor while the adapter is off: no new
            # registration, refresh() only updates the state.
            if self._wanted:
                self.refresh()
            return
        if interface != DEVICE:
            return
        if "Paired" in changed or "Modalias" in changed:
            self.refresh()
        if not self._scan:
            return
        has_data = "ManufacturerData" in changed
        data = apple_payload(changed["ManufacturerData"]) if has_data else None
        rssi = _rssi(changed.get("RSSI")) if "RSSI" in changed else None
        if not has_data and rssi is None:
            return
        cached = self._devices.get(path)
        if not has_data:
            if cached is None or cached[0] is None:
                return          # an RSSI update of a device we never matched
            data = cached[0]
        self._update(path, data, rssi if rssi is not None else (cached or (None, None))[1],
                     report=data is not None)

    def _interfaces_added(self, path: str, interfaces: Any) -> None:
        if ADAPTER in interfaces:
            self.refresh()
            return
        device = interfaces.get(DEVICE)
        if device is None:
            return
        if paired_model(device) is not None:
            self.refresh()
        if not self._scan:
            return
        data = apple_payload(device.get("ManufacturerData", {}))
        if data is not None:
            self._update(str(path), data, _rssi(device.get("RSSI")), report=True)

    def _interfaces_removed(self, path: str, interfaces: Any) -> None:
        path = str(path)
        if ADAPTER in interfaces:
            if path == self._registered_adapter:
                self._registered, self._registered_adapter = False, None
            self.refresh()
            return
        if DEVICE in interfaces:
            self._devices.pop(path, None)
            if path in self._paired:
                paired = dict(self._paired)
                paired.pop(path)
                self._set_paired(paired)

    def _update(self, path: str, data: bytes | None, rssi: int | None, *, report: bool) -> None:
        self._devices[path] = (data, rssi)
        self._devices.move_to_end(path)
        while len(self._devices) > MAX_DEVICES:
            self._devices.popitem(last=False)
        if report and data is not None:
            self._on_sighting(Sighting(
                path=path, data=data, rssi=rssi, paired=path in self._paired,
                seen_at=self._clock(),
            ))

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self._on_state(state)


def _error_name(error: Exception) -> str:
    getter = getattr(error, "get_dbus_name", None)
    return str(getter()) if callable(getter) else type(error).__name__

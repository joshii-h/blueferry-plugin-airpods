"""Test doubles: a scripted system bus with BlueZ on it, sightings, a clock.

``FakeSystemBus`` answers the asynchronous calls the watcher makes
(``GetManagedObjects``, ``RegisterMonitor``, ``GetAll`` …) from a dict of
BlueZ objects, and lets a test emit BlueZ's signals. Nothing touches a real
bus. Replies are delivered at once, like a main loop that runs them next.
"""
from __future__ import annotations

from typing import Any

import dbus
from samples import THEENGS, payload

from blueferry_airpods.bluez import (
    ADAPTER,
    DEVICE,
    MONITOR_MANAGER,
    OBJECT_MANAGER,
    PROPERTIES,
    Sighting,
)

ADAPTER_PATH = "/org/bluez/hci0"


class Match:
    def __init__(self, bus: FakeSystemBus, entry: tuple) -> None:
        self.bus, self.entry = bus, entry

    def remove(self) -> None:
        if self.entry in self.bus.receivers:
            self.bus.receivers.remove(self.entry)


class FakeSystemBus:
    def __init__(self, *, monitor: bool = True, powered: bool = True,
                 bluez: bool = True) -> None:
        adapter: dict[str, Any] = {ADAPTER: {"Powered": dbus.Boolean(powered)}}
        if monitor:
            adapter[MONITOR_MANAGER] = {}
        self.objects: dict[str, dict[str, Any]] = {ADAPTER_PATH: adapter}
        self.bluez = bluez
        self.receivers: list[tuple] = []
        self.calls: list[tuple[str, str, str]] = []
        self.registered: list[str] = []
        self.fail_register = False

    # ---- what the watcher calls ------------------------------------------------

    def add_signal_receiver(self, handler, signal_name, interface, bus_name, path=None,
                            **keywords) -> Match:
        entry = (handler, signal_name, interface, keywords)
        self.receivers.append(entry)
        return Match(self, entry)

    def call_async(self, bus_name, path, interface, method, signature, args, reply, error,
                   timeout=None) -> None:
        self.calls.append((path, interface, method))
        if not self.bluez:
            error(dbus.exceptions.DBusException(
                "no BlueZ", name="org.freedesktop.DBus.Error.ServiceUnknown"))
            return
        if (interface, method) == (OBJECT_MANAGER, "GetManagedObjects"):
            reply(self.objects)
        elif (interface, method) == (MONITOR_MANAGER, "RegisterMonitor"):
            if self.fail_register:
                error(dbus.exceptions.DBusException(
                    "nope", name="org.bluez.Error.Failed"))
                return
            self.registered.append(str(args[0]))
            reply()
        elif (interface, method) == (MONITOR_MANAGER, "UnregisterMonitor"):
            self.registered.remove(str(args[0]))
            reply()
        elif (interface, method) == (PROPERTIES, "GetAll"):
            reply(self.objects.get(path, {}).get(DEVICE, {}))
        else:
            error(dbus.exceptions.DBusException("unknown method"))

    # ---- what the test does ----------------------------------------------------

    def emit(self, signal_name: str, *args: Any, path: str = "") -> None:
        for handler, name, _interface, keywords in list(self.receivers):
            if name != signal_name:
                continue
            if "path_keyword" in keywords:
                handler(*args, path=path)
            else:
                handler(*args)

    def add_device(self, path: str, **properties: Any) -> None:
        self.objects[path] = {DEVICE: properties}

    def advert(self, path: str, hex_text: str, rssi: int | None = -50) -> None:
        """BlueZ reports a changed advert on ``path``."""
        changed: dict[str, Any] = {
            "ManufacturerData": dbus.Dictionary(
                {dbus.UInt16(0x004C): dbus.Array(
                    [dbus.Byte(b) for b in payload(hex_text)], signature="y")},
                signature="qv",
            ),
        }
        if rssi is not None:
            changed["RSSI"] = dbus.Int16(rssi)
        self.emit("PropertiesChanged", DEVICE, changed, [], path=path)


def paired_airpods(bus: FakeSystemBus, path: str, product: int) -> None:
    bus.add_device(path, Paired=dbus.Boolean(True), Address="00:00:00:00:00:01",
                   Modalias=f"bluetooth:v004Cp{product:04X}d0A31")


def sighting(name: str = "pro2_left_in_ear", *, path: str = "/org/bluez/hci0/dev_X",
             rssi: int | None = -50, paired: bool = False, at: float = 100.0) -> Sighting:
    return Sighting(path=path, data=payload(THEENGS[name]), rssi=rssi, paired=paired,
                    seen_at=at)


class Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

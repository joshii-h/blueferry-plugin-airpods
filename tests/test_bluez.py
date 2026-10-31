"""The BlueZ side: monitor registration, signals, paired models, no discovery."""
from __future__ import annotations

import dbus
import pytest
from fakes import ADAPTER_PATH, FakeSystemBus, Timers, paired_airpods
from samples import THEENGS

from blueferry_airpods.bluez import (
    ADAPTER,
    DEVICE,
    MAX_DEVICES,
    MONITOR,
    MONITOR_MANAGER,
    PROPERTIES,
    BluezWatcher,
    apple_payload,
    paired_model,
)


class Recorder:
    def __init__(self) -> None:
        self.sightings: list = []
        self.paired: list[frozenset[int]] = []
        self.states: list[str] = []


def watcher(bus: FakeSystemBus, timers: Timers | None = None) -> tuple[BluezWatcher, Recorder]:
    seen = Recorder()
    w = BluezWatcher(bus, seen.sightings.append, seen.paired.append, seen.states.append,
                     clock=lambda: 5.0, timer=timers if timers is not None else Timers())
    return w, seen


APP = "/io/weirdware/blueferry/airpods"


def methods(bus: FakeSystemBus, name: str) -> int:
    return sum(1 for _path, _interface, method in bus.calls if method == name)


def test_registers_an_apple_proximity_monitor() -> None:
    bus = FakeSystemBus()
    w, seen = watcher(bus)
    w.start()
    assert bus.registered == ["/io/weirdware/blueferry/airpods"]
    assert seen.states == ["starting", "monitoring"]
    objects = w._app.GetManagedObjects()
    (path, interfaces), = objects.items()
    assert path == "/io/weirdware/blueferry/airpods/monitor0"
    props = interfaces[MONITOR]
    assert props["Type"] == "or_patterns"
    (start, ad_type, content), = props["Patterns"]
    assert (start, ad_type, bytes(content)) == (0, 0xFF, b"\x4c\x00\x07")
    assert w._monitor.Get(MONITOR, "Type") == "or_patterns"


def test_never_starts_discovery_or_touches_the_adapter() -> None:
    bus = FakeSystemBus()
    w, _seen = watcher(bus)
    w.start()
    bus.advert("/org/bluez/hci0/dev_A", THEENGS["pro2_left_in_ear"])
    w.stop()
    methods = {method for _path, _interface, method in bus.calls}
    assert methods <= {"GetManagedObjects", "RegisterMonitor", "UnregisterMonitor", "GetAll"}
    assert not any(interface == ADAPTER for _path, interface, _method in bus.calls)


def test_stop_unregisters_and_removes_the_match_rules() -> None:
    bus = FakeSystemBus()
    w, seen = watcher(bus)
    w.start()
    w.stop()
    assert bus.registered == [] and bus.receivers == []
    assert seen.states[-1] == "stopped"


def test_stop_takes_the_objects_off_the_bus_for_the_next_watcher() -> None:
    # dbus.SystemBus() is one connection per process: the watcher after a
    # stop exports at the same path.
    bus = FakeSystemBus()
    first, _seen = watcher(bus)
    first.start()
    assert set(bus.exported) == {APP, APP + "/monitor0"}
    first.stop()
    assert bus.exported == {} and first._app is None and first._monitor is None
    second, seen = watcher(bus)
    second.start()
    assert seen.states[-1] == "monitoring" and bus.registered == [APP]
    assert set(bus.exported) == {APP, APP + "/monitor0"}


def test_a_taken_path_falls_back_to_listening() -> None:
    bus = FakeSystemBus()
    first, _seen = watcher(bus)
    first.start()
    second, seen = watcher(bus)
    second.start()
    assert seen.states[-1] == "listening" and methods(bus, "RegisterMonitor") == 1


def test_a_power_cycle_keeps_the_registration() -> None:
    bus = FakeSystemBus()
    w, seen = watcher(bus)
    w.start()
    bus.power(False)
    assert seen.states[-1] == "powered-off"
    bus.power(True)
    assert seen.states[-1] == "monitoring"
    assert bus.registered == [APP] and methods(bus, "RegisterMonitor") == 1


def test_already_exists_means_registered() -> None:
    bus = FakeSystemBus()
    bus.registered.append(APP)              # BlueZ still holds the application
    w, seen = watcher(bus)
    w.start()
    assert seen.states[-1] == "monitoring" and bus.registered == [APP]


def test_after_a_release_it_registers_again_later() -> None:
    bus = FakeSystemBus()
    timers = Timers()
    w, seen = watcher(bus, timers)
    w.start()
    w._monitor.Release()
    assert seen.states[-1] == "listening"
    assert timers.fire() == [5]
    # The application BlueZ may still hold goes first, then a fresh one.
    assert [m for _p, _i, m in bus.calls][-3:] == [
        "GetManagedObjects", "UnregisterMonitor", "RegisterMonitor"]
    assert seen.states[-1] == "monitoring" and bus.registered == [APP]
    assert timers.pending == []


def test_failed_registrations_retry_with_a_growing_pause() -> None:
    bus = FakeSystemBus()
    bus.fail_register = True
    timers = Timers()
    w, seen = watcher(bus, timers)
    w.start()
    assert seen.states[-1] == "listening"
    assert timers.fire() == [5]
    assert timers.fire() == [10]
    bus.fail_register = False
    assert timers.fire() == [20]
    assert seen.states[-1] == "monitoring" and timers.pending == []


def test_a_stopped_watcher_ignores_its_retry() -> None:
    bus = FakeSystemBus()
    bus.fail_register = True
    timers = Timers()
    w, _seen = watcher(bus, timers)
    w.start()
    w.stop()
    calls = len(bus.calls)
    timers.fire()
    assert len(bus.calls) == calls


def test_a_powered_adapter_wins() -> None:
    bus = FakeSystemBus(powered=False)
    bus.objects["/org/bluez/hci1"] = {ADAPTER: {"Powered": dbus.Boolean(True)},
                                     MONITOR_MANAGER: {}}
    w, seen = watcher(bus)
    w.start()
    assert seen.states[-1] == "monitoring"
    assert ("/org/bluez/hci1", MONITOR_MANAGER, "RegisterMonitor") in bus.calls


def test_scanning_switched_off_and_on() -> None:
    bus = FakeSystemBus()
    w, seen = watcher(bus)
    w.start()
    w.set_scanning(False)
    assert seen.states[-1] == "idle" and bus.registered == []
    bus.advert("/org/bluez/hci0/dev_A", THEENGS["pro2_left_in_ear"])
    assert seen.sightings == []
    # Pairing is still followed while idle.
    paired_airpods(bus, "/org/bluez/hci0/dev_P", 0x2024)
    bus.emit("PropertiesChanged", DEVICE, {"Paired": dbus.Boolean(True)}, [],
             path="/org/bluez/hci0/dev_P")
    assert seen.paired[-1] == {0x2024} and seen.states[-1] == "idle"
    w.set_scanning(True)
    assert seen.states[-1] == "monitoring" and bus.registered == [APP]


def test_started_without_scanning_never_registers() -> None:
    bus = FakeSystemBus()
    w, seen = watcher(bus)
    w.set_scanning(False)
    w.start()
    assert seen.states[-1] == "idle" and methods(bus, "RegisterMonitor") == 0


def test_adverts_become_sightings() -> None:
    bus = FakeSystemBus()
    w, seen = watcher(bus)
    w.start()
    bus.advert("/org/bluez/hci0/dev_A", THEENGS["pro2_left_in_ear"], rssi=-61)
    (s,) = seen.sightings
    assert s.data[:3] == b"\x07\x19\x01" and s.rssi == -61 and not s.paired
    assert s.seen_at == 5.0


def test_an_rssi_update_reuses_the_last_advert() -> None:
    bus = FakeSystemBus()
    w, seen = watcher(bus)
    w.start()
    bus.advert("/org/bluez/hci0/dev_A", THEENGS["pro2_left_in_ear"], rssi=-80)
    bus.emit("PropertiesChanged", DEVICE, {"RSSI": dbus.Int16(-40)}, [],
             path="/org/bluez/hci0/dev_A")
    assert [s.rssi for s in seen.sightings] == [-80, -40]
    # An RSSI update of something never matched is ignored.
    bus.emit("PropertiesChanged", DEVICE, {"RSSI": dbus.Int16(-40)}, [],
             path="/org/bluez/hci0/dev_B")
    assert len(seen.sightings) == 2


def test_other_manufacturer_data_is_ignored() -> None:
    bus = FakeSystemBus()
    w, seen = watcher(bus)
    w.start()
    nearby_info = {"ManufacturerData": {dbus.UInt16(0x004C): dbus.Array(
        [dbus.Byte(b) for b in bytes.fromhex("1005031c0d0e36")], signature="y")}}
    bus.emit("PropertiesChanged", DEVICE, nearby_info, [], path="/org/bluez/hci0/dev_C")
    samsung = {"ManufacturerData": {dbus.UInt16(0x0075): dbus.Array(
        [dbus.Byte(7), dbus.Byte(0x19)], signature="y")}}
    bus.emit("PropertiesChanged", DEVICE, samsung, [], path="/org/bluez/hci0/dev_D")
    bus.emit("PropertiesChanged", "org.bluez.MediaControl1", {"Connected": True}, [],
             path="/org/bluez/hci0/dev_E")
    assert seen.sightings == []


def test_device_found_reads_the_device() -> None:
    bus = FakeSystemBus()
    path = "/org/bluez/hci0/dev_F"
    bus.add_device(path, RSSI=dbus.Int16(-58), ManufacturerData={
        dbus.UInt16(0x004C): dbus.Array(
            [dbus.Byte(b) for b in bytes.fromhex(THEENGS["max_green"])[2:]], signature="y")})
    w, seen = watcher(bus)
    w.start()
    w._monitor.DeviceFound(path)
    w._monitor.DeviceFound("/elsewhere/dev")       # not BlueZ's: ignored
    (s,) = seen.sightings
    assert s.path == path and s.rssi == -58


def test_device_found_again_needs_no_round_trip() -> None:
    bus = FakeSystemBus()
    path = "/org/bluez/hci0/dev_F"
    bus.add_device(path, RSSI=dbus.Int16(-58), ManufacturerData={
        dbus.UInt16(0x004C): dbus.Array(
            [dbus.Byte(b) for b in bytes.fromhex(THEENGS["max_green"])[2:]], signature="y")})
    bus.add_device("/org/bluez/hci0/dev_G", RSSI=dbus.Int16(-40))     # no proximity data
    w, seen = watcher(bus)
    w.start()
    for _advert in range(5):
        w._monitor.DeviceFound(path)
        w._monitor.DeviceFound("/org/bluez/hci0/dev_G")
    get_all = [c for c in bus.calls if c[1:] == (PROPERTIES, "GetAll")]
    assert len(get_all) == 2
    assert [(s.path, s.rssi) for s in seen.sightings] == [(path, -58)] * 5


def test_paired_models_come_from_the_modalias() -> None:
    bus = FakeSystemBus()
    paired_airpods(bus, "/org/bluez/hci0/dev_P", 0x2014)
    bus.add_device("/org/bluez/hci0/dev_Q", Paired=dbus.Boolean(False),
                   Modalias="bluetooth:v004Cp200Ad0001")       # not paired
    bus.add_device("/org/bluez/hci0/dev_R", Paired=dbus.Boolean(True),
                   Modalias="usb:v05ACp0250d0100")              # not Bluetooth Apple
    w, seen = watcher(bus)
    w.start()
    assert seen.paired == [frozenset({0x2014})]
    assert w.paired_models == {0x2014}


def test_an_advert_on_a_paired_device_is_marked_resolved() -> None:
    bus = FakeSystemBus()
    paired_airpods(bus, "/org/bluez/hci0/dev_P", 0x2014)
    w, seen = watcher(bus)
    w.start()
    bus.advert("/org/bluez/hci0/dev_P", THEENGS["pro2_left_in_ear"], rssi=-90)
    assert seen.sightings[0].paired


def test_pairing_later_refreshes_the_models() -> None:
    bus = FakeSystemBus()
    w, seen = watcher(bus)
    w.start()
    assert seen.paired == [frozenset()]
    paired_airpods(bus, "/org/bluez/hci0/dev_P", 0x2024)
    bus.emit("PropertiesChanged", DEVICE, {"Paired": dbus.Boolean(True)}, [],
             path="/org/bluez/hci0/dev_P")
    assert seen.paired[-1] == {0x2024}
    del bus.objects["/org/bluez/hci0/dev_P"]
    bus.emit("InterfacesRemoved", "/org/bluez/hci0/dev_P", [DEVICE])
    assert seen.paired[-1] == frozenset()


def test_without_monitor_support_it_only_listens() -> None:
    bus = FakeSystemBus(monitor=False)
    w, seen = watcher(bus)
    w.start()
    assert seen.states[-1] == "listening" and bus.registered == []
    bus.advert("/org/bluez/hci0/dev_A", THEENGS["pro2_left_in_ear"])
    assert len(seen.sightings) == 1


def test_a_refused_registration_falls_back_to_listening() -> None:
    bus = FakeSystemBus()
    bus.fail_register = True
    w, seen = watcher(bus)
    w.start()
    assert seen.states[-1] == "listening"


@pytest.mark.parametrize(("bus", "state"), [
    (FakeSystemBus(powered=False), "powered-off"),
    (FakeSystemBus(bluez=False), "unavailable"),
])
def test_adapter_states(bus: FakeSystemBus, state: str) -> None:
    w, seen = watcher(bus)
    w.start()
    assert seen.states[-1] == state and bus.registered == []


def test_no_adapter() -> None:
    bus = FakeSystemBus()
    bus.objects.clear()
    w, seen = watcher(bus)
    w.start()
    assert seen.states[-1] == "no-adapter"


def test_powering_on_registers_the_monitor() -> None:
    bus = FakeSystemBus(powered=False)
    w, seen = watcher(bus)
    w.start()
    bus.objects[ADAPTER_PATH][ADAPTER]["Powered"] = dbus.Boolean(True)
    bus.emit("PropertiesChanged", ADAPTER, {"Powered": dbus.Boolean(True)}, [],
             path=ADAPTER_PATH)
    assert seen.states[-1] == "monitoring" and len(bus.registered) == 1


def test_bluez_restart_registers_again() -> None:
    bus = FakeSystemBus()
    w, seen = watcher(bus)
    w.start()
    bus.registered.clear()               # bluetoothd went away with our monitor
    bus.emit("NameOwnerChanged", "org.bluez", ":1.5", "")
    assert seen.states[-1] == "unavailable"
    bus.emit("NameOwnerChanged", "org.bluez", "", ":1.9")
    assert seen.states[-1] == "monitoring" and len(bus.registered) == 1


def test_released_monitor_means_listening() -> None:
    bus = FakeSystemBus()
    timers = Timers()
    w, seen = watcher(bus, timers)
    w.start()
    w._monitor.Release()
    assert seen.states[-1] == "listening" and len(timers.pending) == 1
    w.stop()
    timers.fire()
    assert seen.states[-1] == "stopped"


def test_the_device_cache_stays_small() -> None:
    bus = FakeSystemBus()
    w, _seen = watcher(bus)
    w.start()
    for number in range(MAX_DEVICES + 50):
        bus.advert(f"/org/bluez/hci0/dev_{number}", THEENGS["pro2_left_in_ear"])
    assert len(w._devices) == MAX_DEVICES


def test_payload_helpers() -> None:
    assert apple_payload({}) is None
    assert apple_payload({0x004C: [0x07, 0x19]}) == b"\x07\x19"
    assert apple_payload({0x004C: [0x10, 0x05]}) is None
    assert apple_payload(None) is None
    assert paired_model({"Paired": True, "Modalias": "bluetooth:v004Cp200Ed0A31"}) == 0x200E
    assert paired_model({"Paired": False, "Modalias": "bluetooth:v004Cp200Ed0A31"}) is None
    assert paired_model({"Paired": True, "Modalias": "bluetooth:v0075p200Ed0A31"}) is None
    assert MONITOR_MANAGER == "org.bluez.AdvertisementMonitorManager1"

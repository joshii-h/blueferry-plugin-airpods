"""The plugin through the kit's fake BlueFerry host, with BlueZ faked."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from blueferry.plugin_api.manifest import parse_manifest
from blueferry.plugin_api.testing import inline_service
from blueferry_plugin_kit.testing import FakeHost
from fakes import Clock, FakeSystemBus, Timers, paired_airpods
from samples import THEENGS

from blueferry_airpods import manifest_text
from blueferry_airpods.bluez import BluezWatcher
from blueferry_airpods.service import ACCESSORIES_INTERFACE, AirPodsService
from blueferry_airpods.settings import Settings, SettingsStore

ADDRESS_PATH = "/org/bluez/hci0/dev_4A_11_22_33_44_55"


class Env:
    def __init__(self, tmp_path: Path, bus: FakeSystemBus | None = None, *,
                 model: str | None = None, disabled: bool = False) -> None:
        self.bus = bus or FakeSystemBus()
        self.store = SettingsStore(tmp_path / "config")
        if model is not None:
            self.store.save(Settings(model=model))
        self.clock = Clock()
        self.timers = Timers()
        self.disabled = disabled
        self.watchers: list[BluezWatcher] = []
        self.changed = 0

        def factory(on_sighting, on_paired, on_state):
            watcher = BluezWatcher(self.bus, on_sighting, on_paired, on_state,
                                   clock=self.clock, timer=self.timers)
            self.watchers.append(watcher)
            return watcher

        self.service = inline_service(
            AirPodsService, parse_manifest(manifest_text()), settings=self.store,
            watcher_factory=factory, monotonic=self.clock, every=None,
            disabled=lambda: self.disabled, clock=self.clock,
        )
        self.host = FakeHost(self.service)

        def changed(*args):
            assert not args, "Changed carries no content"
            self.changed += 1

        self.service.Changed = changed
        self.service.start()

    def batteries(self) -> dict:
        return json.loads(self.host.call("GetBatteries"))


def test_unconfigured_card_explains_the_setup(tmp_path: Path) -> None:
    env = Env(tmp_path)
    (item,) = env.host.card_items()
    assert item["id"] == "setup" and "Pair your AirPods" in item["subtitle"]
    assert env.service.status()["state"] == "unconfigured"


def test_nothing_to_look_for_means_no_scan(tmp_path: Path) -> None:
    env = Env(tmp_path)
    assert env.bus.registered == [] and env.batteries()["state"] == "idle"
    assert env.host.set_config({"model": "AirPods-Pro-2"}) == {"ok": True}
    assert env.bus.registered == ["/io/weirdware/blueferry/airpods"]
    assert env.batteries()["state"] == "monitoring"
    assert env.host.set_config({"model": "paired"}) == {"ok": True}
    assert env.bus.registered == [] and env.batteries()["state"] == "idle"


def test_pairing_later_starts_the_scan(tmp_path: Path) -> None:
    env = Env(tmp_path)
    assert env.bus.registered == []
    paired_airpods(env.bus, "/org/bluez/hci0/dev_P", 0x2014)
    env.bus.emit("PropertiesChanged", "org.bluez.Device1", {"Paired": True}, [],
                 path="/org/bluez/hci0/dev_P")
    assert len(env.bus.registered) == 1
    assert env.service.status()["detail"] == "Scanning passively for AirPods Pro 2"


def test_strangers_never_reach_the_card_or_the_interface(tmp_path: Path) -> None:
    env = Env(tmp_path)
    for name in ("pro2_both_in_ear", "max_green", "pro_left_50"):
        env.bus.advert(ADDRESS_PATH, THEENGS[name], rssi=-30)
    assert [i["id"] for i in env.host.card_items()] == ["setup"]
    assert env.batteries()["devices"] == []
    env.host.assert_never_sent("AirPods Pro", "AirPods Max", "4A_11", "4A:11")


def test_confirmed_model_shows_the_battery(tmp_path: Path) -> None:
    env = Env(tmp_path, model="AirPods-Pro-2")
    (item,) = env.host.card_items()
    assert item["title"] == "AirPods Pro 2" and item["subtitle"].startswith("Not nearby")
    before = env.host.card_changed
    env.bus.advert(ADDRESS_PATH, THEENGS["pro2_both_charging_left_90"], rssi=-55)
    assert env.host.card_changed == before + 1 and env.changed >= 1
    items = {i["id"]: i for i in env.host.card_items()}
    assert set(items) == {"m2014"}
    assert items["m2014"]["subtitle"] == (
        "Left 90 % (charging) · Right 100 % (charging) · Case 70 % · case open"
    )
    assert env.service.status() == {
        "state": "ok", "detail": "Scanning passively for AirPods Pro 2",
    }
    env.host.assert_never_sent("4A_11", "4A:11", ADDRESS_PATH)


def test_rssi_changes_alone_do_not_ping_the_clients(tmp_path: Path) -> None:
    env = Env(tmp_path, model="AirPods-Pro-2")
    env.bus.advert(ADDRESS_PATH, THEENGS["pro2_left_in_ear"], rssi=-55)
    before = (env.host.card_changed, env.changed)
    env.bus.advert(ADDRESS_PATH, THEENGS["pro2_left_in_ear"], rssi=-50)
    assert (env.host.card_changed, env.changed) == before


def test_weak_adverts_of_your_model_are_ignored(tmp_path: Path) -> None:
    env = Env(tmp_path, model="AirPods-Pro-2")
    env.bus.advert(ADDRESS_PATH, THEENGS["pro2_left_in_ear"], rssi=-85)
    assert env.batteries()["devices"] == []


def test_paired_airpods_are_yours_without_settings(tmp_path: Path) -> None:
    bus = FakeSystemBus()
    paired_airpods(bus, "/org/bluez/hci0/dev_P", 0x200E)
    env = Env(tmp_path, bus)
    env.bus.advert(ADDRESS_PATH, THEENGS["pro_left_50"], rssi=-60)
    (device,) = env.batteries()["devices"]
    assert device["name"] == "AirPods Pro" and device["matched_by"] == "model"
    assert device["left"] == {"percent": 50, "charging": False, "in_ear": True}
    assert device["right"] is None and device["case"] is None


def test_get_batteries_json(tmp_path: Path) -> None:
    env = Env(tmp_path, model="AirPods-Pro-2")
    env.bus.advert(ADDRESS_PATH, THEENGS["pro2_in_case_70"], rssi=-50)
    env.clock.now += 42
    data = env.batteries()
    assert data["version"] == 1 and data["state"] == "monitoring"
    (device,) = data["devices"]
    assert device["id"] == "airpods-2014" and device["model_id"] == "0x2014"
    assert device["kind"] == "earbuds" and device["battery"] is None
    assert device["left"] == {"percent": 90, "charging": True, "in_ear": False}
    assert device["case"] == {"percent": 70, "charging": False}
    assert device["lid_open"] is True and device["age_seconds"] == 42
    assert device["seen_at"].endswith("Z")
    assert set(device) == {
        "id", "model_id", "name", "kind", "battery", "left", "right", "case", "lid_open",
        "matched_by", "age_seconds", "seen_at",
    }


def test_max_reports_one_battery(tmp_path: Path) -> None:
    env = Env(tmp_path, model="AirPods-Max")
    env.bus.advert(ADDRESS_PATH, THEENGS["max_green"], rssi=-50)
    (device,) = env.batteries()["devices"]
    assert device["kind"] == "headphones" and device["battery"]["percent"] == 100
    assert device["left"] is None and device["right"] is None
    item = next(i for i in env.host.card_items() if i["id"] == "m200a")
    assert item["subtitle"] == "Battery 100 %"


def test_old_readings_say_when_and_then_disappear(tmp_path: Path) -> None:
    env = Env(tmp_path, model="AirPods-Pro-2")
    env.bus.advert(ADDRESS_PATH, THEENGS["pro2_left_in_ear"], rssi=-50)
    env.clock.now += 5 * 60
    before = env.host.card_changed
    env.service.tick()
    assert env.host.card_changed == before + 1
    item = next(i for i in env.host.card_items() if i["id"] == "m2014")
    assert item["subtitle"].endswith("5 min ago")
    env.clock.now += 30 * 60
    env.service.tick()
    item = next(i for i in env.host.card_items() if i["id"] == "m2014")
    assert item["subtitle"].startswith("Not nearby")


def test_keeps_running_while_watching_and_idles_when_unconfigured(tmp_path: Path) -> None:
    env = Env(tmp_path, model="AirPods-Pro-2")
    env.service.last_activity = 0
    env.service.tick()
    assert env.service.last_activity == env.clock.now
    idle = Env(tmp_path / "other")
    idle.service.last_activity = 0
    idle.service.tick()
    assert idle.service.last_activity == 0


def test_disabled_in_blueferry_stops_scanning(tmp_path: Path) -> None:
    env = Env(tmp_path, model="AirPods-Pro-2")
    assert len(env.bus.registered) == 1
    env.disabled = True
    env.service.tick()
    assert env.bus.registered == []
    assert env.host.card_items()[0]["title"] == "Disabled in BlueFerry"
    env.service.last_activity = 0
    env.service.tick()
    assert env.service.last_activity == 0


def test_enabled_again_scans_again(tmp_path: Path) -> None:
    env = Env(tmp_path, model="AirPods-Pro-2")
    env.disabled = True
    env.service.tick()
    assert env.bus.exported == {}
    env.disabled = False
    env.service.tick()
    assert len(env.watchers) == 2 and env.watchers[-1].state == "monitoring"
    assert len(env.bus.registered) == 1 and len(env.bus.exported) == 2
    env.bus.advert(ADDRESS_PATH, THEENGS["pro2_left_in_ear"], rssi=-50)
    assert [d["id"] for d in env.batteries()["devices"]] == ["airpods-2014"]


def test_power_cycle_keeps_monitoring(tmp_path: Path) -> None:
    env = Env(tmp_path, model="AirPods-Pro-2")
    env.bus.power(False)
    assert env.host.card_items()[0]["title"] == "Bluetooth is off"
    env.bus.power(True)
    assert env.batteries()["state"] == "monitoring" and len(env.bus.registered) == 1


def test_polling_keeps_only_a_scanning_plugin_alive(tmp_path: Path) -> None:
    env = Env(tmp_path, model="AirPods-Pro-2")
    env.service.last_activity = 0
    env.batteries()
    assert env.service.last_activity == env.clock.now
    idle = Env(tmp_path / "other")
    idle.service.last_activity = 0
    idle.batteries()
    assert idle.service.last_activity == 0


def test_started_while_disabled_never_scans(tmp_path: Path) -> None:
    env = Env(tmp_path, model="AirPods-Pro-2", disabled=True)
    assert env.watchers == [] and env.bus.calls == []


def test_settings_round_trip(tmp_path: Path) -> None:
    env = Env(tmp_path)
    assert env.host.get_config() == {"values": {"model": "paired", "nearby": -70}}
    assert env.host.set_config({"model": "AirPods-Max", "nearby": -60}) == {"ok": True}
    assert env.store.load() == Settings(model="AirPods-Max", nearby=-60)
    assert [i["title"] for i in env.host.card_items()] == ["AirPods Max"]
    reply = env.host.set_config({"nearby": -20})
    assert reply["ok"] is False and "nearby" in reply["errors"]
    reply = env.host.set_config({"model": "AirPods-9"})
    assert reply["ok"] is False and "model" in reply["errors"]


@pytest.mark.parametrize(("bus", "title"), [
    (FakeSystemBus(powered=False), "Bluetooth is off"),
    (FakeSystemBus(bluez=False), "BlueZ is not running"),
])
def test_bluetooth_problems_on_the_card(tmp_path: Path, bus: FakeSystemBus, title: str) -> None:
    env = Env(tmp_path, bus, model="AirPods-Pro-2")
    (item,) = env.host.card_items()
    assert item["title"] == title
    assert env.service.status()["state"] == "error"


def test_listening_only_is_explained(tmp_path: Path) -> None:
    env = Env(tmp_path, FakeSystemBus(monitor=False), model="AirPods-Pro")
    ids = [i["id"] for i in env.host.card_items()]
    assert ids == ["m200e", "listening"]


def test_broken_settings_file(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "config")
    store.save(Settings())
    store.config_path.write_text("{nope")
    env = Env(tmp_path)
    (item,) = env.host.card_items()
    assert item["id"] == "error"
    assert env.service.status()["state"] == "error"


def test_german_card(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANG", "de_CH.UTF-8")
    env = Env(tmp_path, model="AirPods-Pro-2")
    env.bus.advert(ADDRESS_PATH, THEENGS["pro2_left_charging"], rssi=-50)
    item = next(i for i in env.host.card_items() if i["id"] == "m2014")
    assert item["subtitle"] == "Links 100 % (lädt) · Rechts 100 % · Case 30 %"


def test_interface_name_and_signal() -> None:
    assert ACCESSORIES_INTERFACE == "io.github.joshii_h.BlueFerryAccessories1"
    assert AirPodsService.GetBatteries._dbus_interface == ACCESSORIES_INTERFACE
    assert AirPodsService.Changed._dbus_interface == ACCESSORIES_INTERFACE
    assert AirPodsService.Changed._dbus_signature == ""

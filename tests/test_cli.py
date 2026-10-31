"""The command line: setup, status, forget, activation files."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from blueferry_airpods import PLUGIN_ID
from blueferry_airpods.__main__ import install_activation, main
from blueferry_airpods.settings import Settings, SettingsStore


def test_setup_stores_the_settings_and_installs_activation(capsys) -> None:
    assert main(["setup", "--model", "AirPods-Pro-2", "--nearby", "-65"]) == 0
    assert SettingsStore().load() == Settings(model="AirPods-Pro-2", nearby=-65)
    data = Path(os.environ["XDG_DATA_HOME"])
    manifest = data / "blueferry" / "plugins" / f"{PLUGIN_ID}.plugin"
    service = data / "dbus-1" / "services" / (
        "io.weirdware.BlueFerry.Plugin.io.weirdware.blueferry.airpods.service")
    assert manifest.is_file() and service.is_file()
    assert "serve" in service.read_text()
    assert oct(SettingsStore().config_path.stat().st_mode & 0o777) == "0o600"
    assert main(["status"]) == 0
    assert "AirPods-Pro-2" in capsys.readouterr().out
    assert main(["forget"]) == 0
    assert SettingsStore().load() == Settings()


def test_setup_refuses_bad_values() -> None:
    with pytest.raises(SystemExit):
        main(["setup", "--model", "AirPods-9"])
    with pytest.raises(SystemExit):
        main(["setup", "--nearby", "-10"])


def test_activation_is_left_to_a_system_manifest(tmp_path: Path, monkeypatch) -> None:
    system = tmp_path / "system"
    (system / "blueferry" / "plugins").mkdir(parents=True)
    (system / "blueferry" / "plugins" / f"{PLUGIN_ID}.plugin").write_text("x")
    monkeypatch.setenv("XDG_DATA_DIRS", str(system))
    assert install_activation(tmp_path / "home") == []

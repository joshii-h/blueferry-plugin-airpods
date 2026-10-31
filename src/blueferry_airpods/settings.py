"""The plugin's two settings in an owner-only ``config.json``.

Nothing here is secret: which AirPods model is yours and how close an
advert has to be. The file lives in
``$XDG_CONFIG_HOME/blueferry/plugins/io.weirdware.blueferry.airpods/``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from blueferry_plugin_kit.secrets import (
    SecretsError,
    read_private_text,
    write_private,
)
from blueferry_plugin_kit.secrets import config_dir as _kit_config_dir

from blueferry_airpods import PLUGIN_ID
from blueferry_airpods.proximity import CHOICES

SettingsError = SecretsError

PAIRED = "paired"
DEFAULT_NEARBY = -70
NEARBY_MIN, NEARBY_MAX = -100, -30


def config_dir() -> Path:
    return _kit_config_dir(PLUGIN_ID)


@dataclass(frozen=True, slots=True)
class Settings:
    #: ``paired`` (only AirPods paired with this computer) or a choice from
    #: :data:`blueferry_airpods.proximity.CHOICES` confirmed by the user.
    model: str = PAIRED
    #: Weakest signal (dBm) an advert may have to count as yours.
    nearby: int = DEFAULT_NEARBY

    def __post_init__(self) -> None:
        if self.model != PAIRED and self.model not in CHOICES:
            raise SettingsError("unknown model in config.json")
        if not NEARBY_MIN <= self.nearby <= NEARBY_MAX:
            raise SettingsError("nearby threshold out of range in config.json")


class SettingsStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or config_dir()

    @property
    def config_path(self) -> Path:
        return self.directory / "config.json"

    def load(self) -> Settings:
        """The stored settings; the defaults when nothing is stored."""
        try:
            raw = json.loads(read_private_text(self.config_path))
        except FileNotFoundError:
            return Settings()
        except ValueError:
            raise SettingsError("config.json is not valid JSON") from None
        if not isinstance(raw, dict):
            raise SettingsError("config.json is not an object")
        model = raw.get("model", PAIRED)
        nearby = raw.get("nearby", DEFAULT_NEARBY)
        if not isinstance(model, str) or isinstance(nearby, bool) or not isinstance(nearby, int):
            raise SettingsError("config.json has values of the wrong type")
        return Settings(model=model, nearby=nearby)

    def save(self, settings: Settings) -> None:
        write_private(self.config_path, json.dumps(
            {"model": settings.model, "nearby": settings.nearby}, indent=2,
        ) + "\n")

    def forget(self) -> None:
        self.config_path.unlink(missing_ok=True)


def blueferry_disabled(plugin_id: str = PLUGIN_ID) -> bool:
    """Whether the user disabled this plugin in BlueFerry (``plugins.json``).

    A disabled plugin does not scan, even when something else (the DMS
    widget) starts it over D-Bus.
    """
    import os

    home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    try:
        with open(os.path.join(home, "blueferry", "plugins.json"), encoding="utf-8") as stream:
            value = json.loads(stream.read(1024 * 1024))
    except (OSError, ValueError):
        return False
    disabled = value.get("disabled") if isinstance(value, dict) else None
    return isinstance(disabled, list) and plugin_id in disabled

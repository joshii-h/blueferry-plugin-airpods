"""User-visible strings in German and English, picked from the locale
(the same scheme as blueferry-plugin-calendar, -localsend and -webdav)."""
from __future__ import annotations

import os

_DE = {
    "left": "Links",
    "right": "Rechts",
    "case": "Case",
    "battery": "Akku",
    "charging": "lädt",
    "in_ear": "im Ohr",
    "lid_open": "Case offen",
    "minutes_ago": "vor {minutes} min",
    "not_nearby": "Nicht in der Nähe – Case in der Nähe dieses Computers öffnen",
    "waiting": "Warte auf ein Signal – Case in der Nähe dieses Computers öffnen",
    "setup_title": "Keine AirPods eingerichtet",
    "setup_hint": (
        "AirPods mit diesem Computer koppeln oder das Modell in den Plugin-Einstellungen "
        "wählen (Plugins > AirPods)"
    ),
    "listening_title": "Kein Hintergrund-Scan möglich",
    "listening_hint": (
        "BlueZ bietet hier keinen Advertisement-Monitor; Akkustände erscheinen nur, "
        "solange ein anderes Programm nach Geräten sucht"
    ),
    "no_adapter": "Kein Bluetooth-Adapter gefunden",
    "powered_off": "Bluetooth ist ausgeschaltet",
    "unavailable": "BlueZ läuft nicht",
    "starting": "Starte …",
    "disabled": "In BlueFerry deaktiviert",
    "status_monitoring": "Passiver Scan nach {models}",
    "status_listening": "Nur passiv mithören (kein Advertisement-Monitor)",
    "status_unconfigured": (
        "AirPods mit diesem Computer koppeln oder das Modell in den Einstellungen wählen"
    ),
    "nearby_range": "von {low} bis {high} dBm",
    "settings_unreadable": "Einstellungen nicht lesbar",
    "store_failed": "Einstellungen konnten nicht gespeichert werden: {error}",
}

_EN = {
    "left": "Left",
    "right": "Right",
    "case": "Case",
    "battery": "Battery",
    "charging": "charging",
    "in_ear": "in ear",
    "lid_open": "case open",
    "minutes_ago": "{minutes} min ago",
    "not_nearby": "Not nearby – open the case close to this computer",
    "waiting": "Waiting for a signal – open the case close to this computer",
    "setup_title": "No AirPods set up",
    "setup_hint": (
        "Pair your AirPods with this computer or pick the model in the plugin settings "
        "(Plugins > AirPods)"
    ),
    "listening_title": "No background scan",
    "listening_hint": (
        "BlueZ offers no advertisement monitor here; battery levels show only while "
        "another program searches for devices"
    ),
    "no_adapter": "No Bluetooth adapter found",
    "powered_off": "Bluetooth is off",
    "unavailable": "BlueZ is not running",
    "starting": "Starting …",
    "disabled": "Disabled in BlueFerry",
    "status_monitoring": "Scanning passively for {models}",
    "status_listening": "Listening only (no advertisement monitor)",
    "status_unconfigured": "Pair your AirPods with this computer or pick the model in the settings",
    "nearby_range": "from {low} to {high} dBm",
    "settings_unreadable": "Settings unreadable",
    "store_failed": "Could not store the settings: {error}",
}


def german() -> bool:
    for variable in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        value = os.environ.get(variable)
        if value:
            return value.split(":")[0].lower().startswith("de")
    return False


def t(key: str, **values: object) -> str:
    table = _DE if german() else _EN
    return table.get(key, _EN.get(key, key)).format(**values)

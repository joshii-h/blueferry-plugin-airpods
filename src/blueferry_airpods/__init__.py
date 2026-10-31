"""BlueFerry plugin: battery of your AirPods (left, right, case) from their
Bluetooth adverts.

Imports only ``blueferry.plugin_api`` from BlueFerry.
"""
from __future__ import annotations

from importlib import resources

PLUGIN_ID = "io.weirdware.blueferry.airpods"
__version__ = "0.1.0"


def manifest_text() -> str:
    return (
        resources.files(__name__).joinpath(f"{PLUGIN_ID}.plugin").read_text(encoding="utf-8")
    )

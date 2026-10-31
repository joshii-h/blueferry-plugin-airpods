"""``blueferry-airpods serve|setup|status|nearby|forget``.

``nearby`` is a diagnosis for the terminal: it scans passively for a few
seconds and lists the models it heard with their strongest signal, so you
can pick your model and threshold. It shows battery levels only for
adverts that pass the same matching as the card, and never addresses.
"""
from __future__ import annotations

import argparse
import logging
import os
import shlex
import shutil
import sys
from pathlib import Path

from blueferry.plugin_api.manifest import ManifestError, default_directories, parse_manifest
from blueferry.plugin_api.service import run

from blueferry_airpods import PLUGIN_ID, manifest_text
from blueferry_airpods.proximity import CHOICES, MODELS, decode, model_name
from blueferry_airpods.settings import (
    DEFAULT_NEARBY,
    NEARBY_MAX,
    NEARBY_MIN,
    PAIRED,
    Settings,
    SettingsError,
    SettingsStore,
)

ENTRY_POINT = "blueferry-airpods"
MAX_SCAN_SECONDS = 60


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _command() -> list[str]:
    """How the bus and BlueFerry should start this plugin."""
    beside = Path(sys.executable).parent / ENTRY_POINT
    if beside.is_file() and os.access(beside, os.X_OK):
        return [str(beside)]
    installed = shutil.which(ENTRY_POINT)
    if installed:
        return [installed]
    import blueferry.plugin_api as api

    roots = [
        str(Path(__file__).resolve().parents[1]),
        str(Path(api.__file__).resolve().parents[2]),
    ]
    return [
        "/usr/bin/env", "PYTHONPATH=" + ":".join(dict.fromkeys(roots)),
        sys.executable, "-m", "blueferry_airpods",
    ]


def install_activation(data_home: Path | None = None) -> list[Path]:
    """Write the user manifest and D-Bus service file unless the system has them
    (for a checkout; ``blueferry plugins install`` does this itself)."""
    data_home = data_home or _data_home()
    template = parse_manifest(manifest_text())
    written: list[Path] = []
    system = [d for d in default_directories() if not str(d).startswith(str(data_home))]
    if not any((directory / f"{PLUGIN_ID}.plugin").exists() for directory in system):
        command = _command()
        text = manifest_text().replace(
            f"Exec={ENTRY_POINT} serve", "Exec=" + shlex.join([*command, "serve"]),
        ).replace(f"Cli={ENTRY_POINT}", "Cli=" + shlex.join(command))
        parse_manifest(text)  # never install something clients would ignore
        target = data_home / "blueferry" / "plugins" / f"{PLUGIN_ID}.plugin"
        _write(target, text)
        written.append(target)
        service = data_home / "dbus-1" / "services" / f"{template.bus_name}.service"
        _write(service, "[D-BUS Service]\nName={}\nExec={}\n".format(
            template.bus_name, shlex.join([*command, "serve"]),
        ))
        written.append(service)
    return written


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(0o644)
    os.replace(temporary, path)


def setup(args: argparse.Namespace, store: SettingsStore | None = None) -> int:
    store = store or SettingsStore()
    try:
        store.save(Settings(model=args.model, nearby=args.nearby))
    except (SettingsError, OSError) as error:
        print(f"Could not store the settings: {error}", file=sys.stderr)
        return 1
    for path in install_activation():
        print(f"Installed {path}")
    print("Done. BlueFerry's phone card shows the battery when the AirPods are near.")
    return 0


def status(store: SettingsStore | None = None) -> int:
    try:
        settings = (store or SettingsStore()).load()
    except SettingsError as error:
        print(error)
        return 1
    which = ("AirPods paired with this computer" if settings.model == PAIRED
             else f"{settings.model} (and AirPods paired with this computer)")
    print(f"Model: {which}")
    print(f"Nearby threshold: {settings.nearby} dBm")
    return 0


def nearby(seconds: int, store: SettingsStore | None = None) -> int:
    """Scan passively for ``seconds`` and print what was heard (terminal only)."""
    import dbus
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    from blueferry_airpods.bluez import BluezWatcher
    from blueferry_airpods.matching import Tracker, wanted_models

    try:
        settings = (store or SettingsStore()).load()
    except SettingsError as error:
        print(error, file=sys.stderr)
        return 1
    DBusGMainLoop(set_as_default=True)
    loop = GLib.MainLoop()
    heard: dict[int, dict[str, object]] = {}
    tracker = Tracker(settings.nearby, wanted_models(frozenset(), settings.model))
    states: list[str] = []

    def sighting(found) -> None:
        proximity = decode(found.data)
        if proximity is None:
            return
        entry = heard.setdefault(proximity.model_id, {"count": 0, "rssi": None, "yours": None})
        entry["count"] = int(entry["count"]) + 1
        if found.rssi is not None and (entry["rssi"] is None or found.rssi > entry["rssi"]):
            entry["rssi"] = found.rssi
        if tracker.feed(found):
            entry["yours"] = proximity

    def paired(models: frozenset[int]) -> None:
        tracker.configure(nearby=settings.nearby, wanted=wanted_models(models, settings.model))

    watcher = BluezWatcher(dbus.SystemBus(), sighting, paired, states.append,
                           app_path=f"/io/weirdware/blueferry/airpods/scan{os.getpid()}")
    watcher.start()
    GLib.timeout_add_seconds(seconds, loop.quit)
    print(f"Scanning passively for {seconds} s (open the AirPods case nearby) …")
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    watcher.stop()
    # Let the asynchronous UnregisterMonitor go out before exiting.
    context = loop.get_context()
    for _round in range(20):
        context.iteration(False)
    print(f"Bluetooth: {', '.join(dict.fromkeys(states)) or 'no answer from BlueZ'}")
    yours = tracker.wanted
    print("Your models: " + (", ".join(model_name(m) for m in sorted(yours)) or
                             "none (pair them or pass a model to setup)"))
    if not heard:
        print("No AirPods or Beats adverts heard.")
        return 0
    for model, entry in sorted(heard.items(), key=lambda kv: -(kv[1]["rssi"] or -200)):
        rssi = f"{entry['rssi']} dBm" if entry["rssi"] is not None else "no RSSI"
        line = f"{model_name(model):<24} strongest {rssi:<9} adverts {entry['count']}"
        proximity = entry["yours"]
        if proximity is not None:
            def pct(battery) -> str:
                return "–" if battery is None else f"{battery.percent} %"
            line += (f"  yours: L {pct(proximity.left)}  R {pct(proximity.right)}"
                     f"  case {pct(proximity.case)}")
        elif model not in MODELS:
            line += "  (unknown model id)"
        print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=ENTRY_POINT, description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="serve on the session bus (started by D-Bus)")
    set_up = commands.add_parser("setup", help="store the settings, install the activation files")
    set_up.add_argument("--model", choices=(PAIRED, *CHOICES), default=PAIRED,
                        help="your model when the AirPods are not paired with this computer")
    set_up.add_argument("--nearby", type=int, default=DEFAULT_NEARBY, metavar="DBM",
                        help=f"weakest signal that counts ({NEARBY_MIN} to {NEARBY_MAX})")
    commands.add_parser("status", help="show the settings")
    scan = commands.add_parser("nearby", help="scan passively and list the models heard")
    scan.add_argument("--seconds", type=int, default=20,
                      help=f"how long to listen (at most {MAX_SCAN_SECONDS})")
    commands.add_parser("forget", help="remove the stored settings")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "setup":
        if not NEARBY_MIN <= args.nearby <= NEARBY_MAX:
            parser.error(f"--nearby takes {NEARBY_MIN} to {NEARBY_MAX}")
        return setup(args)
    if args.command == "status":
        return status()
    if args.command == "nearby":
        return nearby(max(1, min(args.seconds, MAX_SCAN_SECONDS)))
    if args.command == "forget":
        SettingsStore().forget()
        print("Removed the stored settings.")
        return 0
    try:
        manifest = parse_manifest(manifest_text())
    except ManifestError as error:
        print(error, file=sys.stderr)
        return 1
    from blueferry_airpods.service import AirPodsService

    return run(lambda bus: AirPodsService(manifest, bus).start())


if __name__ == "__main__":
    sys.exit(main())

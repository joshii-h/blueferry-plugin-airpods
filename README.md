# blueferry-plugin-airpods

The battery of your AirPods (left, right, case) in BlueFerry, read from the
Bluetooth adverts they send anyway: no pairing with this computer needed,
nothing is sent to them.

A plugin for [BlueFerry](https://github.com/joshii-h/blueferry) that shows
your AirPods (and Beats with the same advert) in the "From plugins" section
of the phone card (capability `card`), and offers the same numbers on a small
D-Bus interface for other desktop tools, for example the DMS widget
[Device Batteries](https://github.com/joshii-h/dms-device-batteries). It runs
as its own process on the session bus and talks to BlueFerry only through
`blueferry.plugin_api` (plugin API 1.5, see `PLUGINS.md` in the BlueFerry
repository).

Card and status texts are German or English, following the locale
(`LC_ALL`, `LC_MESSAGES`, `LANGUAGE`, `LANG`), as in the other plugins; the
command line stays English.

## Install

```sh
blueferry plugins install https://github.com/joshii-h/blueferry-plugin-airpods
```

BlueFerry shows the source, the version tag and commit, the capabilities and
the command it will run, and installs only after you confirm. Update with
`blueferry plugins update io.weirdware.blueferry.airpods`, remove with
`blueferry plugins remove io.weirdware.blueferry.airpods`.

It needs BlueZ with `org.bluez.AdvertisementMonitorManager1` (BlueZ 5.56 or
newer; some versions offer it only with bluetoothd's experimental features,
`-E`; registering was checked on BlueZ 5.87 running with `-E`) and a
Bluetooth LE adapter. Version 0.1.0 has not been tested with AirPods on real hardware
yet; see "Test it" below.

## Configure

Nothing to do if your AirPods are **paired with this computer**: their model
is known then. If they are paired with the iPhone only, pick your model once
in BlueFerry's settings, Plugins > AirPods > Settings, or:

```sh
blueferry plugins config io.weirdware.blueferry.airpods --set model=AirPods-Pro-2
```

| Setting | Meaning |
| --- | --- |
| `model` | `paired` (default): only models paired with this computer. Or your model (`AirPods-2`, `AirPods-Pro-2`, `AirPods-Max`, `Powerbeats-Pro`, …); it counts in addition to paired ones. The iPhone shows the model under Settings > Bluetooth > (i). |
| `nearby` | Advanced. The weakest signal that counts as yours, -100 to -30 dBm, default -70 (about the same room). -60 means only AirPods close to the computer. |

`blueferry plugins airpods nearby` listens passively for 20 seconds (up to
60 with `--seconds`) and lists the models heard with their strongest signal,
to find your model and a good threshold. It shows battery levels only for
adverts that pass the matching below, and never addresses.

## How it finds your AirPods (and not your neighbour's)

AirPods send a "Proximity Pairing" advert (Apple manufacturer data, type
0x07) while they are out of the case or the case is open. Its battery
fields are plain text; the part that would identify the owner is encrypted
with keys only the owner's Apple devices have. The advert comes from a
resolvable private address that changes regularly (the Bluetooth spec
suggests every 15 minutes), so a computer cannot recognise "your" AirPods by
address either. The plugin therefore decides like this:

1. An advert that BlueZ resolves to a device **paired with this computer**
   is yours, at any distance.
2. Otherwise its model must be one of **your models**: the models of the
   Apple audio devices paired with this computer (product id from their
   `Modalias`, which is the same id the advert carries) plus the model you
   picked in the settings.
3. And it must be **near**: at least as strong as `nearby`.
4. Of several matching adverts of one model, the strongest of the last 20
   seconds wins; a resolved one always wins.

Everything else is dropped as it arrives: not stored, logged or shown, on
the card nor on D-Bus. The limit: a second set of the **same model** that
is **closer** to the computer than yours (a colleague's AirPods Pro 2 on
the next desk while yours are in the closed case) can be shown as yours.
A stricter `nearby` makes that less likely. Pairing the AirPods with this
computer does not fix it on its own: BlueZ pairs them over classic
Bluetooth and, as far as known, does not learn the key that resolves their
LE address (LibrePods gets it from the AirPods over Apple's own protocol);
if your BlueZ does resolve it, rule 1 makes the match exact.

## What it shows

One card line per model, e.g. "AirPods Pro 2 — Left 90 % (charging) ·
Right 100 % (in ear) · Case 70 % · case open". The levels come in steps of
10 % (that is all the advert carries); "–" means the advert says "unknown"
(a pod out of range, or the case while its lid is closed). After two minutes
without an advert the line says how old it is, after 30 minutes "Not
nearby". "In ear" and "case open" are hints: the open decoders disagree on
these bits in some states (see `proximity.py`).

The card appears in the Qt and the GTK client and in `blueferry cards`. The
TUI and the Quickshell client do not render plugin cards yet (a BlueFerry
limitation, not this plugin's); use the D-Bus interface there.

The plugin scans **passively**: it registers an advertisement monitor that
matches only Apple's proximity advert (AD type 0xFF, bytes `4C 00 07`), and
BlueZ then listens in the background. It never starts a discovery, never
connects and never changes adapter settings. While it watches it keeps
running instead of exiting when idle; when it has nothing to watch (no
model) it exits after 10 minutes like other plugins. Disabled in BlueFerry,
it stops scanning, even if another tool starts it over D-Bus. Without the
monitor manager it falls back to listening only, which sees adverts only
while another program searches for devices (the card says so).

## D-Bus interface for other tools

On the plugin's own bus name (D-Bus activated, only callable by your user):

- bus name `io.weirdware.BlueFerry.Plugin.io.weirdware.blueferry.airpods`
- object `/io/weirdware/BlueFerry/Plugin`
- interface `io.github.joshii_h.BlueFerryAccessories1`
  - `GetBatteries() → s`: JSON, answered from memory at once
  - `Changed()`: content-free signal, call `GetBatteries` again

```json
{"version": 1, "state": "monitoring", "devices": [
  {"id": "airpods-2014", "model_id": "0x2014", "name": "AirPods Pro 2",
   "kind": "earbuds", "battery": null,
   "left": {"percent": 90, "charging": true, "in_ear": false},
   "right": {"percent": 100, "charging": true, "in_ear": false},
   "case": {"percent": 70, "charging": false}, "lid_open": true,
   "matched_by": "model", "age_seconds": 12, "seen_at": "2026-10-31T09:12:40Z"}]}
```

`kind` is `earbuds` or `headphones`; headphones (AirPods Max, Beats Solo)
have `battery` instead of `left`/`right`. Unknown levels are `null`.
`state` is `monitoring`, `listening`, `starting`, `powered-off`,
`no-adapter`, `unavailable` or `disabled`. Later versions only add keys.

```sh
busctl --user call io.weirdware.BlueFerry.Plugin.io.weirdware.blueferry.airpods \
    /io/weirdware/BlueFerry/Plugin io.github.joshii_h.BlueFerryAccessories1 GetBatteries
```

## Apple Watch: not supported

Not supported because a computer that is not the Watch's iPhone cannot read
its battery: the Watch puts no battery level into any Bluetooth advert
(its Nearby Info and Magic Switch messages carry flags and an
authentication tag, see furiousMAC's
[continuity](https://github.com/furiousMAC/continuity) documentation and
Celosia/Cunche, "Discontinued Privacy", PETS 2020), and it bonds only with
its own iPhone, which gets the level over that encrypted link. There is no
known Battery Service a foreign computer could read without pairing, and a
Watch cannot be paired with a computer. (An idea for later, untested: a
Shortcut on the Watch with "Get Battery Level" that sends the value to a
BlueFerry plugin.)

## Advert layout

`proximity.py` documents the bytes with their sources: AirPodsDesktop
(`AppleCP.h`), OpenPods, AirStatus, LibrePods and furiousMAC. In short:
byte 3–4 model id (little-endian), byte 5 status (bit 0x20: the left pod is
sending, else left and right swap; 0x02/0x08 in ear; 0x04/0x40 in the case),
byte 6 the two pod levels as nibbles (0–10 = 0–100 %, 0xF unknown), byte 7
charging flags and the case level, byte 8 the lid. For the AirPods Max the
decoders disagree which nibble holds the battery; the plugin takes the
sending device's, as AirPodsDesktop and OpenPods do (Theengs takes the
other).

## Privacy and security

- No Bluetooth address, advert or RSSI of anyone leaves the process or
  reaches the log; the log has states and counts only.
- Adverts that are not yours are dropped on arrival; at most 128 BlueZ
  device paths are remembered (to pair an RSSI update with its advert).
- The settings are not secret (model and threshold) and live in
  `~/.config/blueferry/plugins/io.weirdware.blueferry.airpods/config.json`
  (directory 0700, file 0600).
- Every BlueZ call is asynchronous; nothing blocks the plugin's main loop.
- The D-Bus interface checks the caller's Unix user like every plugin
  method (BlueFerry's SDK), and is rate-limited.

`blueferry-airpods forget` removes the settings.

## Test it

With the plugin installed and AirPods near the computer:

1. `blueferry plugins airpods nearby` with the case open: your model should
   be listed with a strong signal (-40 to -60 dBm).
2. If the AirPods are not paired with this computer, set `model`.
3. The phone card shows the levels; take one pod out, put it back, close
   the lid: the line should follow within seconds.
4. Compare with the iPhone's battery widget (the plugin rounds to 10 %).
5. For the AirPods Max: tell us whether the level matches; the nibble
   choice above is unconfirmed.

## Develop

```sh
python3 -m venv --system-site-packages .venv   # dbus-python, PyGObject from the system
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check . && .venv/bin/python -m pytest -q
```

`blueferry-plugin-api` comes from the `plugin-api` directory of the BlueFerry
repository, the owner-only config file from
[blueferry-plugin-kit](https://github.com/joshii-h/blueferry-plugin-kit).
The tests decode real captures from the Theengs decoder's test suite
(AirPods Pro, Pro 2, Max) and AirPodsDesktop's tests (a newer AirPods model,
encrypted part zeroed), plus adverts written by hand for AirPods 2 and 3
(no public capture found), all in `tests/samples.py` with their source.
BlueZ is a scripted fake system bus (monitor registration, signals, paired
devices, adapter off, BlueZ restart), and the plugin runs through the kit's
fake BlueFerry host, which checks every card reply against the plugin API
spec; the tests also check that no stranger's advert and no address reaches
a reply.

## License

GPL-2.0-or-later, like BlueFerry. See `LICENSE`.

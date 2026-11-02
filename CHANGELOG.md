# Changelog

All notable changes to blueferry-plugin-airpods.

## 0.1.1 (2026-11-02)

- Takes `blueferry-plugin-api` 1.6.0 from the official repository
  (`erikwb/blueferry`) and `blueferry-plugin-kit` 0.4.1. The manifest still
  declares plugin API 1.5, so nothing changes for BlueFerry.

## 0.1.0 (2026-11-02)

First release. Tested with AirPods Pro 2 (USB-C) connected to the iPhone: left, right and case levels, charging and the open lid on the phone card and in the DMS Device Batteries plugin.

- Enabling the plugin again in BlueFerry starts scanning again: a stop
  now takes the monitor objects off the bus.
- Switching Bluetooth off and on keeps the monitor; BlueZ's
  `AlreadyExists` counts as registered. After BlueZ releases the monitor or
  a registration fails, it registers again after 5 s, doubling up to 5 min.
- A repeated `DeviceFound` for a known device costs no D-Bus round trip.
- No scan while there is nothing to look for (nothing paired, no model);
  new state `idle`. `GetBatteries` keeps only a scanning plugin running.
- A powered adapter is preferred over one that is off.
- README: bluetoothd needs `-E` for the monitor; the AirPods 5 ids are
  marked unconfirmed.

## 0.1.0 – 2026-10-31

First release, for plugin API 1.5. Not yet tested with AirPods on real
hardware.

- Reads Apple's Proximity Pairing adverts passively through a BlueZ
  advertisement monitor (only `4C 00 07`); never starts a discovery or
  connects.
- Decodes model, left/right/case battery, charging, in-ear and lid flags,
  including the left/right swap when the right pod sends.
- Shows only your AirPods: resolved to a paired device, or one of your
  models (paired with this computer or picked in the settings) and near
  enough; the strongest wins. Strangers' adverts are dropped on arrival.
- A card line per model in the phone card; settings `model` and `nearby`.
- `io.github.joshii_h.BlueFerryAccessories1` on the plugin's bus name:
  `GetBatteries()` as JSON and the content-free `Changed()` signal.
- `blueferry-airpods nearby`: a short passive scan listing the models heard.
- Apple Watch: not supported (no battery in its adverts, no readable
  Battery Service without being its iPhone).

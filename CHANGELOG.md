# Changelog

All notable changes to blueferry-plugin-airpods.

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

"""Decode Apple "Proximity Pairing" adverts (manufacturer data 0x004C, type 0x07).

AirPods and Beats put their battery levels into this advert in clear text;
only the last 16 bytes are encrypted. ``data`` below is the manufacturer
data after the company id, as BlueZ hands it out in ``ManufacturerData``:

====  ==========================================================
byte  meaning
====  ==========================================================
0     0x07, message type Proximity Pairing
1     0x19, length of what follows (25)
2     0x01 for a paired device (0x00 = pairing mode, other layout)
3-4   model id, little-endian (``0E 20`` = 0x200E, AirPods Pro)
5     status flags, see below
6     battery nibbles of the two pods (0-10 = 0-100 %, 0xF = unknown)
7     high nibble: charging flags, low nibble: case battery
8     lid: bit 3 set = lid closed, bits 0-2 an open counter
9     color
10    connection state / suffix
11+   16 encrypted bytes (need keys only the owner's iCloud devices have)
====  ==========================================================

The pod that sends the advert ("this" pod) is the left one when status bit
0x20 is set; then the low battery nibble and charging bit 0 belong to the
left pod. Without the bit the roles swap ("flipped"). Status bits 0x02 and
0x08 are "this pod in ear" and "other pod in ear", 0x04 "both pods in the
case", 0x40 "this pod in the case".

Sources: AirPodsDesktop ``Source/Core/AppleCP.h`` (SpriteOvO), OpenPods
``PodsStatus.java``, AirStatus ``main.py``, LibrePods
``linux/ble/blemanager.cpp``, furiousMAC ``messages/proximity_pairing.md``,
and Celosia/Cunche, "Discontinued Privacy", PETS 2020 (the case battery is
only sent while the lid is open). The decoders agree on battery, charging
and the flip rule; in-ear and lid flags are less certain (see ``decode``).
"""
from __future__ import annotations

from dataclasses import dataclass

APPLE_COMPANY_ID = 0x004C
PROXIMITY_TYPE = 0x07
PROXIMITY_LENGTH = 0x19
UNKNOWN = 0xF

STATUS_THIS_IN_EAR = 0x02
STATUS_BOTH_IN_CASE = 0x04
STATUS_OTHER_IN_EAR = 0x08
STATUS_THIS_IS_LEFT = 0x20
STATUS_THIS_IN_CASE = 0x40
CHARGING_THIS = 0x1
CHARGING_OTHER = 0x2
CHARGING_CASE = 0x4
LID_CLOSED = 0x08


@dataclass(frozen=True, slots=True)
class Model:
    id: int
    name: str
    choice: str           # the settings word that stands for this model
    single: bool = False  # one battery (over-ear, neckband)


_MODELS = (
    Model(0x2002, "AirPods", "AirPods-1"),
    Model(0x200F, "AirPods 2", "AirPods-2"),
    Model(0x2013, "AirPods 3", "AirPods-3"),
    Model(0x2019, "AirPods 4", "AirPods-4"),
    Model(0x201B, "AirPods 4 (ANC)", "AirPods-4-ANC"),
    # Unconfirmed: only AirPodsDesktop maps these ids to "AirPods 5"
    # (SpriteOvO/AirPodsDesktop commit 71fbb5e, 2026-09-19,
    # Source/Core/AppleCP.cpp, from captured packets); no Apple source and
    # no second decoder confirm them yet.
    Model(0x2030, "AirPods 5", "AirPods-5"),
    Model(0x2036, "AirPods 5", "AirPods-5"),
    Model(0x200E, "AirPods Pro", "AirPods-Pro"),
    Model(0x2014, "AirPods Pro 2", "AirPods-Pro-2"),
    Model(0x2024, "AirPods Pro 2 (USB-C)", "AirPods-Pro-2"),
    Model(0x2027, "AirPods Pro 3", "AirPods-Pro-3"),
    Model(0x200A, "AirPods Max", "AirPods-Max", single=True),
    Model(0x201F, "AirPods Max (USB-C)", "AirPods-Max", single=True),
    Model(0x200B, "Powerbeats Pro", "Powerbeats-Pro"),
    Model(0x2012, "Beats Fit Pro", "Beats-Fit-Pro"),
    Model(0x2010, "Beats Flex", "Beats-Flex", single=True),
    Model(0x2005, "BeatsX", "BeatsX", single=True),
    Model(0x2003, "Powerbeats 3", "Powerbeats-3", single=True),
    Model(0x2006, "Beats Solo 3", "Beats-Solo-3", single=True),
    Model(0x2009, "Beats Studio 3", "Beats-Studio-3", single=True),
)
MODELS: dict[int, Model] = {model.id: model for model in _MODELS}
#: Settings words in a stable order (the manifest's Choices after "paired").
CHOICES: tuple[str, ...] = tuple(dict.fromkeys(model.choice for model in _MODELS))


def choice_models(choice: str) -> frozenset[int]:
    return frozenset(model.id for model in _MODELS if model.choice == choice)


def model_name(model_id: int) -> str:
    model = MODELS.get(model_id)
    return model.name if model else f"Apple 0x{model_id:04x}"


@dataclass(frozen=True, slots=True)
class Battery:
    percent: int | None   # 0-100 in steps of 10; None = unknown
    charging: bool


@dataclass(frozen=True, slots=True)
class Pod(Battery):
    in_ear: bool | None = None


@dataclass(frozen=True, slots=True)
class Proximity:
    model_id: int
    model: Model | None
    left: Pod | None
    right: Pod | None
    case: Battery | None
    lid_open: bool | None
    flipped: bool
    color: int
    status: int


def level(nibble: int) -> int | None:
    """0-10 means 0-100 %; 0xF (and 11-14, never documented) unknown."""
    return nibble * 10 if 0 <= nibble <= 10 else None


def decode(data: bytes) -> Proximity | None:
    """The proximity advert's contents, or None if ``data`` is not one."""
    if len(data) < 11 or data[0] != PROXIMITY_TYPE or data[1] != PROXIMITY_LENGTH:
        return None
    if len(data) < 2 + PROXIMITY_LENGTH or data[2] != 0x01:
        return None   # pairing mode (0x00) uses a different layout
    model_id = data[3] | data[4] << 8
    model = MODELS.get(model_id)
    status, pods, charge_case, lid = data[5], data[6], data[7], data[8]
    flipped = not status & STATUS_THIS_IS_LEFT
    this_level, other_level = level(pods & 0xF), level(pods >> 4)
    charging = charge_case >> 4
    this_charging = bool(charging & CHARGING_THIS)
    other_charging = bool(charging & CHARGING_OTHER)
    # In-ear: AirPodsDesktop's reading (bit 0x02 this pod, 0x08 the other),
    # and like it, a charging pod is never "in ear". LibrePods additionally
    # swaps the two bits while "this pod" is in the case; the decoders
    # disagree there, so in-ear is a hint, not a fact.
    this_in_ear = bool(status & STATUS_THIS_IN_EAR) and not this_charging
    other_in_ear = bool(status & STATUS_OTHER_IN_EAR) and not other_charging
    this = Pod(this_level, this_charging, this_in_ear) if this_level is not None else None
    other = Pod(other_level, other_charging, other_in_ear) if other_level is not None else None
    left, right = (other, this) if flipped else (this, other)
    if model is not None and model.single:
        # One battery: it is the sending device's nibble (AirPodsDesktop's
        # "current", OpenPods' choice for the Max); the other only if that
        # one is unknown. Theengs reads the other nibble; unconfirmed.
        single = this or other
        single = Pod(single.percent, single.charging) if single else None
        left, right = single, None
    case_level = level(charge_case & 0xF)
    case = Battery(case_level, bool(charging & CHARGING_CASE)) if case_level is not None else None
    # The lid bit only means something while a pod sits in the case
    # (LibrePods trusts it only then).
    in_case = status & (STATUS_THIS_IN_CASE | STATUS_BOTH_IN_CASE)
    lid_open = (not lid & LID_CLOSED) if in_case else None
    return Proximity(
        model_id=model_id, model=model, left=left, right=right, case=case,
        lid_open=lid_open, flipped=flipped, color=data[9], status=status,
    )

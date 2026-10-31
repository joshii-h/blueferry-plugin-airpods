"""Decoding real and synthetic Proximity Pairing adverts."""
from __future__ import annotations

import pytest
from samples import AIRPODSDESKTOP, SYNTHETIC, THEENGS, payload

from blueferry_airpods.proximity import (
    CHOICES,
    MODELS,
    Battery,
    Pod,
    choice_models,
    decode,
    level,
    model_name,
)


def levels(hex_text: str):
    p = decode(payload(hex_text))
    assert p is not None
    pct = lambda b: None if b is None else b.percent  # noqa: E731
    return p, (pct(p.left), pct(p.right), pct(p.case))


# Expected (left, right, case) per Theengs' own claims, with its capped
# case 0xF (and pod 0xF) read as unknown.
@pytest.mark.parametrize(("name", "expected"), [
    ("pro_left_50", (50, None, None)),
    ("pro_flipped_right_90", (None, 90, None)),
    ("pro2_left_charging", (100, 100, 30)),
    ("pro2_both_charging_lid_open", (100, 100, 30)),
    ("pro2_right_charging", (100, 100, 30)),
    ("pro2_full_case_unknown", (100, 100, None)),
    ("pro2_left_charging_90", (90, 90, 70)),
    ("pro2_right_charging_left_90", (90, 100, 70)),
    ("pro2_both_charging_left_90", (90, 100, 70)),
    ("pro2_in_case_70", (90, 90, 70)),
    ("pro2_in_case_flipped_60", (90, 90, 60)),
    ("pro2_flipped_right_in_ear", (80, 90, None)),
    ("pro2_left_in_ear", (100, 100, None)),
    ("pro2_both_in_ear", (90, 100, None)),
])
def test_theengs_levels(name: str, expected: tuple) -> None:
    _p, got = levels(THEENGS[name])
    assert got == expected


def test_models_of_real_captures() -> None:
    assert decode(payload(THEENGS["pro_left_50"])).model.name == "AirPods Pro"
    assert decode(payload(THEENGS["pro2_left_charging"])).model.name == "AirPods Pro 2"
    assert decode(payload(THEENGS["max_green"])).model.name == "AirPods Max"
    assert decode(payload(AIRPODSDESKTOP["airpods5_left_sending"])).model_id == 0x2030


@pytest.mark.parametrize(("name", "left", "right", "case"), [
    ("pro2_left_charging", True, False, False),
    ("pro2_right_charging", False, True, False),
    ("pro2_both_charging_lid_open", True, True, False),
    ("pro2_left_charging_90", True, False, False),
    ("pro2_in_case_flipped_60", True, True, False),
])
def test_charging_flags_follow_the_flip(name: str, left: bool, right: bool, case: bool) -> None:
    p = decode(payload(THEENGS[name]))
    assert (p.left.charging, p.right.charging, p.case.charging) == (left, right, case)


def test_flip_bit_swaps_the_nibbles() -> None:
    not_flipped = decode(payload(THEENGS["pro2_right_charging_left_90"]))   # status 0x31
    flipped = decode(payload(THEENGS["pro2_flipped_right_in_ear"]))         # status 0x03
    assert not not_flipped.flipped and flipped.flipped
    # 0xa9: low nibble 9 is the left pod while the left pod sends ...
    assert (not_flipped.left.percent, not_flipped.right.percent) == (90, 100)
    # ... 0x89: and the right pod when the right one sends.
    assert (flipped.left.percent, flipped.right.percent) == (80, 90)


def test_the_same_moment_from_either_pod_decodes_the_same() -> None:
    left = decode(payload(AIRPODSDESKTOP["airpods5_left_sending"]))
    right = decode(payload(AIRPODSDESKTOP["airpods5_right_sending"]))
    assert not left.flipped and right.flipped
    for p in (left, right):
        assert p.left == Pod(100, True, False) and p.right == Pod(100, True, False)
        assert p.case == Battery(100, False)
        assert p.lid_open is True


def test_in_ear_flags() -> None:
    both = decode(payload(THEENGS["pro2_both_in_ear"]))           # status 0x0b, flipped
    assert both.left.in_ear and both.right.in_ear
    one = decode(payload(THEENGS["pro2_flipped_right_in_ear"]))   # status 0x03, flipped
    assert one.right.in_ear and not one.left.in_ear
    left = decode(payload(THEENGS["pro2_left_in_ear"]))           # status 0x23
    assert left.left.in_ear and not left.right.in_ear


def test_charging_pods_are_never_in_ear() -> None:
    p = decode(payload(SYNTHETIC["airpods3_flipped"]))
    assert p.right == Pod(80, True, False)
    assert p.left == Pod(100, False, True)


def test_lid_only_with_a_pod_in_the_case() -> None:
    assert decode(payload(THEENGS["pro2_in_case_70"])).lid_open is True        # 0x51, in case
    assert decode(payload(THEENGS["pro2_left_in_ear"])).lid_open is None       # out of case
    assert decode(payload(THEENGS["pro2_left_charging_90"])).lid_open is True  # 0x40 this in case


def test_max_has_one_battery() -> None:
    p = decode(payload(THEENGS["max_green"]))
    assert p.model.single
    # 0x9a with status bit 0x20: the sending device's (low) nibble. Theengs
    # reads the high one (90 %); see the README.
    assert p.left == Pod(100, False, None) and p.right is None
    assert p.color == 0x11


def test_synthetic_airpods_2() -> None:
    p = decode(payload(SYNTHETIC["airpods2"]))
    assert p.model.name == "AirPods 2"
    assert p.left == Pod(70, False, True) and p.right == Pod(60, False, False)
    assert p.case == Battery(40, False) and p.lid_open is None


def test_synthetic_airpods_3() -> None:
    p = decode(payload(SYNTHETIC["airpods3_flipped"]))
    assert p.model.name == "AirPods 3" and p.flipped
    assert p.case == Battery(50, True) and p.lid_open is True


@pytest.mark.parametrize("data", [
    b"",
    bytes.fromhex("0719"),
    bytes.fromhex("1005031c0d0e36"),                         # Nearby Info, not proximity
    bytes.fromhex("0719000e2022f58f00000a") + bytes(16),     # pairing mode: other layout
    bytes.fromhex("0718010e2022f58f00000a") + bytes(15),     # wrong length byte
    payload(THEENGS["pro_left_50"])[:20],                    # truncated
])
def test_not_a_proximity_advert(data: bytes) -> None:
    assert decode(data) is None


def test_unknown_model_decodes_without_a_model() -> None:
    data = bytearray(payload(THEENGS["pro2_left_in_ear"]))
    data[3:5] = b"\x99\x29"
    p = decode(bytes(data))
    assert p is not None and p.model is None and p.model_id == 0x2999
    assert model_name(0x2999) == "Apple 0x2999"


def test_level_scale() -> None:
    assert [level(n) for n in range(16)] == [
        0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, None, None, None, None, None,
    ]


def test_choices_cover_every_model_and_match_the_manifest() -> None:
    from blueferry.plugin_api.manifest import parse_manifest

    from blueferry_airpods import manifest_text

    assert {m for c in CHOICES for m in choice_models(c)} == set(MODELS)
    assert choice_models("AirPods-Pro-2") == {0x2014, 0x2024}
    field = next(f for f in parse_manifest(manifest_text()).config if f.key == "model")
    assert field.choices == ("paired", *CHOICES)

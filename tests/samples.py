"""Proximity Pairing adverts for the tests, with where each one comes from.

``THEENGS`` are real captures from the Theengs decoder's test suite
(https://github.com/theengs/decoder, ``tests/BLE/test_ble.cpp``): the full
manufacturer data, company id ``4c00`` first. Theengs reads a case nibble
0xF as 150 % and caps it at 100 %; here it is "unknown".

``AIRPODSDESKTOP`` are captures from AirPodsDesktop's
``Tests/AirPodsDomainTests.cpp`` (https://github.com/SpriteOvO/AirPodsDesktop),
with the encrypted part zeroed by its author.

``SYNTHETIC`` adverts were written for these tests from the documented
layout, because no public capture of AirPods 2 or 3 was found. Each one is
labelled as such.
"""
from __future__ import annotations

THEENGS = {
    "pro_left_50": "4c000719010e2022f58f00000a7d9fff27234873d4305e0fed1b39e2b8",
    "pro_flipped_right_90": "4c000719010e2002f98f0000058de7929a3aa09ed6fbb010db935b056c",
    "pro2_left_charging": "4c00071901142011aaa3110004254f0c5375c903064e61be2590411d53",
    "pro2_both_charging_lid_open": "4c00071901142075aab3320004336834816d0f667558352a4e211589aa",
    "pro2_right_charging": "4c00071901142031aaa3120004a1156f4a5481160b323df3f8d892bfc8",
    "pro2_full_case_unknown": "4c00071901142021aa8f12000471436c43b098ff4365eb784afd30c09e",
    "pro2_left_charging_90": "4c0007190114207199973200080c1b53c3031aa10c5da3847e3690f7db",
    "pro2_right_charging_left_90": "4c00071901142031a9a7110004e9d97f8a064c7f4fcc0df5e7aa48708f",
    "pro2_both_charging_left_90": "4c00071901142075a9b732000882b97530ec03edd806dced9d00e6a14f",
    "pro2_in_case_70": "4c0007190114203599b751000444a7139966d773e3e1b642a3be823d75",
    "pro2_in_case_flipped_60": "4c0007190114205599b65100082b939c385b205372f32501b289b844a8",
    "pro2_flipped_right_in_ear": "4c00071901142003898f100008cc74d502b12d6d72e0f4f6eabf826972",
    "pro2_left_in_ear": "4c00071901142023aa8f1100085925754da1d6ab3f80c398447e652875",
    "pro2_both_in_ear": "4c0007190114200b9a8f110004fa242ea8abb9e8f5e57cefb13a3077eb",
    "max_green": "4c000719010a202b9a8f111104fa242ea8abb9e8f5e57cefb13a3077eb",
}

AIRPODSDESKTOP = {
    # Both pods in the case, lid open, the left pod sending.
    "airpods5_left_sending": "0719013020" "35" "aa" "ba" "32" "00" "05" + "00" * 16,
    # The same moment, sent by the right pod (status bit 0x20 clear).
    "airpods5_right_sending": "0719013020" "55" "aa" "ba" "32" "00" "05" + "00" * 16,
}

SYNTHETIC = {
    # SYNTHETIC: AirPods 2 (0x200F), left 70 % in ear, right 60 %, case 40 %.
    "airpods2": "0719010f20" "22" "67" "04" "08" "00" "02" + "11" * 16,
    # SYNTHETIC: AirPods 3 (0x2013), flipped (the right pod sends): right
    # 80 % charging in the case, left 100 % in the ear, case 50 % charging,
    # lid open.
    "airpods3_flipped": "0719011320" "48" "a8" "55" "01" "00" "03" + "22" * 16,
}


def payload(hex_text: str) -> bytes:
    """The bytes BlueZ hands out in ManufacturerData[0x004c]."""
    data = bytes.fromhex(hex_text)
    return data[2:] if data[:2] == b"\x4c\x00" else data

"""Only your AirPods: the privacy rules of the tracker."""
from __future__ import annotations

from fakes import sighting

from blueferry_airpods.matching import STALE, Tracker, wanted_models

PRO2, MAX = 0x2014, 0x200A


def test_wanted_models_are_paired_audio_plus_the_choice() -> None:
    # 0x761B is an iPhone (not in the table): never a wanted model.
    assert wanted_models(frozenset({0x761B, PRO2}), "paired") == {PRO2}
    assert wanted_models(frozenset(), "AirPods-Max") == {MAX, 0x201F}


def test_strangers_models_are_dropped() -> None:
    tracker = Tracker(-70, frozenset({MAX}))
    assert not tracker.feed(sighting("pro2_left_in_ear", rssi=-30))
    assert tracker.readings(100.0) == {}


def test_your_model_far_away_is_dropped() -> None:
    tracker = Tracker(-70, frozenset({PRO2}))
    assert not tracker.feed(sighting(rssi=-71))
    assert not tracker.feed(sighting(rssi=None))
    assert tracker.feed(sighting(rssi=-70))
    assert set(tracker.readings(100.0)) == {PRO2}


def test_resolved_paired_adverts_count_at_any_distance() -> None:
    tracker = Tracker(-50, frozenset())
    assert tracker.feed(sighting(rssi=-95, paired=True))
    assert tracker.readings(100.0)[PRO2].resolved


def test_the_strongest_of_the_window_wins() -> None:
    tracker = Tracker(-80, frozenset({PRO2}))
    tracker.feed(sighting("pro2_left_in_ear", path="/a", rssi=-75, at=100.0))
    tracker.feed(sighting("pro2_both_in_ear", path="/b", rssi=-45, at=101.0))
    tracker.feed(sighting("pro2_left_in_ear", path="/a", rssi=-74, at=102.0))
    best = tracker.readings(102.0)[PRO2]
    assert best.path == "/b" and best.proximity.left.percent == 90
    # /b goes quiet; after the window the one still sending wins.
    tracker.feed(sighting("pro2_left_in_ear", path="/a", rssi=-74, at=130.0))
    assert tracker.readings(130.0)[PRO2].path == "/a"


def test_a_resolved_advert_beats_a_stronger_unresolved_one() -> None:
    tracker = Tracker(-80, frozenset({PRO2}))
    tracker.feed(sighting("pro2_both_in_ear", path="/near", rssi=-40, at=100.0))
    tracker.feed(sighting("pro2_left_in_ear", path="/mine", rssi=-79, paired=True, at=100.0))
    assert tracker.readings(100.0)[PRO2].path == "/mine"


def test_readings_expire() -> None:
    tracker = Tracker(-70, frozenset({PRO2}))
    tracker.feed(sighting(at=100.0))
    assert PRO2 in tracker.readings(100.0 + STALE)
    tracker.expire(101.0 + STALE)
    assert tracker.readings(101.0 + STALE) == {}


def test_summary_changes_with_levels_and_freshness_only() -> None:
    tracker = Tracker(-70, frozenset({PRO2}))
    tracker.feed(sighting(rssi=-60, at=100.0))
    first = tracker.summary(100.0)
    tracker.feed(sighting(rssi=-55, at=105.0))          # only RSSI differs
    assert tracker.summary(105.0) == first
    assert tracker.summary(300.0) != first              # now "n min ago"
    tracker.feed(sighting("pro2_both_in_ear", rssi=-55, at=301.0))
    assert tracker.summary(301.0) != first


def test_configure_drops_models_no_longer_wanted() -> None:
    tracker = Tracker(-70, frozenset({PRO2}))
    tracker.feed(sighting(path="/a", at=100.0))
    tracker.feed(sighting(path="/b", paired=True, at=100.0))
    tracker.configure(nearby=-70, wanted=frozenset())
    # The resolved one stays: it is paired with this computer.
    assert tracker.readings(100.0)[PRO2].path == "/b"
    tracker.configure(nearby=-70, wanted=frozenset())
    assert set(tracker.readings(100.0)) == {PRO2}

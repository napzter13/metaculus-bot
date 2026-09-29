"""Slot arithmetic for the Kira scheduler: the UTC minutes each workflow fires, and catch-up boundaries."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta, timezone

import pytest

from kira_scheduler.slots import latest_slot, next_slot
from kira_scheduler.spec import WORKFLOWS

TOURNAMENT = (3, 23, 43)
MINIBENCH = (8, 38)
MANTIC = (5, 15, 25)


def _t(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 9, 29, hour, minute, second, tzinfo=UTC)


def test_the_workflows_carry_the_documented_slots() -> None:
    by_name = {wf.name: wf.slots for wf in WORKFLOWS}
    assert by_name == {"tournament": TOURNAMENT, "minibench": MINIBENCH, "mantic": MANTIC}


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_t(10, 3), _t(10, 3)),  # exactly on a slot: that slot is the latest, so it is due
        (_t(10, 2, 59), _t(9, 43)),  # one second before: still the previous hour's last slot
        (_t(10, 14), _t(10, 3)),
        (_t(10, 23), _t(10, 23)),
        (_t(10, 59, 59), _t(10, 43)),
        (_t(0, 0), datetime(2026, 9, 28, 23, 43, tzinfo=UTC)),  # midnight looks back across the day
    ],
)
def test_latest_slot_tournament(now: datetime, expected: datetime) -> None:
    assert latest_slot(TOURNAMENT, now) == expected


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_t(10, 3), _t(10, 23)),  # strictly after: a slot in progress is not "next"
        (_t(10, 2, 59), _t(10, 3)),
        (_t(10, 43), _t(11, 3)),  # rolls into the next hour
        (_t(23, 50), datetime(2026, 9, 30, 0, 3, tzinfo=UTC)),  # and across midnight
    ],
)
def test_next_slot_tournament(now: datetime, expected: datetime) -> None:
    assert next_slot(TOURNAMENT, now) == expected


def test_minibench_and_mantic_edges() -> None:
    assert latest_slot(MINIBENCH, _t(10, 7, 59)) == _t(9, 38)
    assert next_slot(MINIBENCH, _t(10, 38)) == _t(11, 8)
    assert latest_slot(MANTIC, _t(10, 26)) == _t(10, 25)
    assert next_slot(MANTIC, _t(10, 25)) == _t(11, 5)


def test_a_non_utc_clock_is_read_as_the_same_instant() -> None:
    plus_two = timezone(timedelta(hours=2))
    assert latest_slot(TOURNAMENT, datetime(2026, 9, 29, 12, 14, tzinfo=plus_two)) == _t(10, 3)
    assert next_slot(TOURNAMENT, datetime(2026, 9, 29, 12, 14, tzinfo=plus_two)) == _t(10, 23)


def test_slots_never_share_a_minute_across_workflows() -> None:
    minutes = [m for wf in WORKFLOWS for m in wf.slots]
    assert len(minutes) == len(set(minutes))


@pytest.fixture
def stockholm(monkeypatch: pytest.MonkeyPatch):
    """Process-wide TZ=Europe/Stockholm, as kira-earn sets it, restored afterwards."""
    monkeypatch.setenv("TZ", "Europe/Stockholm")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


class TestSlotsAreUtcWhateverTheContainerTimezoneIs:
    def test_the_fixture_really_moves_local_time(self, stockholm: None) -> None:
        # Guard: if this passes vacuously the tests below prove nothing.
        assert time.localtime(datetime(2026, 9, 29, 10, 14, tzinfo=UTC).timestamp()).tm_hour == 12  # CEST, UTC+2
        assert time.localtime(datetime(2026, 12, 15, 10, 14, tzinfo=UTC).timestamp()).tm_hour == 11  # CET, UTC+1

    @pytest.mark.parametrize("day", [datetime(2026, 9, 29, tzinfo=UTC), datetime(2026, 12, 15, tzinfo=UTC)])
    def test_slots_are_the_same_minutes_of_the_utc_hour_in_summer_and_winter(
        self, stockholm: None, day: datetime
    ) -> None:
        now = day.replace(hour=10, minute=14)
        assert latest_slot(TOURNAMENT, now) == day.replace(hour=10, minute=3)
        assert next_slot(TOURNAMENT, now) == day.replace(hour=10, minute=23)
        assert latest_slot(MINIBENCH, now) == day.replace(hour=10, minute=8)
        assert next_slot(MANTIC, now) == day.replace(hour=10, minute=15)

    def test_a_naive_datetime_is_refused_instead_of_read_as_local_time(self, stockholm: None) -> None:
        naive = datetime(2026, 9, 29, 10, 14)
        with pytest.raises(ValueError, match="timezone-aware"):
            latest_slot(TOURNAMENT, naive)
        with pytest.raises(ValueError, match="timezone-aware"):
            next_slot(TOURNAMENT, naive)

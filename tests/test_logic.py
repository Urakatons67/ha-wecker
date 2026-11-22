"""Tests der reinen Logik (Zeitplan, Kalendertage, Datenformat).

Ausführen in einer Umgebung mit Home Assistant:
  PYTHONPATH=. python3 -m pytest tests -q     (oder: python3 tests/test_logic.py)
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.wecker.schedule import event_dates, next_occurrence  # noqa: E402
from custom_components.wecker.store import normalize  # noqa: E402

TZ = ZoneInfo("Europe/Berlin")


def A(**kw):
    a = {"time": "06:30", "schedule": {"mode": "days", "days": [0, 1, 2, 3, 4], "date": ""}, "skip_dates": []}
    a.update(kw)
    return a


def test_days_same_day_and_next():
    now = datetime(2026, 11, 23, 6, 0, tzinfo=TZ)  # Montag
    assert next_occurrence(A(), now) == datetime(2026, 11, 23, 6, 30, tzinfo=TZ)
    now = datetime(2026, 11, 23, 6, 30, tzinfo=TZ)  # genau zur Weckzeit → nächster Tag
    assert next_occurrence(A(), now) == datetime(2026, 11, 24, 6, 30, tzinfo=TZ)


def test_weekend_skipped():
    now = datetime(2026, 11, 27, 7, 0, tzinfo=TZ)  # Freitag nach Weckzeit
    assert next_occurrence(A(), now).date() == date(2026, 11, 30)


def test_workdays_with_holiday():
    now = datetime(2026, 12, 24, 7, 0, tzinfo=TZ)
    holidays = {date(2026, 12, 25), date(2026, 12, 26)}
    wd = lambda d: d.weekday() < 5 and d not in holidays  # noqa: E731
    a = A(schedule={"mode": "workdays", "days": [], "date": ""})
    assert next_occurrence(a, now, wd).date() == date(2026, 12, 28)


def test_once_and_past_once():
    now = datetime(2026, 11, 22, 20, 0, tzinfo=TZ)
    a = A(schedule={"mode": "once", "days": [], "date": "2026-11-25"})
    assert next_occurrence(a, now) == datetime(2026, 11, 25, 6, 30, tzinfo=TZ)
    a = A(schedule={"mode": "once", "days": [], "date": "2026-11-21"})
    assert next_occurrence(a, now) is None


def test_skip_and_blocked():
    now = datetime(2026, 11, 22, 20, 0, tzinfo=TZ)  # Sonntag
    a = A(skip_dates=["2026-11-23"])
    assert next_occurrence(a, now).date() == date(2026, 11, 24)
    assert next_occurrence(a, now, ignore_skip=True).date() == date(2026, 11, 23)
    assert next_occurrence(A(), now, blocked={"2026-11-23", "2026-11-24"}).date() == date(2026, 11, 25)


def test_dst_change():
    now = datetime(2027, 3, 27, 12, 0, tzinfo=TZ)  # Sa vor Zeitumstellung
    a = A(schedule={"mode": "days", "days": [6], "date": ""})
    nxt = next_occurrence(a, now)
    assert nxt.hour == 6 and nxt.minute == 30 and nxt.utcoffset().total_seconds() == 7200


def test_event_dates():
    assert event_dates("2026-12-21", "2027-01-07") == {f"2026-12-{d}" for d in range(21, 32)} | {f"2027-01-0{d}" for d in range(1, 7)}
    assert event_dates("2026-11-23", "2026-11-24") == {"2026-11-23"}
    assert event_dates("2026-11-23T22:00:00+01:00", "2026-11-24T00:00:00+01:00") == {"2026-11-23"}
    assert event_dates("2026-11-23T22:00:00+01:00", "2026-11-24T02:00:00+01:00") == {"2026-11-23", "2026-11-24"}






def test_normalize_keeps_existing_data():
    raw = {"profiles": {"p1": {"name": "A", "user_id": "u", "settings": {"player": "media_player.x"},
                               "alarms": {"a1": {"name": "W", "time": "06:00", "source": "alt:a:1",
                                                 "sound": {"station_id": "s1"}}}}},
           "stations": [{"id": "s1", "name": "S", "alexa": "S", "media_id": ""}], "parents": []}
    d = normalize(raw)
    a = d["profiles"]["p1"]["alarms"]["a1"]
    assert a["source"] == "alt:a:1" and a["sound"]["station_id"] == "s1" and a["sound"]["duration_min"] == 60
    assert d["profiles"]["p1"]["settings"]["player"] == "media_player.x" and d["profiles"]["p1"]["settings"]["snooze_min"] == 9
    assert d["stations"][0]["id"] == "s1"


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
    print(f"{n} Tests ok")

"""Zeitplan-Berechnung (reine Funktionen ohne Home Assistant, damit testbar).

Ein Wecker hat eine Uhrzeit und einen Zeitplan:
  mode "days"      bestimmte Wochentage (days: 0 = Montag … 6 = Sonntag)
  mode "workdays"  nur an Werktagen (Auskunft liefert der Werktag-Sensor)
  mode "once"      einmalig am Datum date
Ausnahmen: skip_dates (einzelne Tage überspringen) und gesperrte Tage aus Kalendern.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, datetime, time, timedelta

HORIZON_DAYS = 400


def parse_time(value: str) -> time:
    hh, mm = (int(x) for x in value.split(":")[:2])
    return time(hh, mm)


def default_workday(d: date) -> bool:
    return d.weekday() < 5


def matches_day(alarm: dict, d: date, is_workday: Callable[[date], bool] = default_workday) -> bool:
    sch = alarm.get("schedule") or {}
    mode = sch.get("mode", "days")
    if mode == "once":
        return d.isoformat() == sch.get("date")
    if mode == "workdays":
        return is_workday(d)
    return d.weekday() in (sch.get("days") or [])


def next_occurrence(
    alarm: dict,
    now: datetime,
    is_workday: Callable[[date], bool] = default_workday,
    blocked: Iterable[str] = (),
    ignore_skip: bool = False,
) -> datetime | None:
    """Nächster Weckzeitpunkt nach now (now ist zeitzonenbehaftet, Ortszeit)."""
    try:
        at = parse_time(alarm["time"])
    except (KeyError, ValueError):
        return None
    sch = alarm.get("schedule") or {}
    once = sch.get("mode") == "once"
    if once:
        try:
            last = date.fromisoformat(sch.get("date") or "")
        except ValueError:
            return None
    skip = set() if ignore_skip else set(alarm.get("skip_dates") or [])
    blocked = set(blocked)
    for i in range(HORIZON_DAYS):
        d = now.date() + timedelta(days=i)
        if once and d > last:
            return None
        when = datetime.combine(d, at, tzinfo=now.tzinfo)
        if when <= now:
            continue
        iso = d.isoformat()
        if iso in skip or iso in blocked:
            continue
        if matches_day(alarm, d, is_workday):
            return when
    return None


def event_dates(start: str, end: str | None) -> set[str]:
    """Kalendertage, die ein Termin belegt (ganztägig: Ende ist exklusiv)."""
    def _parse(v: str):
        if len(v) == 10:
            return date.fromisoformat(v), True
        dtv = datetime.fromisoformat(v)
        return dtv, False

    s, s_allday = _parse(start)
    e, _ = _parse(end) if end else (s, s_allday)
    if s_allday:
        first, last = s, (e - timedelta(days=1)) if e > s else s
    else:
        first = s.date()
        # Endet ein Termin genau um Mitternacht, belegt er den Folgetag nicht
        last = (e - timedelta(seconds=1)).date() if e > s else s.date()
    out = set()
    d = first
    while d <= last and len(out) < 400:
        out.add(d.isoformat())
        d += timedelta(days=1)
    return out

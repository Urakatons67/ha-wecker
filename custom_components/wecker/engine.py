"""Zeitsteuerung und Ablauf beim Klingeln.

Für jeden aktiven Wecker wird der nächste Zeitpunkt T berechnet und es werden
Zeitgeber gesetzt:
  T - pre.minutes    Ereignis wecker_vorlauf (z. B. Bad vorheizen, Kaffee)
  T - light.minutes  Sonnenaufgang: Licht langsam heller
  T                  Klingeln: Rollläden, Skript, Ton (mit Lautstärke-Anstieg), Push
Beim Klingeln: Stopp oder Schlummern über Dienst, Taste, Push-Aktion oder Seite.
Nach duration_min endet der Ton automatisch.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_point_in_time
from homeassistant.util import dt as dt_util

from .const import (
    ACTION_SNOOZE, ACTION_STOP, DEFAULT_ALEXA_TEMPLATE, EVENT_END, EVENT_PRE, EVENT_RING,
    EVENT_SNOOZE, SIGNAL_UPDATE,
)
from .schedule import default_workday, event_dates, next_occurrence

_LOGGER = logging.getLogger(__name__)

WORKDAY_LOOKAHEAD = 14   # Tage, für die der Werktag-Sensor gefragt wird (danach Mo–Fr)
CALENDAR_LOOKAHEAD = 60  # Tage, für die Kalender-Ausnahmen gelesen werden
CALENDAR_MAX_AGE = timedelta(hours=1)
RAMP_STEP_S = 20


@dataclass
class Ring:
    pid: str
    aid: str
    test: bool = False
    status: str = "klingelt"          # klingelt | schlummert
    started: datetime = field(default_factory=dt_util.now)
    snoozes: int = 0
    player: str | None = None
    unsubs: list = field(default_factory=list)
    tasks: list = field(default_factory=list)

    def cancel(self) -> None:
        for u in self.unsubs:
            u()
        self.unsubs.clear()
        for t in self.tasks:
            t.cancel()
        self.tasks.clear()


class Engine:
    def __init__(self, hass: HomeAssistant, store) -> None:
        self.hass = hass
        self.store = store
        self.alarm_next: dict[tuple[str, str], datetime] = {}
        self.ringing: dict[str, Ring] = {}
        self._timers: list = []
        self._done: set[tuple] = set()
        self._wd_cache: dict[tuple[str, str], bool] = {}
        self._cal_cache: dict[str, tuple[datetime, set[str]]] = {}
        self._lock = asyncio.Lock()
        self._pending: asyncio.Task | None = None

    # ---------- Planung ----------
    @callback
    def request_reschedule(self) -> None:
        if self._pending and not self._pending.done():
            return
        self._pending = self.hass.async_create_background_task(self.async_reschedule(), "wecker_planen")

    @callback
    def clear_caches(self) -> None:
        self._wd_cache.clear()
        self._cal_cache.clear()

    async def async_reschedule(self) -> None:
        async with self._lock:
            await asyncio.sleep(0.2)  # mehrere Änderungen zusammenfassen
            self._pending = None
            for u in self._timers:
                u()
            self._timers.clear()
            self.alarm_next.clear()
            now = dt_util.now()
            today = now.date().isoformat()
            self._done = {d for d in self._done if d[3] >= now - timedelta(days=1)}
            changed = False
            for pid, prof in self.store.data["profiles"].items():
                st = prof["settings"]
                for aid, alarm in prof["alarms"].items():
                    old = alarm.get("skip_dates") or []
                    keep = [d for d in old if d >= today]
                    if keep != old:
                        alarm["skip_dates"] = keep
                        changed = True
                    if not (st.get("enabled", True) and alarm.get("enabled")):
                        continue
                    when = await self.async_next(pid, alarm, now)
                    if when is None:
                        continue
                    self.alarm_next[(pid, aid)] = when
                    self._plan(pid, aid, alarm, when, now)
            if changed:
                self.store.save()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    def _plan(self, pid: str, aid: str, alarm: dict, when: datetime, now: datetime) -> None:
        jobs = [("ring", when)]
        if alarm["light"].get("enabled") and alarm["light"].get("entities"):
            jobs.append(("light", when - timedelta(minutes=int(alarm["light"].get("minutes") or 0))))
        if alarm["pre"].get("enabled"):
            jobs.append(("pre", when - timedelta(minutes=int(alarm["pre"].get("minutes") or 0))))
        for kind, at in jobs:
            key = (kind, pid, aid, when)
            if key in self._done:
                continue
            at = max(at, now + timedelta(seconds=1))

            @callback
            def _fire(_now, kind=kind, key=key) -> None:
                if key in self._done:
                    return
                self._done.add(key)
                self.hass.async_create_background_task(self._run(kind, pid, aid), f"wecker_{kind}_{aid}")

            self._timers.append(async_track_point_in_time(self.hass, _fire, at))

    async def _run(self, kind: str, pid: str, aid: str) -> None:
        prof = self.store.data["profiles"].get(pid)
        alarm = prof and prof["alarms"].get(aid)
        if not alarm:
            return
        try:
            if kind == "pre":
                self.hass.bus.async_fire(EVENT_PRE, self._event_data(pid, aid))
            elif kind == "light":
                await self._sunrise(alarm["light"])
            else:
                if (alarm.get("schedule") or {}).get("mode") == "once":
                    alarm["enabled"] = False
                    self.store.save()
                await self.async_ring(pid, aid)
        except Exception:  # noqa: BLE001 – ein Fehler darf die Planung nicht stoppen
            _LOGGER.exception("Wecker %s/%s: Fehler bei %s", pid, aid, kind)
        if kind == "ring":
            self.request_reschedule()

    async def async_next(self, pid: str, alarm: dict, now: datetime | None = None,
                         ignore_skip: bool = False) -> datetime | None:
        now = now or dt_util.now()
        prof = self.store.data["profiles"][pid]
        st = prof["settings"]
        workday = await self._workday_fn(st, alarm, now.date())
        blocked: set[str] = set()
        if alarm.get("use_calendars") and st.get("calendars"):
            for cal in st["calendars"]:
                blocked |= await self._calendar_dates(cal, now)
        return next_occurrence(alarm, now, workday, blocked, ignore_skip=ignore_skip)

    async def _workday_fn(self, st: dict, alarm: dict, today: date):
        if (alarm.get("schedule") or {}).get("mode") != "workdays":
            return default_workday
        ent = st.get("workday_entity") or self._first_workday_entity()
        if not ent:
            return default_workday
        known: dict[str, bool] = {}
        for i in range(WORKDAY_LOOKAHEAD):
            d = (today + timedelta(days=i)).isoformat()
            key = (ent, d)
            if key not in self._wd_cache:
                try:
                    resp = await self.hass.services.async_call(
                        "workday", "check_date", {"check_date": d}, target={"entity_id": ent},
                        blocking=True, return_response=True)
                    self._wd_cache[key] = bool((resp or {}).get(ent, {}).get("workday"))
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Werktag-Abfrage %s fehlgeschlagen: %s", ent, err)
                    break
            known[d] = self._wd_cache[key]
        return lambda d: known.get(d.isoformat(), default_workday(d))

    def _first_workday_entity(self) -> str | None:
        reg = er.async_get(self.hass)
        return next((e.entity_id for e in reg.entities.values()
                     if e.platform == "workday" and e.domain == "binary_sensor" and not e.disabled), None)

    async def _calendar_dates(self, cal: str, now: datetime) -> set[str]:
        hit = self._cal_cache.get(cal)
        if hit and now - hit[0] < CALENDAR_MAX_AGE:
            return hit[1]
        dates: set[str] = set()
        start = dt_util.start_of_local_day(now)
        try:
            resp = await self.hass.services.async_call(
                "calendar", "get_events",
                {"start_date_time": start.isoformat(),
                 "end_date_time": (start + timedelta(days=CALENDAR_LOOKAHEAD)).isoformat()},
                target={"entity_id": cal}, blocking=True, return_response=True)
            for ev in (resp or {}).get(cal, {}).get("events", []):
                dates |= event_dates(str(ev.get("start")), ev.get("end") and str(ev.get("end")))
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Kalender %s nicht lesbar: %s", cal, err)
        self._cal_cache[cal] = (now, dates)
        return dates

    def profile_next(self, pid: str) -> tuple[datetime, str] | None:
        best = None
        for (p, aid), when in self.alarm_next.items():
            if p == pid and (best is None or when < best[0]):
                best = (when, aid)
        return best

    # ---------- Klingeln ----------
    def _event_data(self, pid: str, aid: str, **extra) -> dict:
        prof = self.store.data["profiles"].get(pid, {})
        alarm = prof.get("alarms", {}).get(aid, {})
        return {"profil": pid, "name": prof.get("name", ""), "wecker_id": aid,
                "wecker": alarm.get("name", ""), **extra}

    async def async_ring(self, pid: str, aid: str, test: bool = False, snoozes: int = 0) -> None:
        prof = self.store.data["profiles"].get(pid)
        alarm = prof and prof["alarms"].get(aid)
        if not alarm:
            return
        st = prof["settings"]
        old = self.ringing.pop(pid, None)
        if old:
            old.cancel()
        ring = Ring(pid=pid, aid=aid, test=test, snoozes=snoozes)
        self.ringing[pid] = ring
        self.hass.bus.async_fire(EVENT_RING, self._event_data(pid, aid, test=test, schlummer=snoozes))

        if snoozes == 0 and not test:  # Test: nur Ton und Push, keine Rollläden/Skripte
            await self._covers(alarm["covers"])
            scr = alarm["script"]
            if scr.get("enabled") and scr.get("entity"):
                await self._call("script", "turn_on", {}, scr["entity"])

        problem = await self._sound(ring, alarm, st)
        if st.get("push", True) or problem:
            await self._push(pid, aid, problem)

        minutes = int(alarm["sound"].get("duration_min") or 60)

        @callback
        def _end(_now) -> None:
            self.hass.async_create_task(self.async_stop(pid, "zeit"))

        ring.unsubs.append(async_call_later(self.hass, minutes * 60, _end))
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    async def async_stop(self, pid: str | None = None, reason: str = "manuell") -> int:
        pids = [pid] if pid else list(self.ringing)
        n = 0
        for p in pids:
            ring = self.ringing.pop(p, None)
            if not ring:
                continue
            ring.cancel()
            if ring.status == "klingelt":
                await self._silence(ring)
            await self._clear_push(p)
            self.hass.bus.async_fire(EVENT_END, self._event_data(p, ring.aid, grund=reason, test=ring.test))
            n += 1
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
        return n

    async def async_snooze(self, pid: str | None = None) -> int:
        pids = [pid] if pid else [p for p, r in self.ringing.items() if r.status == "klingelt"]
        n = 0
        for p in pids:
            ring = self.ringing.get(p)
            if not ring or ring.status != "klingelt":
                continue
            prof = self.store.data["profiles"].get(p)
            if not prof:
                continue
            ring.cancel()
            await self._silence(ring)
            ring.status = "schlummert"
            minutes = int(prof["settings"].get("snooze_min") or 9)
            self.hass.bus.async_fire(EVENT_SNOOZE, self._event_data(p, ring.aid, minuten=minutes))

            @callback
            def _again(_now, p=p, aid=ring.aid, test=ring.test, sn=ring.snoozes + 1) -> None:
                self.hass.async_create_task(self.async_ring(p, aid, test=test, snoozes=sn))

            ring.unsubs.append(async_call_later(self.hass, minutes * 60, _again))
            n += 1
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
        return n

    @callback
    def handle_push_action(self, event) -> None:
        action = str(event.data.get("action") or "")
        for prefix, fn in ((ACTION_STOP + "_", self.async_stop), (ACTION_SNOOZE + "_", self.async_snooze)):
            if action.startswith(prefix):
                self.hass.async_create_task(fn(action[len(prefix):]))

    async def async_shutdown(self) -> None:
        for u in self._timers:
            u()
        self._timers.clear()
        for ring in self.ringing.values():
            ring.cancel()
        self.ringing.clear()

    # ---------- Bausteine ----------
    async def _call(self, domain: str, service: str, data: dict, entity_id=None) -> bool:
        try:
            await self.hass.services.async_call(
                domain, service, data, target={"entity_id": entity_id} if entity_id else None, blocking=True)
            return True
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Wecker: %s.%s für %s fehlgeschlagen: %s", domain, service, entity_id, err)
            return False

    async def _covers(self, cfg: dict) -> None:
        ents = cfg.get("entities") or []
        if not (cfg.get("enabled") and ents):
            return
        pos = int(cfg.get("position", 100))
        if pos >= 100:
            await self._call("cover", "open_cover", {}, ents)
        else:
            await self._call("cover", "set_cover_position", {"position": pos}, ents)

    async def _sunrise(self, cfg: dict) -> None:
        ents = cfg.get("entities") or []
        if not ents:
            return
        kelvin = int(cfg.get("kelvin") or 0)
        base = {"color_temp_kelvin": kelvin} if kelvin else {}
        await self._call("light", "turn_on", {**base, "brightness_pct": 1}, ents)
        await asyncio.sleep(2)
        await self._call("light", "turn_on", {
            **base, "brightness_pct": int(cfg.get("brightness") or 100),
            "transition": max(1, int(cfg.get("minutes") or 0) * 60)}, ents)

    def station(self, sid: str | None) -> dict | None:
        stations = self.store.data.get("stations") or []
        return next((s for s in stations if s.get("id") == sid), stations[0] if stations else None)

    def player_platform(self, entity_id: str) -> str:
        ent = er.async_get(self.hass).async_get(entity_id)
        return ent.platform if ent else ""

    async def _sound(self, ring: Ring, alarm: dict, st: dict) -> str | None:
        """Ton starten. Gibt eine Problembeschreibung zurück, wenn es nicht ging."""
        snd = alarm["sound"]
        if not snd.get("enabled"):
            return None
        player = snd.get("player") or st.get("player")
        if not player:
            return "Kein Lautsprecher eingestellt"
        state = self.hass.states.get(player)
        if state is None or state.state in ("unavailable", "unknown"):
            return f"Lautsprecher {state.name if state else player} ist nicht erreichbar"
        station = self.station(snd.get("station_id"))
        if not station:
            return "Kein Sender eingestellt"
        v0, v1 = snd.get("volume_start"), snd.get("volume_end")
        if v0 is not None:
            await self._call("media_player", "volume_set", {"volume_level": int(v0) / 100}, player)
        platform = self.player_platform(player)
        if platform == "alexa_media" or (station.get("alexa") and not station.get("media_id")):
            tpl = self.store.data.get("alexa_template") or DEFAULT_ALEXA_TEMPLATE
            text = tpl.replace("{sender}", station.get("alexa") or station.get("name", ""))
            ok = await self._call("media_player", "play_media",
                                  {"media_content_id": text, "media_content_type": "custom"}, player)
        elif not station.get("media_id"):
            return f"Sender {station.get('name')} hat keine Adresse für diesen Lautsprecher"
        elif platform == "music_assistant":
            ok = await self._call("music_assistant", "play_media",
                                  {"media_id": station["media_id"], "media_type": "radio"}, player)
        else:
            ok = await self._call("media_player", "play_media",
                                  {"media_content_id": station["media_id"], "media_content_type": "music"}, player)
        if not ok:
            return f"Wiedergabe auf {state.name} fehlgeschlagen"
        ring.player = player
        ramp = int(snd.get("ramp_min") or 0)
        if v0 is not None and v1 is not None and ramp > 0 and int(v1) != int(v0):
            ring.tasks.append(self.hass.async_create_background_task(
                self._ramp(player, int(v0), int(v1), ramp), f"wecker_rampe_{ring.pid}"))
        return None

    async def _ramp(self, player: str, v0: int, v1: int, minutes: int) -> None:
        steps = max(1, minutes * 60 // RAMP_STEP_S)
        for i in range(1, steps + 1):
            await asyncio.sleep(RAMP_STEP_S)
            vol = v0 + (v1 - v0) * i / steps
            await self._call("media_player", "volume_set", {"volume_level": round(vol) / 100}, player)

    async def _silence(self, ring: Ring) -> None:
        if not ring.player:
            return
        state = self.hass.states.get(ring.player)
        feats = int(state.attributes.get("supported_features", 0)) if state else 0
        STOP, PAUSE = 4096, 1  # MediaPlayerEntityFeature.STOP / PAUSE
        if feats & STOP:
            await self._call("media_player", "media_stop", {}, ring.player)
        elif feats & PAUSE:
            await self._call("media_player", "media_pause", {}, ring.player)
        ring.player = None

    def _notify_targets(self, pid: str) -> list[str]:
        prof = self.store.data["profiles"].get(pid) or {}
        out = []
        for s in prof.get("settings", {}).get("notify") or []:
            name = s.split(".", 1)[1] if s.startswith("notify.") else s
            if self.hass.services.has_service("notify", name):
                out.append(name)
        return out

    async def _push(self, pid: str, aid: str, problem: str | None) -> None:
        prof = self.store.data["profiles"][pid]
        alarm = prof["alarms"][aid]
        if problem and not prof["settings"].get("fallback_push", True) and not prof["settings"].get("push", True):
            return
        msg = f"{alarm.get('name') or 'Wecker'} · {dt_util.now().strftime('%H:%M')}"
        if problem:
            msg += f"\n⚠️ {problem}"
        data = {
            "tag": f"wecker_{pid}",
            "actions": [{"action": f"{ACTION_STOP}_{pid}", "title": "Stopp"},
                        {"action": f"{ACTION_SNOOZE}_{pid}", "title": "Schlummern"}],
            "push": {"interruption-level": "time-sensitive", "sound": "default"},
            "priority": "high", "ttl": 0,
        }
        for name in self._notify_targets(pid):
            await self._call("notify", name, {"title": "⏰ Wecker", "message": msg, "data": data})

    async def _clear_push(self, pid: str) -> None:
        for name in self._notify_targets(pid):
            if name.startswith("mobile_app_"):
                await self._call("notify", name, {"message": "clear_notification", "data": {"tag": f"wecker_{pid}"}})

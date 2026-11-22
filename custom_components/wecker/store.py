"""Datenhaltung in .storage/wecker.

  profiles: {profil_id: {
      name, user_id,                       Person und zugehöriger HA-Benutzer
      settings: {enabled, player, notify, push, fallback_push, snooze_min,
                 calendars, workday_entity}
      alarms:   {wecker_id: Wecker (siehe DEFAULT_ALARM)}
  }}
  parents:        [HA-Benutzer-IDs]  sehen und bearbeiten alle Profile
  stations:       [{id, name, alexa, media_id}]  Sender/Töne für alle
  alexa_template: Sprachbefehl für Alexa-Lautsprecher, {sender} wird ersetzt
"""
from __future__ import annotations

import copy
import uuid

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DEFAULT_ALEXA_TEMPLATE, DOMAIN

DEFAULT_SETTINGS = {
    "enabled": True,          # Hauptschalter der Person (z. B. Urlaub)
    "player": "",             # Standard-Lautsprecher
    "notify": [],             # notify-Dienste für Push (z. B. notify.mobile_app_…)
    "push": True,             # beim Klingeln Push mit Stopp/Schlummern
    "fallback_push": True,    # Push, wenn der Lautsprecher nicht erreichbar ist
    "snooze_min": 9,
    "calendars": [],          # Ausnahme-Kalender: an belegten Tagen kein Wecker
    "workday_entity": "",     # Werktag-Sensor (leer = erster gefundener)
}

DEFAULT_ALARM = {
    "name": "Wecker",
    "enabled": True,
    "time": "06:30",
    "schedule": {"mode": "workdays", "days": [0, 1, 2, 3, 4], "date": ""},
    "use_calendars": True,
    "skip_dates": [],
    "sound": {"enabled": True, "station_id": "", "player": "", "volume_start": None,
              "volume_end": None, "ramp_min": 0, "duration_min": 60},
    "light": {"enabled": False, "entities": [], "minutes": 15, "brightness": 100, "kelvin": 2700},
    "covers": {"enabled": False, "entities": [], "position": 100},
    "script": {"enabled": False, "entity": ""},
    "pre": {"enabled": False, "minutes": 30},
    "source": "",             # Herkunft bei Übernahme (z. B. alt:person:1)
}

EMPTY = {"profiles": {}, "parents": [], "stations": [], "alexa_template": DEFAULT_ALEXA_TEMPLATE}


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def norm_alarm(a: dict) -> dict:
    out = copy.deepcopy(DEFAULT_ALARM)
    for k, v in (a or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k].update(v)
        elif k in out:
            out[k] = v
    return out


def norm_profile(p: dict) -> dict:
    return {
        "name": p.get("name", ""),
        "user_id": p.get("user_id"),
        "settings": {**copy.deepcopy(DEFAULT_SETTINGS), **(p.get("settings") or {})},
        "alarms": {aid: norm_alarm(a) for aid, a in (p.get("alarms") or {}).items()},
    }


def normalize(data: dict) -> dict:
    data = copy.deepcopy(data or {})
    out = {**copy.deepcopy(EMPTY), **{k: v for k, v in data.items() if k in EMPTY}}
    out["profiles"] = {pid: norm_profile(p) for pid, p in (out.get("profiles") or {}).items()}
    out["stations"] = [
        {"id": s.get("id") or new_id(), "name": s.get("name", ""), "alexa": s.get("alexa", ""),
         "media_id": s.get("media_id", "")}
        for s in out.get("stations") or [] if s.get("name")
    ]
    return out


class WeckerStore:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict] = Store(hass, 1, DOMAIN)
        self.data: dict = copy.deepcopy(EMPTY)

    async def async_load(self) -> None:
        self.data = normalize(await self._store.async_load() or {})

    @callback
    def save(self) -> None:
        self._store.async_delay_save(lambda: self.data, 1)

    async def async_save_now(self) -> None:
        await self._store.async_save(self.data)

    def profile(self, pid: str | None) -> dict | None:
        return self.data["profiles"].get(pid) if pid else None

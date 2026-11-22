"""Übernahme eines bestehenden Helfer-basierten Weckers (nur lesend).

Erkannt werden Helfer nach dem Muster
  input_boolean.wecker_<person>_<n>_aktiv
  input_datetime.wecker_<person>_<n>_zeit
  input_select.wecker_<person>_<n>_sender   (Sendername)
  input_select.wecker_<person>_<n>_modus    (Werktags | Täglich)
  input_select.wecker_zielgerat_<person>    (Name des Lautsprechers)
  input_text.wecker_senderliste             (Sender, kommagetrennt)
Die alten Helfer und Automationen werden nicht verändert.
"""
from __future__ import annotations

import copy
import re

from homeassistant.core import HomeAssistant

from .const import DEFAULT_ALEXA_TEMPLATE
from .store import DEFAULT_SETTINGS, new_id, norm_alarm

OLD_ALEXA_TEMPLATE = "{sender} auf TuneIn für eine Stunde"
RE_SLOT = re.compile(r"^input_boolean\.wecker_([a-z0-9]+)_(\d+)_aktiv$")


def _state(hass: HomeAssistant, entity_id: str) -> str | None:
    s = hass.states.get(entity_id)
    return None if s is None or s.state in ("unknown", "unavailable") else s.state


def _match_player(hass: HomeAssistant, label: str | None) -> str:
    """Lautsprecher-Auswahl (Anzeigename) auf einen media_player abbilden."""
    if not label:
        return ""
    want = label.strip().lower()
    short = re.sub(r"\s*\(.*\)\s*$", "", want)
    for s in hass.states.async_all("media_player"):
        name = (s.name or "").strip().lower()
        if name in (want, short):
            return s.entity_id
    return ""


async def async_preview(hass: HomeAssistant) -> dict:
    people: dict[str, dict] = {}
    for s in hass.states.async_all("input_boolean"):
        m = RE_SLOT.match(s.entity_id)
        if not m:
            continue
        key, n = m.group(1), int(m.group(2))
        p = people.setdefault(key, {"key": key, "name": key.capitalize(), "slots": []})
        p["slots"].append({
            "n": n,
            "aktiv": s.state == "on",
            "zeit": (_state(hass, f"input_datetime.wecker_{key}_{n}_zeit") or "")[:5],
            "sender": _state(hass, f"input_select.wecker_{key}_{n}_sender") or "",
            "modus": _state(hass, f"input_select.wecker_{key}_{n}_modus") or "Werktags",
        })
    users = await hass.auth.async_get_users()
    for p in people.values():
        p["slots"].sort(key=lambda x: x["n"])
        label = _state(hass, f"input_select.wecker_zielgerat_{p['key']}")
        p["zielgeraet"] = label or ""
        p["player"] = _match_player(hass, label)
        p["user_id"] = next((u.id for u in users if not u.system_generated and u.is_active
                             and (u.name or "").strip().lower().startswith(p["key"])), None)
    stations = [x.strip() for x in (_state(hass, "input_text.wecker_senderliste") or "").split(",") if x.strip()]
    return {"people": sorted(people.values(), key=lambda p: p["key"]), "stations": stations}


def apply(data: dict, preview: dict, plan: dict) -> dict:
    """Übernahme ausführen (rein, testbar). plan:
      people: {key: {include, name, user_id, player}}
      activate: bool   alte Aktiv-Zustände übernehmen (sonst alle aus = Parallelbetrieb)
    Erneutes Ausführen ersetzt die früher übernommenen Wecker (source alt:<key>:<n>).
    """
    activate = bool(plan.get("activate"))
    if data.get("alexa_template") == DEFAULT_ALEXA_TEMPLATE:
        data["alexa_template"] = OLD_ALEXA_TEMPLATE  # alter Wecker sprach genau diesen Befehl
    by_name = {s["name"].lower(): s for s in data["stations"]}
    for name in preview.get("stations", []):
        if name.lower() not in by_name:
            st = {"id": new_id(), "name": name, "alexa": name, "media_id": ""}
            data["stations"].append(st)
            by_name[name.lower()] = st
    result = {"profiles": 0, "alarms": 0}
    for person in preview.get("people", []):
        cfg = (plan.get("people") or {}).get(person["key"]) or {}
        if not cfg.get("include", True):
            continue
        name = (cfg.get("name") or person["name"]).strip()
        uid = cfg.get("user_id") if "user_id" in cfg else person.get("user_id")
        pid = next((k for k, p in data["profiles"].items()
                    if (uid and p.get("user_id") == uid) or p.get("name", "").lower() == name.lower()), None)
        if pid is None:
            pid = new_id()
            data["profiles"][pid] = {"name": name, "user_id": uid, "settings": copy.deepcopy(DEFAULT_SETTINGS), "alarms": {}}
            result["profiles"] += 1
        prof = data["profiles"][pid]
        if uid and not prof.get("user_id"):
            prof["user_id"] = uid
        player = cfg.get("player", person.get("player")) or ""
        if player:
            prof["settings"]["player"] = player
        prof["alarms"] = {k: a for k, a in prof["alarms"].items()
                          if not str(a.get("source", "")).startswith(f"alt:{person['key']}:")}
        for slot in person["slots"]:
            if not slot["zeit"]:
                continue
            st = by_name.get((slot["sender"] or "").lower())
            alarm = norm_alarm({
                "name": f"Wecker {slot['n']}",
                "enabled": activate and slot["aktiv"],
                "time": slot["zeit"],
                "schedule": ({"mode": "days", "days": [0, 1, 2, 3, 4, 5, 6], "date": ""}
                             if slot["modus"] == "Täglich" else
                             {"mode": "workdays", "days": [0, 1, 2, 3, 4], "date": ""}),
                "use_calendars": False,
                "sound": {"enabled": True, "station_id": st["id"] if st else "", "duration_min": 60},
                "source": f"alt:{person['key']}:{slot['n']}",
            })
            prof["alarms"][new_id()] = alarm
            result["alarms"] += 1
    return result

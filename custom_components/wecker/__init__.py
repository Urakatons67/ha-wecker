"""Wecker: beliebig viele Personen und Wecker mit eigener Seite in Home Assistant.

Rechte (vom Server geprüft): Admins und Eltern sehen und bearbeiten alle Profile.
Alle anderen Benutzer sehen nur das Profil mit ihrer user_id und dürfen dort
Wecker stellen, testen, überspringen, stoppen und schlummern. Personen-Einstellungen
und Sender nur Eltern/Admins, Profile, Eltern, Export/Import und Übernahme nur Admins.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from . import migrate
from .const import DOMAIN, PANEL_ELEMENT, PANEL_PATH, URL_BASE, VERSION
from .engine import Engine
from .store import DEFAULT_SETTINGS, WeckerStore, new_id, norm_alarm, normalize

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.BUTTON, Platform.BINARY_SENSOR]
WWW = Path(__file__).parent / "www"
PANEL_JS = "wecker-panel.js"


class Runtime:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: WeckerStore) -> None:
        self.entry = entry
        self.store = store
        self.engine = Engine(hass, store)


def _rt(hass: HomeAssistant) -> Runtime | None:
    return hass.data.get(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    for cmd in (ws_get, ws_alarm_save, ws_alarm_delete, ws_alarm_test, ws_alarm_skip, ws_stop,
                ws_snooze, ws_settings_save, ws_stations_save, ws_parents_save, ws_profile_save,
                ws_profile_delete, ws_export, ws_import, ws_migrate_preview, ws_migrate_run):
        websocket_api.async_register_command(hass, cmd)
    _register_services(hass)
    return True


def _file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()[:8]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    store = WeckerStore(hass)
    await store.async_load()
    rt = Runtime(hass, entry, store)
    hass.data[DOMAIN] = rt

    flags = hass.data.setdefault(f"{DOMAIN}_flags", {})
    if not flags.get("static"):
        await hass.http.async_register_static_paths([StaticPathConfig(URL_BASE, str(WWW), False)])
        flags["static"] = True
    js_hash = await hass.async_add_executor_job(_file_hash, WWW / PANEL_JS)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_PATH,
        webcomponent_name=PANEL_ELEMENT,
        sidebar_title="Wecker",
        sidebar_icon="mdi:alarm",
        module_url=f"{URL_BASE}/{PANEL_JS}?v={VERSION}-{js_hash}",
        require_admin=False,
        config={},
    )

    entry.async_on_unload(hass.bus.async_listen("mobile_app_notification_action", rt.engine.handle_push_action))

    @callback
    def _daily(_now) -> None:  # Kalender und Werktage neu lesen
        rt.engine.clear_caches()
        rt.engine.request_reschedule()

    entry.async_on_unload(async_track_time_change(hass, _daily, hour=0, minute=1, second=0))
    entry.async_on_unload(async_track_time_change(hass, lambda _n: rt.engine.request_reschedule(),
                                                  minute=7, second=0))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    rt.engine.request_reschedule()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    rt = hass.data.pop(DOMAIN, None)
    if rt:
        await rt.engine.async_shutdown()
    frontend.async_remove_panel(hass, PANEL_PATH)
    return ok


@callback
def _changed(rt: Runtime, reload: bool = False) -> None:
    hass = rt.engine.hass
    if not reload:
        rt.store.save()
        rt.engine.request_reschedule()
        return

    async def _reload() -> None:  # Profile geändert → erst sicher speichern, dann Entitäten neu anlegen
        await rt.store.async_save_now()
        await hass.config_entries.async_reload(rt.entry.entry_id)

    hass.async_create_task(_reload())


# ---------- Dienste ----------
def _find_profile(rt: Runtime, ref: str | None) -> str | None:
    if not ref:
        return None
    profs = rt.store.data["profiles"]
    if ref in profs:
        return ref
    return next((k for k, p in profs.items() if p.get("name", "").lower() == ref.lower()), None)


def _register_services(hass: HomeAssistant) -> None:
    def _need(call: ServiceCall) -> tuple[Runtime, str | None]:
        rt = _rt(hass)
        if rt is None:
            raise ServiceValidationError("Wecker ist nicht eingerichtet")
        ref = call.data.get("profil")
        pid = _find_profile(rt, ref)
        if ref and pid is None:
            raise ServiceValidationError(f"Profil {ref} nicht gefunden")
        return rt, pid

    async def stop(call: ServiceCall) -> None:
        rt, pid = _need(call)
        await rt.engine.async_stop(pid, "dienst")

    async def snooze(call: ServiceCall) -> None:
        rt, pid = _need(call)
        await rt.engine.async_snooze(pid)

    def _alarm(rt: Runtime, pid: str | None, ref: str | None) -> str:
        if pid is None:
            raise ServiceValidationError("Profil angeben")
        alarms = rt.store.data["profiles"][pid]["alarms"]
        aid = ref if ref in alarms else next(
            (k for k, a in alarms.items() if a.get("name", "").lower() == (ref or "").lower()), None)
        if aid is None:
            raise ServiceValidationError(f"Wecker {ref} nicht gefunden")
        return aid

    async def trigger(call: ServiceCall) -> None:
        rt, pid = _need(call)
        await rt.engine.async_ring(pid, _alarm(rt, pid, call.data.get("wecker")), test=call.data.get("test", True))

    async def skip(call: ServiceCall) -> None:
        rt, pid = _need(call)
        if pid is None:
            raise ServiceValidationError("Profil angeben")
        alarms = rt.store.data["profiles"][pid]["alarms"]
        ids = [_alarm(rt, pid, call.data["wecker"])] if call.data.get("wecker") else list(alarms)
        for aid in ids:
            await _toggle_skip(rt, pid, aid, True)
        _changed(rt)

    hass.services.async_register(DOMAIN, "stoppen", stop, vol.Schema({vol.Optional("profil"): cv.string}))
    hass.services.async_register(DOMAIN, "schlummern", snooze, vol.Schema({vol.Optional("profil"): cv.string}))
    hass.services.async_register(DOMAIN, "ausloesen", trigger, vol.Schema({
        vol.Required("profil"): cv.string, vol.Required("wecker"): cv.string,
        vol.Optional("test", default=True): cv.boolean}))
    hass.services.async_register(DOMAIN, "ueberspringen", skip, vol.Schema({
        vol.Required("profil"): cv.string, vol.Optional("wecker"): cv.string}))


async def _toggle_skip(rt: Runtime, pid: str, aid: str, skip: bool) -> str | None:
    """Nächsten Termin überspringen (skip) bzw. das Überspringen aufheben."""
    alarm = rt.store.data["profiles"][pid]["alarms"][aid]
    if not skip:
        alarm["skip_dates"] = []
        return None
    when = await rt.engine.async_next(pid, alarm)
    if when is None:
        return None
    d = when.date().isoformat()
    alarm["skip_dates"] = sorted(set(alarm.get("skip_dates") or []) | {d})
    return d


# ---------- Rechte ----------
def _role(store: WeckerStore, user) -> str:
    if user is None:
        return "none"
    if user.is_admin:
        return "admin"
    if user.id in store.data.get("parents", []):
        return "parent"
    return "user"


def _own(store: WeckerStore, user) -> str | None:
    if user is None:
        return None
    return next((pid for pid, p in store.data["profiles"].items() if p.get("user_id") == user.id), None)


def _ctx(hass, connection, msg, need_parent=False, allow_none=False):
    """(runtime, profil_id, profil, rolle) oder None nach Fehlermeldung."""
    rt = _rt(hass)
    if rt is None:
        connection.send_error(msg["id"], "not_loaded", "Wecker ist nicht eingerichtet")
        return None
    store = rt.store
    role = _role(store, connection.user)
    if role in ("admin", "parent"):
        pid = msg.get("profile") or _own(store, connection.user) or next(iter(store.data["profiles"]), None)
    else:
        pid = _own(store, connection.user)
        if msg.get("profile") and msg["profile"] != pid:
            connection.send_error(msg["id"], "unauthorized", "Kein Zugriff auf dieses Profil")
            return None
    if need_parent and role not in ("admin", "parent"):
        connection.send_error(msg["id"], "unauthorized", "Nur für Eltern")
        return None
    prof = store.profile(pid)
    if prof is None and not allow_none:
        connection.send_error(msg["id"], "no_profile", "Für deinen Benutzer gibt es keinen Wecker")
        return None
    return rt, pid, prof, role


def _admin_rt(hass, connection, msg) -> Runtime | None:
    rt = _rt(hass)
    if rt is None:
        connection.send_error(msg["id"], "not_loaded", "Wecker ist nicht eingerichtet")
    return rt


def _choices(hass: HomeAssistant, rt: Runtime) -> dict:
    def ents(domain: str) -> list:
        return sorted(({"id": s.entity_id, "name": s.name or s.entity_id} for s in hass.states.async_all(domain)),
                      key=lambda x: x["name"].lower())

    players = ents("media_player")
    for p in players:
        p["platform"] = rt.engine.player_platform(p["id"])
    notify = sorted(f"notify.{s}" for s in hass.services.async_services().get("notify", {})
                    if s not in ("send_message", "persistent_notification", "notify"))
    workday = [e for e in ents("binary_sensor") if rt.engine.player_platform(e["id"]) == "workday"]
    return {"players": players, "lights": ents("light"), "covers": ents("cover"), "scripts": ents("script"),
            "calendars": ents("calendar"), "notify": notify, "workday": workday}


def _ring_info(rt: Runtime, pid: str) -> dict | None:
    r = rt.engine.ringing.get(pid)
    if not r:
        return None
    return {"alarm_id": r.aid, "status": r.status, "test": r.test, "since": r.started.isoformat(), "snoozes": r.snoozes}


async def _alarms_out(rt: Runtime, pid: str, prof: dict) -> list:
    out = []
    for aid, a in prof["alarms"].items():
        nxt = rt.engine.alarm_next.get((pid, aid))
        if nxt is None and a.get("enabled") and prof["settings"].get("enabled", True):
            nxt = await rt.engine.async_next(pid, a)
        out.append({**a, "id": aid, "next": nxt.isoformat() if nxt else None})
    out.sort(key=lambda a: (a["time"], a["name"]))
    return out


PROFILE_ARG = {vol.Optional("profile"): cv.string}


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/get", **PROFILE_ARG})
@websocket_api.async_response
async def ws_get(hass: HomeAssistant, connection, msg) -> None:
    ctx = _ctx(hass, connection, msg, allow_none=True)
    if ctx is None:
        return
    rt, pid, prof, role = ctx
    store = rt.store
    parent = role in ("admin", "parent")
    result = {
        "version": VERSION,
        "role": role,
        "profile": pid if prof else None,
        "name": prof["name"] if prof else "",
        "settings": prof["settings"] if prof else dict(DEFAULT_SETTINGS),
        "alarms": await _alarms_out(rt, pid, prof) if prof else [],
        "ringing": _ring_info(rt, pid) if prof else None,
        "stations": store.data["stations"],
        "alexa_template": store.data.get("alexa_template", ""),
        "profiles": [{"id": k, "name": p.get("name") or k} for k, p in store.data["profiles"].items()] if parent else [],
        "choices": _choices(hass, rt),
        "now": dt_util.now().isoformat(),
    }
    if role == "admin":
        users = await hass.auth.async_get_users()
        result["parents"] = list(store.data.get("parents", []))
        for p in result["profiles"]:
            p["user_id"] = store.data["profiles"][p["id"]].get("user_id")
        result["users"] = sorted(({"id": u.id, "name": u.name} for u in users
                                  if not u.system_generated and u.is_active),
                                 key=lambda x: (x["name"] or "").lower())
    connection.send_result(msg["id"], result)


HHMM = vol.Match(r"^([01]\d|2[0-3]):[0-5]\d$")
ISO_DATE = vol.Match(r"^\d{4}-\d{2}-\d{2}$")
PCT = vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=0, max=100)))
ALARM = vol.Schema({
    vol.Optional("id"): vol.Any(None, cv.string),
    vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=40)),
    vol.Required("enabled"): cv.boolean,
    vol.Required("time"): HHMM,
    vol.Required("schedule"): {
        vol.Required("mode"): vol.In(["days", "workdays", "once"]),
        vol.Optional("days", default=[]): [vol.All(int, vol.Range(min=0, max=6))],
        vol.Optional("date", default=""): vol.Any("", ISO_DATE),
    },
    vol.Optional("use_calendars", default=True): cv.boolean,
    vol.Optional("skip_dates", default=[]): [ISO_DATE],
    vol.Required("sound"): {
        vol.Required("enabled"): cv.boolean,
        vol.Optional("station_id", default=""): vol.Any(None, cv.string),
        vol.Optional("player", default=""): vol.Any(None, "", cv.entity_id),
        vol.Optional("volume_start"): PCT,
        vol.Optional("volume_end"): PCT,
        vol.Optional("ramp_min", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=60)),
        vol.Optional("duration_min", default=60): vol.All(vol.Coerce(int), vol.Range(min=1, max=240)),
    },
    vol.Required("light"): {
        vol.Required("enabled"): cv.boolean,
        vol.Optional("entities", default=[]): [cv.entity_id],
        vol.Optional("minutes", default=15): vol.All(vol.Coerce(int), vol.Range(min=0, max=120)),
        vol.Optional("brightness", default=100): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
        vol.Optional("kelvin", default=2700): vol.All(vol.Coerce(int), vol.Range(min=0, max=10000)),
    },
    vol.Required("covers"): {
        vol.Required("enabled"): cv.boolean,
        vol.Optional("entities", default=[]): [cv.entity_id],
        vol.Optional("position", default=100): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
    },
    vol.Required("script"): {
        vol.Required("enabled"): cv.boolean,
        vol.Optional("entity", default=""): vol.Any("", cv.entity_id),
    },
    vol.Required("pre"): {
        vol.Required("enabled"): cv.boolean,
        vol.Optional("minutes", default=30): vol.All(vol.Coerce(int), vol.Range(min=1, max=240)),
    },
    vol.Optional("source"): vol.Any(None, cv.string),
    vol.Optional("next"): vol.Any(None, cv.string),
}, extra=vol.REMOVE_EXTRA)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/alarm/save", vol.Required("alarm"): ALARM, **PROFILE_ARG})
@websocket_api.async_response
async def ws_alarm_save(hass, connection, msg) -> None:
    ctx = _ctx(hass, connection, msg)
    if ctx is None:
        return
    rt, pid, prof, _role_ = ctx
    a = dict(msg["alarm"])
    a.pop("next", None)
    aid = a.pop("id", None) or new_id()
    if a["schedule"]["mode"] == "once" and not a["schedule"].get("date"):
        connection.send_error(msg["id"], "invalid", "Für „einmalig“ ein Datum wählen")
        return
    if a["schedule"]["mode"] == "days" and not a["schedule"].get("days"):
        connection.send_error(msg["id"], "invalid", "Mindestens einen Wochentag wählen")
        return
    old = prof["alarms"].get(aid, {})
    a["source"] = old.get("source", "")
    prof["alarms"][aid] = norm_alarm(a)
    _changed(rt)
    nxt = await rt.engine.async_next(pid, prof["alarms"][aid])
    connection.send_result(msg["id"], {"id": aid, "next": nxt.isoformat() if nxt else None})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/alarm/delete", vol.Required("alarm_id"): cv.string, **PROFILE_ARG})
@callback
def ws_alarm_delete(hass, connection, msg) -> None:
    ctx = _ctx(hass, connection, msg)
    if ctx is None:
        return
    rt, _pid, prof, _role_ = ctx
    prof["alarms"].pop(msg["alarm_id"], None)
    _changed(rt)
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/alarm/test", vol.Required("alarm_id"): cv.string, **PROFILE_ARG})
@websocket_api.async_response
async def ws_alarm_test(hass, connection, msg) -> None:
    ctx = _ctx(hass, connection, msg)
    if ctx is None:
        return
    rt, pid, prof, _role_ = ctx
    if msg["alarm_id"] not in prof["alarms"]:
        connection.send_error(msg["id"], "not_found", "Wecker nicht gefunden")
        return
    await rt.engine.async_ring(pid, msg["alarm_id"], test=True)
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/alarm/skip", vol.Required("alarm_id"): cv.string,
                                  vol.Required("skip"): cv.boolean, **PROFILE_ARG})
@websocket_api.async_response
async def ws_alarm_skip(hass, connection, msg) -> None:
    ctx = _ctx(hass, connection, msg)
    if ctx is None:
        return
    rt, pid, prof, _role_ = ctx
    if msg["alarm_id"] not in prof["alarms"]:
        connection.send_error(msg["id"], "not_found", "Wecker nicht gefunden")
        return
    d = await _toggle_skip(rt, pid, msg["alarm_id"], msg["skip"])
    _changed(rt)
    connection.send_result(msg["id"], {"skipped": d})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/stop", **PROFILE_ARG})
@websocket_api.async_response
async def ws_stop(hass, connection, msg) -> None:
    ctx = _ctx(hass, connection, msg)
    if ctx is None:
        return
    rt, pid, _prof, _role_ = ctx
    connection.send_result(msg["id"], {"stopped": await rt.engine.async_stop(pid, "seite")})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/snooze", **PROFILE_ARG})
@websocket_api.async_response
async def ws_snooze(hass, connection, msg) -> None:
    ctx = _ctx(hass, connection, msg)
    if ctx is None:
        return
    rt, pid, _prof, _role_ = ctx
    connection.send_result(msg["id"], {"snoozed": await rt.engine.async_snooze(pid)})


SETTINGS = vol.Schema({
    vol.Optional("enabled"): cv.boolean,
    vol.Optional("player"): vol.Any("", cv.entity_id),
    vol.Optional("notify"): [cv.string],
    vol.Optional("push"): cv.boolean,
    vol.Optional("fallback_push"): cv.boolean,
    vol.Optional("snooze_min"): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
    vol.Optional("calendars"): [cv.entity_id],
    vol.Optional("workday_entity"): vol.Any("", cv.entity_id),
}, extra=vol.REMOVE_EXTRA)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/settings/save", vol.Required("settings"): SETTINGS, **PROFILE_ARG})
@callback
def ws_settings_save(hass, connection, msg) -> None:
    ctx = _ctx(hass, connection, msg, need_parent=True)
    if ctx is None:
        return
    rt, _pid, prof, _role_ = ctx
    prof["settings"].update(msg["settings"])
    rt.engine.clear_caches()
    _changed(rt)
    connection.send_result(msg["id"], {"settings": prof["settings"]})


STATION = vol.Schema({
    vol.Optional("id"): vol.Any(None, cv.string),
    vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=40)),
    vol.Optional("alexa", default=""): vol.All(cv.string, vol.Length(max=80)),
    vol.Optional("media_id", default=""): vol.All(cv.string, vol.Length(max=500)),
}, extra=vol.REMOVE_EXTRA)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/stations/save", vol.Required("stations"): [STATION],
                                  vol.Optional("alexa_template"): vol.All(cv.string, vol.Length(min=1, max=120))})
@callback
def ws_stations_save(hass, connection, msg) -> None:
    ctx = _ctx(hass, connection, msg, need_parent=True, allow_none=True)
    if ctx is None:
        return
    rt = ctx[0]
    rt.store.data["stations"] = [{**s, "id": s.get("id") or new_id()} for s in msg["stations"]]
    if msg.get("alexa_template"):
        tpl = msg["alexa_template"]
        if "{sender}" not in tpl:
            connection.send_error(msg["id"], "invalid", "Der Sprachbefehl muss {sender} enthalten")
            return
        rt.store.data["alexa_template"] = tpl
    _changed(rt)
    connection.send_result(msg["id"], {"stations": rt.store.data["stations"]})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/parents/save", vol.Required("parents"): [cv.string]})
@websocket_api.require_admin
@callback
def ws_parents_save(hass, connection, msg) -> None:
    rt = _admin_rt(hass, connection, msg)
    if rt is None:
        return
    rt.store.data["parents"] = sorted(set(msg["parents"]))
    _changed(rt)
    connection.send_result(msg["id"], {"parents": rt.store.data["parents"]})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/profile/save",
    vol.Optional("profile_id"): vol.Any(None, cv.string),
    vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=30)),
    vol.Optional("user_id"): vol.Any(None, cv.string),
})
@websocket_api.require_admin
@callback
def ws_profile_save(hass, connection, msg) -> None:
    """Profil anlegen (ohne profile_id) oder ändern. "id" ist die WS-Nachrichtennummer."""
    rt = _admin_rt(hass, connection, msg)
    if rt is None:
        return
    profiles = rt.store.data["profiles"]
    uid = msg.get("user_id") or None
    pid = msg.get("profile_id")
    if uid and any(p.get("user_id") == uid for k, p in profiles.items() if k != pid):
        connection.send_error(msg["id"], "user_taken", "Dieser Benutzer hat schon ein Profil")
        return
    if pid:
        prof = profiles.get(pid)
        if prof is None:
            connection.send_error(msg["id"], "not_found", "Profil nicht gefunden")
            return
        renamed = prof["name"] != msg["name"].strip()
        prof["name"] = msg["name"].strip()
        prof["user_id"] = uid
        _changed(rt, reload=renamed)
    else:
        pid = new_id()
        profiles[pid] = normalize({"profiles": {pid: {"name": msg["name"].strip(), "user_id": uid}}})["profiles"][pid]
        _changed(rt, reload=True)
    connection.send_result(msg["id"], {"profile_id": pid})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/profile/delete", vol.Required("profile_id"): cv.string,
                                  vol.Required("confirm_name"): cv.string})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_profile_delete(hass, connection, msg) -> None:
    rt = _admin_rt(hass, connection, msg)
    if rt is None:
        return
    prof = rt.store.data["profiles"].get(msg["profile_id"])
    if prof is None:
        connection.send_error(msg["id"], "not_found", "Profil nicht gefunden")
        return
    if msg["confirm_name"].strip() != prof.get("name", "").strip():
        connection.send_error(msg["id"], "confirm", "Name zur Bestätigung stimmt nicht")
        return
    await rt.engine.async_stop(msg["profile_id"], "geloescht")
    del rt.store.data["profiles"][msg["profile_id"]]
    _changed(rt, reload=True)
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/export"})
@websocket_api.require_admin
@callback
def ws_export(hass, connection, msg) -> None:
    rt = _admin_rt(hass, connection, msg)
    if rt is None:
        return
    connection.send_result(msg["id"], {"format": "wecker", "version": VERSION,
                                       "exported": dt_util.now().isoformat(), "data": rt.store.data})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/import", vol.Required("data"): dict})
@websocket_api.require_admin
@callback
def ws_import(hass, connection, msg) -> None:
    """Gesamten Datenbestand ersetzen (vorher exportieren!)."""
    rt = _admin_rt(hass, connection, msg)
    if rt is None:
        return
    raw = msg["data"].get("data", msg["data"])
    if not isinstance(raw.get("profiles"), dict):
        connection.send_error(msg["id"], "invalid", "Keine Wecker-Datei")
        return
    rt.store.data = normalize(raw)
    _changed(rt, reload=True)
    connection.send_result(msg["id"], {"profiles": len(rt.store.data["profiles"])})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/migrate/preview"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_migrate_preview(hass, connection, msg) -> None:
    connection.send_result(msg["id"], await migrate.async_preview(hass))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/migrate/run", vol.Required("plan"): dict})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_migrate_run(hass, connection, msg) -> None:
    rt = _admin_rt(hass, connection, msg)
    if rt is None:
        return
    preview = await migrate.async_preview(hass)
    before = {pid for pid in rt.store.data["profiles"]}
    result = migrate.apply(rt.store.data, preview, msg["plan"])
    rt.store.data = normalize(rt.store.data)
    _changed(rt, reload=set(rt.store.data["profiles"]) != before)
    _LOGGER.info("Wecker übernommen: %s", result)
    connection.send_result(msg["id"], result)

// Wecker – Seite „Wecker“ (Wecker stellen, Einstellungen, Sender, Profile, Sichern)
const DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clone = (o) => JSON.parse(JSON.stringify(o));
const pad = (n) => String(n).padStart(2, "0");
const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };

function getPath(o, p) { return p.split(".").reduce((x, k) => (x == null ? x : x[k]), o); }
function setPath(o, p, v) {
  const ks = p.split(".");
  let x = o;
  for (const k of ks.slice(0, -1)) { if (x[k] == null) x[k] = {}; x = x[k]; }
  x[ks[ks.length - 1]] = v;
}
function fmtNext(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const t = new Date(); t.setHours(0, 0, 0, 0);
  const diff = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()) - t) / 86400000);
  const day = diff === 0 ? "heute" : diff === 1 ? "morgen" : `${DAYS[(d.getDay() + 6) % 7]} ${pad(d.getDate())}.${pad(d.getMonth() + 1)}.`;
  return `${day} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function schedText(a) {
  const s = a.schedule || {};
  if (s.mode === "once") return s.date ? `einmalig am ${s.date.split("-").reverse().join(".")}` : "einmalig";
  if (s.mode === "workdays") return "an Werktagen";
  const d = [...(s.days || [])].sort();
  if (d.length === 7) return "täglich";
  if (d.join() === "0,1,2,3,4") return "Mo–Fr";
  if (d.join() === "5,6") return "Wochenende";
  return d.map((i) => DAYS[i]).join(", ");
}
const NEW_ALARM = {
  name: "Wecker", enabled: true, time: "06:30",
  schedule: { mode: "workdays", days: [0, 1, 2, 3, 4], date: "" },
  use_calendars: true, skip_dates: [],
  sound: { enabled: true, station_id: "", player: "", volume_start: null, volume_end: null, ramp_min: 0, duration_min: 60 },
  light: { enabled: false, entities: [], minutes: 15, brightness: 100, kelvin: 2700 },
  covers: { enabled: false, entities: [], position: 100 },
  script: { enabled: false, entity: "" },
  pre: { enabled: false, minutes: 30 },
};

class WeckerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "wecker";
    this._data = null;
    this._edit = null;
    this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
    this.shadowRoot.addEventListener("change", (e) => this._onChange(e));
  }

  set hass(h) {
    const first = !this._hass;
    this._hass = h;
    if (this._menu) this._menu.hass = h;
    if (first) this._load();
    // Klingeln live anzeigen: ändert sich eine „Klingelt“-Entität, neu laden
    const key = Object.values(h.states)
      .filter((s) => s.entity_id.startsWith("binary_sensor.wecker_") && s.attributes.status !== undefined)
      .map((s) => s.entity_id + s.attributes.status).join();
    if (key !== this._ringKey) {
      const had = this._ringKey !== undefined;
      this._ringKey = key;
      if (had && this._data && !this._edit) this._load();
    }
  }
  set narrow(n) { this._narrow = n; if (this._menu) this._menu.narrow = n; }
  set panel(p) {}

  _ws(msg) { return this._hass.callWS(this._profile ? { ...msg, profile: this._profile } : msg); }
  get _isParent() { return !!this._data && (this._data.role === "admin" || this._data.role === "parent"); }
  get _isAdmin() { return !!this._data && this._data.role === "admin"; }

  async _load() {
    try {
      const req = { type: "wecker/get" };
      if (this._profile) req.profile = this._profile;
      this._data = await this._hass.callWS(req);
      this._profile = this._data.profile;
      this._err = null;
      this._set = clone(this._data.settings);
      this._st = clone(this._data.stations);
      this._tpl = this._data.alexa_template;
      this._profDraft = clone(this._data.profiles || []);
      this._parentsDraft = [...(this._data.parents || [])];
      this._newProf = this._newProf || { name: "", user_id: "" };
    } catch (e) {
      this._err = e.message || String(e);
    }
    this._render();
  }

  _toast(msg) {
    this._msg = msg;
    this._render();
    clearTimeout(this._toastT);
    this._toastT = setTimeout(() => { this._msg = ""; this._render(); }, 2800);
  }

  async _try(fn, ok) {
    try { const r = await fn(); if (ok) this._toast(ok); return r; } catch (e) { this._toast("Fehler: " + (e.message || e)); return null; }
  }

  _obj(name) { return { edit: this._edit, set: this._set, newProf: this._newProf }[name]; }

  // ---------- Eingaben ----------
  _onChange(e) {
    const el = e.target;
    const ds = el.dataset;
    if (ds.f && ds.o) {
      let v = el.type === "checkbox" ? el.checked : el.value;
      if (ds.t === "int") v = v === "" ? 0 : parseInt(v, 10);
      if (ds.t === "intnull") v = v === "" ? null : Math.max(0, Math.min(100, parseInt(v, 10)));
      setPath(this._obj(ds.o), ds.f, v);
      if (ds.r !== undefined) this._render();
      return;
    }
    if (ds.act === "msadd" && el.value) {
      const o = this._obj(ds.o);
      const arr = getPath(o, ds.f) || [];
      if (!arr.includes(el.value)) arr.push(el.value);
      setPath(o, ds.f, arr);
      this._render();
    } else if (ds.act === "toggle") {
      this._toggleAlarm(ds.aid, el.checked);
    } else if (ds.act === "st") {
      this._st[+ds.i][ds.k] = el.value;
    } else if (ds.act === "tpl") {
      this._tpl = el.value;
    } else if (ds.act === "pname") {
      this._profDraft[+ds.i].name = el.value;
    } else if (ds.act === "puser") {
      this._profDraft[+ds.i].user_id = el.value || null;
    } else if (ds.act === "parent") {
      const s = new Set(this._parentsDraft);
      el.checked ? s.add(ds.uid) : s.delete(ds.uid);
      this._parentsDraft = [...s];
    } else if (ds.act === "import") {
      this._import(el.files && el.files[0]);
    }
  }

  _onClick(e) {
    const b = e.target.closest("[data-act]");
    if (!b || b.tagName === "SELECT" || (b.tagName === "INPUT" && b.type !== "button")) return;
    const ds = b.dataset;
    const a = ds.act;
    if (a === "tab") { this._tab = ds.tab; this._edit = null; this._render(); }
    else if (a === "profile") this._switch(ds.pid);
    else if (a === "new") { this._edit = clone(NEW_ALARM); if (this._st[0]) this._edit.sound.station_id = this._st[0].id; this._render(); }
    else if (a === "edit") { this._edit = clone(this._data.alarms.find((x) => x.id === ds.aid)); this._render(); }
    else if (a === "cancel") { this._edit = null; this._render(); }
    else if (a === "save") this._saveAlarm();
    else if (a === "delete") this._deleteAlarm();
    else if (a === "test") this._try(() => this._ws({ type: "wecker/alarm/test", alarm_id: ds.aid }), "Test läuft – mit Stopp beenden").then(() => this._load());
    else if (a === "skip") this._try(() => this._ws({ type: "wecker/alarm/skip", alarm_id: ds.aid, skip: ds.on === "1" }), ds.on === "1" ? "Nächster Termin wird übersprungen" : "Überspringen aufgehoben").then(() => this._load());
    else if (a === "stop") this._try(() => this._ws({ type: "wecker/stop" }), "Wecker gestoppt").then(() => this._load());
    else if (a === "snooze") this._try(() => this._ws({ type: "wecker/snooze" }), "Schlummern …").then(() => this._load());
    else if (a === "mode") { this._edit.schedule.mode = ds.m; if (ds.m === "once" && !this._edit.schedule.date) this._edit.schedule.date = todayIso(); this._render(); }
    else if (a === "day") {
      const s = new Set(this._edit.schedule.days || []);
      s.has(+ds.d) ? s.delete(+ds.d) : s.add(+ds.d);
      this._edit.schedule.days = [...s].sort();
      this._render();
    } else if (a === "msdel") {
      const o = this._obj(ds.o);
      setPath(o, ds.f, (getPath(o, ds.f) || []).filter((x) => x !== ds.v));
      this._render();
    } else if (a === "savesettings") this._try(() => this._ws({ type: "wecker/settings/save", settings: this._set }), "Einstellungen gespeichert ✓").then(() => this._load());
    else if (a === "staddd") { this._st.push({ name: "", alexa: "", media_id: "" }); this._render(); }
    else if (a === "stdel") { this._st.splice(+ds.i, 1); this._render(); }
    else if (a === "savestations") this._saveStations();
    else if (a === "profsave") this._profSave(+ds.i);
    else if (a === "profdel") this._profDel(+ds.i);
    else if (a === "profadd") this._profAdd();
    else if (a === "saveparents") this._try(() => this._hass.callWS({ type: "wecker/parents/save", parents: this._parentsDraft }), "Eltern gespeichert ✓");
    else if (a === "export") this._export();
  }

  async _switch(pid) {
    if (pid === this._profile) return;
    if (this._edit && !confirm("Nicht gespeicherte Änderungen verwerfen?")) return;
    this._profile = pid; this._edit = null; this._data = null; this._render();
    await this._load();
  }

  async _toggleAlarm(aid, on) {
    const a = clone(this._data.alarms.find((x) => x.id === aid));
    a.enabled = on;
    await this._try(() => this._ws({ type: "wecker/alarm/save", alarm: a }), on ? "Wecker an" : "Wecker aus");
    this._load();
  }

  async _saveAlarm() {
    const r = await this._try(() => this._ws({ type: "wecker/alarm/save", alarm: this._edit }));
    if (r) { this._edit = null; this._toast(r.next ? `Gespeichert ✓ – klingelt ${fmtNext(r.next)}` : "Gespeichert ✓"); this._load(); }
  }

  async _deleteAlarm() {
    if (!this._edit.id) { this._edit = null; this._render(); return; }
    if (!confirm(`Wecker „${this._edit.name}“ löschen?`)) return;
    await this._try(() => this._ws({ type: "wecker/alarm/delete", alarm_id: this._edit.id }), "Gelöscht");
    this._edit = null; this._load();
  }

  async _saveStations() {
    const stations = this._st.filter((s) => (s.name || "").trim());
    const r = await this._try(() => this._hass.callWS({ type: "wecker/stations/save", stations, alexa_template: this._tpl }), "Sender gespeichert ✓");
    if (r) this._load();
  }

  async _profSave(i) {
    const p = this._profDraft[i];
    await this._try(() => this._hass.callWS({ type: "wecker/profile/save", profile_id: p.id, name: p.name, user_id: p.user_id || null }), "Profil gespeichert ✓");
    setTimeout(() => this._load(), 1500);
  }
  async _profDel(i) {
    const p = this._profDraft[i];
    const n = prompt(`Profil „${p.name}“ mit allen Weckern löschen? Zur Bestätigung den Namen eingeben:`);
    if (n == null) return;
    await this._try(() => this._hass.callWS({ type: "wecker/profile/delete", profile_id: p.id, confirm_name: n }), "Profil gelöscht");
    if (p.id === this._profile) this._profile = null;
    setTimeout(() => this._load(), 1500);
  }
  async _profAdd() {
    if (!this._newProf.name.trim()) { this._toast("Bitte einen Namen eingeben"); return; }
    const r = await this._try(() => this._hass.callWS({ type: "wecker/profile/save", name: this._newProf.name, user_id: this._newProf.user_id || null }), "Profil angelegt ✓");
    if (r) { this._newProf = { name: "", user_id: "" }; this._profile = r.profile_id; setTimeout(() => this._load(), 1500); }
  }

  async _export() {
    const r = await this._try(() => this._hass.callWS({ type: "wecker/export" }));
    if (!r) return;
    const blob = new Blob([JSON.stringify(r, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `wecker-${todayIso()}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  async _import(file) {
    if (!file) return;
    let data;
    try { data = JSON.parse(await file.text()); } catch (e) { this._toast("Datei ist kein gültiges JSON"); return; }
    if (!confirm("Alle Wecker, Profile und Sender durch den Inhalt der Datei ersetzen?")) return;
    const r = await this._try(() => this._hass.callWS({ type: "wecker/import", data }));
    if (r) { this._toast(`Importiert: ${r.profiles} Profile`); this._profile = null; setTimeout(() => this._load(), 1500); }
  }

  // ---------- Darstellung ----------
  _render() {
    const root = this.shadowRoot;
    let body;
    if (this._err) body = `<div class="card err">Fehler beim Laden: ${esc(this._err)}</div>`;
    else if (!this._data) body = `<div class="card">Lade …</div>`;
    else if (this._tab === "einst") body = this._rEinst();
    else body = this._edit ? this._rEdit() : this._rList();
    const tabs = [["wecker", "Wecker"], ["einst", "Einstellungen"]]
      .map(([k, l]) => `<button class="tab ${this._tab === k ? "on" : ""}" data-act="tab" data-tab="${k}">${l}</button>`).join("");
    const profs = (this._data && this._data.profiles) || [];
    const switcher = profs.length > 1
      ? `<div class="pswitch">${profs.map((p) => `<button class="${p.id === this._profile ? "on" : ""}" data-act="profile" data-pid="${esc(p.id)}">${esc(p.name)}</button>`).join("")}</div>` : "";
    root.innerHTML = `<style>${CSS}</style>
      <header><span id="menu"></span><h1>Wecker</h1>${switcher}</header>
      <nav>${tabs}</nav>
      <main>${this._rRinging()}${body}</main>
      ${this._msg ? `<div class="toast">${esc(this._msg)}</div>` : ""}`;
    if (!this._menu) {
      this._menu = document.createElement("ha-menu-button");
      this._menu.hass = this._hass;
      this._menu.narrow = this._narrow;
    }
    root.querySelector("#menu").appendChild(this._menu);
  }

  _rRinging() {
    const r = this._data && this._data.ringing;
    if (!r) return "";
    const a = this._data.alarms.find((x) => x.id === r.alarm_id) || { name: "Wecker" };
    const snoozing = r.status === "schlummert";
    return `<div class="ring ${snoozing ? "sn" : ""}">
      <div class="rt">${snoozing ? "😴" : "⏰"} ${esc(a.name)} ${snoozing ? "schlummert" : "klingelt"}${r.test ? " (Test)" : ""}</div>
      <div class="row">
        ${snoozing ? "" : `<button class="big" data-act="snooze">😴 Schlummern</button>`}
        <button class="big stop" data-act="stop">⏹ Stopp</button>
      </div></div>`;
  }

  _rList() {
    const d = this._data;
    if (!d.profile) {
      return `<div class="card">${this._isAdmin
        ? "Noch kein Profil. Unter <b>Einstellungen → Profile</b> eine Person anlegen (oder dort die alten Wecker übernehmen)."
        : "Für deinen Benutzer gibt es noch keinen Wecker. Bitte einen Erwachsenen, ein Profil anzulegen."}</div>`;
    }
    const off = d.settings.enabled === false;
    const items = d.alarms.map((a) => {
      const skipped = (a.skip_dates || []).length > 0;
      const next = a.enabled && !off && a.next ? `Nächster: ${fmtNext(a.next)}` : a.enabled && !off ? "kein Termin" : "aus";
      const extras = [a.sound.enabled ? "🔊" : "", a.light.enabled ? "🌅" : "", a.covers.enabled ? "🪟" : "", a.script.enabled ? "⚙️" : "", a.pre.enabled ? "⏳" : "", a.use_calendars && (d.settings.calendars || []).length ? "📅" : ""].join("");
      return `<div class="alarm ${a.enabled && !off ? "" : "dim"}">
        <div class="al">
          <div class="time">${esc(a.time)}</div>
          <div class="meta"><b>${esc(a.name)}</b> · ${esc(schedText(a))} <span class="ex">${extras}</span></div>
          <div class="meta">${esc(next)}${skipped ? ` · <span class="warn">übersprungen: ${a.skip_dates.map((x) => x.split("-").reverse().slice(0, 2).join(".") + ".").join(", ")}</span>` : ""}</div>
          <div class="row sm">
            <button class="link" data-act="edit" data-aid="${a.id}">Bearbeiten</button>
            <button class="link" data-act="test" data-aid="${a.id}">Test</button>
            ${a.enabled ? (skipped ? `<button class="link" data-act="skip" data-aid="${a.id}" data-on="0">Überspringen aufheben</button>` : `<button class="link" data-act="skip" data-aid="${a.id}" data-on="1">Nächsten überspringen</button>`) : ""}
          </div>
        </div>
        <label class="sw"><input type="checkbox" data-act="toggle" data-aid="${a.id}" ${a.enabled ? "checked" : ""}><span></span></label>
      </div>`;
    }).join("");
    return `${off ? `<div class="card warn">Alle Wecker von ${esc(d.name)} sind ausgeschaltet (Hauptschalter in den Einstellungen).</div>` : ""}
      ${items || `<div class="card muted">Noch keine Wecker.</div>`}
      <button class="primary" data-act="new">+ Neuer Wecker</button>`;
  }

  _opts(list, sel, empty) {
    return (empty != null ? `<option value="">${esc(empty)}</option>` : "") +
      list.map((x) => `<option value="${esc(x.id)}" ${x.id === sel ? "selected" : ""}>${esc(x.name)}</option>`).join("");
  }

  _ms(o, f, list, label) { // Mehrfachauswahl mit Chips
    const cur = getPath(this._obj(o), f) || [];
    const name = (id) => (list.find((x) => x.id === id) || { name: id }).name;
    return `<div class="ms"><div class="lbl">${esc(label)}</div>
      <div class="chips">${cur.map((id) => `<span class="chip">${esc(name(id))}<button class="x" data-act="msdel" data-o="${o}" data-f="${f}" data-v="${esc(id)}">✕</button></span>`).join("") || `<span class="muted small">keine</span>`}</div>
      <select data-act="msadd" data-o="${o}" data-f="${f}"><option value="">+ hinzufügen …</option>${list.filter((x) => !cur.includes(x.id)).map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("")}</select></div>`;
  }

  _chk(o, f, label, rerender = true) {
    return `<label class="chk"><input type="checkbox" data-o="${o}" data-f="${f}" ${getPath(this._obj(o), f) ? "checked" : ""} ${rerender ? "data-r" : ""}> ${label}</label>`;
  }
  _num(o, f, label, min, max, t = "int", unit = "") {
    const v = getPath(this._obj(o), f);
    return `<label class="num">${esc(label)} <input type="number" min="${min}" max="${max}" value="${v == null ? "" : v}" data-o="${o}" data-f="${f}" data-t="${t}">${unit}</label>`;
  }

  _rEdit() {
    const a = this._edit;
    const c = this._data.choices;
    const s = a.schedule;
    const st = this._data.settings;
    const stdPlayer = (c.players.find((p) => p.id === st.player) || {}).name;
    const modes = [["days", "Wochentage"], ["workdays", "Werktage"], ["once", "Einmalig"]]
      .map(([m, l]) => `<button class="seg ${s.mode === m ? "on" : ""}" data-act="mode" data-m="${m}">${l}</button>`).join("");
    const days = s.mode === "days" ? `<div class="row">${DAYS.map((d, i) => `<button class="seg day ${s.days.includes(i) ? "on" : ""}" data-act="day" data-d="${i}">${d}</button>`).join("")}</div>` : "";
    const date = s.mode === "once" ? `<div class="row"><input type="date" value="${esc(s.date)}" data-o="edit" data-f="schedule.date"></div>` : "";
    const wdHint = s.mode === "workdays" ? `<p class="hint">Werktage laut Werktag-Sensor (ohne Wochenende und Feiertage).</p>` : "";
    const cals = (st.calendars || []).map((id) => (c.calendars.find((x) => x.id === id) || { name: id }).name);
    const plat = (id) => (c.players.find((p) => p.id === id) || {}).platform || "";
    const pl = a.sound.player || st.player;
    const playerHint = !pl ? "Kein Lautsprecher – bitte hier oder in den Einstellungen wählen."
      : plat(pl) === "alexa_media" ? "Alexa: der Sender wird per Sprachbefehl gestartet."
      : plat(pl) === "music_assistant" ? "Music Assistant: braucht beim Sender eine Adresse (URL oder Music-Assistant-Link)."
      : "Braucht beim Sender eine Adresse (Stream-URL).";
    return `<div class="card">
      <h2>${a.id ? "Wecker bearbeiten" : "Neuer Wecker"}</h2>
      <div class="row"><input class="txt" type="text" maxlength="40" value="${esc(a.name)}" data-o="edit" data-f="name" placeholder="Name, z. B. Schule">
        <input class="tm" type="time" value="${esc(a.time)}" data-o="edit" data-f="time"></div>
      <div class="row">${modes}</div>${days}${date}${wdHint}
      ${this._chk("edit", "use_calendars", cals.length ? `nicht an Tagen mit Termin in: ${esc(cals.join(", "))}` : "Ausnahme-Kalender beachten (in den Einstellungen festlegen)", false)}
      ${this._chk("edit", "enabled", "Wecker aktiv", false)}
    </div>
    <div class="card"><h2>🔊 Ton</h2>
      ${this._chk("edit", "sound.enabled", "Ton abspielen")}
      ${a.sound.enabled ? `
      <div class="ms"><div class="lbl">Sender / Ton</div><select data-o="edit" data-f="sound.station_id">${this._opts(this._data.stations, a.sound.station_id, this._data.stations.length ? null : "– erst unter Einstellungen Sender anlegen –")}</select></div>
      <div class="ms"><div class="lbl">Lautsprecher</div><select data-o="edit" data-f="sound.player" data-r>${this._opts(c.players, a.sound.player, `Standard${stdPlayer ? ` (${stdPlayer})` : ""}`)}</select><p class="hint">${esc(playerHint)}</p></div>
      <div class="grid">
        ${this._num("edit", "sound.volume_start", "Lautstärke Start", 0, 100, "intnull", " %")}
        ${this._num("edit", "sound.volume_end", "Lautstärke Ende", 0, 100, "intnull", " %")}
        ${this._num("edit", "sound.ramp_min", "Anstieg über", 0, 60, "int", " min")}
        ${this._num("edit", "sound.duration_min", "Spielt höchstens", 1, 240, "int", " min")}
      </div><p class="hint">Lautstärke leer lassen = nicht verändern.</p>` : ""}
    </div>
    <div class="card"><h2>🌅 Sonnenaufgang</h2>
      ${this._chk("edit", "light.enabled", "Licht vor dem Wecken langsam heller")}
      ${a.light.enabled ? `${this._ms("edit", "light.entities", c.lights, "Lampen")}
      <div class="grid">${this._num("edit", "light.minutes", "Beginn vorher", 0, 120, "int", " min")}${this._num("edit", "light.brightness", "Helligkeit", 1, 100, "int", " %")}${this._num("edit", "light.kelvin", "Farbtemperatur", 0, 10000, "int", " K")}</div>` : ""}
    </div>
    <div class="card"><h2>🪟 Rollläden</h2>
      ${this._chk("edit", "covers.enabled", "Beim Klingeln öffnen")}
      ${a.covers.enabled ? `${this._ms("edit", "covers.entities", c.covers, "Rollläden")}<div class="grid">${this._num("edit", "covers.position", "Position", 0, 100, "int", " %")}</div>` : ""}
    </div>
    <div class="card"><h2>⚙️ Skript und Vorlauf</h2>
      ${this._chk("edit", "script.enabled", "Beim Klingeln ein Skript starten")}
      ${a.script.enabled ? `<div class="ms"><select data-o="edit" data-f="script.entity">${this._opts(c.scripts, a.script.entity, "– Skript wählen –")}</select></div>` : ""}
      ${this._chk("edit", "pre.enabled", "Vorlauf-Ereignis senden")}
      ${a.pre.enabled ? `<div class="grid">${this._num("edit", "pre.minutes", "Minuten vorher", 1, 240)}</div><p class="hint">Löst das Ereignis <code>wecker_vorlauf</code> aus – z. B. für eine Automation „Bad vorheizen“ oder „Kaffeemaschine an“.</p>` : ""}
    </div>
    <div class="row end">
      ${a.id ? `<button class="link danger" data-act="delete">Löschen</button>` : ""}
      <span style="flex:1"></span>
      <button data-act="cancel">Abbrechen</button>
      <button class="primary inline" data-act="save">Speichern</button>
    </div>`;
  }

  _rEinst() {
    const d = this._data;
    let out = "";
    if (d.profile && this._isParent) out += this._rPerson();
    else if (d.profile) out += `<div class="card muted">Lautsprecher, Push und Kalender für ${esc(d.name)} stellen die Eltern ein.</div>`;
    if (this._isParent) out += this._rStations();
    if (this._isAdmin) out += this._rProfiles() + this._rParents() + this._rBackup();
    return out;
  }

  _rPerson() {
    const c = this._data.choices;
    const s = this._set;
    const notify = c.notify.map((n) => ({ id: n, name: n.replace("notify.", "") }));
    return `<div class="card"><h2>👤 ${esc(this._data.name)}</h2>
      ${this._chk("set", "enabled", "Wecker dieser Person aktiv (Hauptschalter, z. B. für Urlaub)", false)}
      <div class="ms"><div class="lbl">Standard-Lautsprecher</div><select data-o="set" data-f="player">${this._opts(c.players, s.player, "– keiner –")}</select></div>
      ${this._ms("set", "notify", notify, "Push an (Handy)")}
      ${this._chk("set", "push", "Beim Klingeln Push mit Stopp / Schlummern", false)}
      ${this._chk("set", "fallback_push", "Push, wenn der Lautsprecher nicht erreichbar ist", false)}
      <div class="grid">${this._num("set", "snooze_min", "Schlummern", 1, 60, "int", " min")}</div>
      ${this._ms("set", "calendars", c.calendars, "Ausnahme-Kalender (an Tagen mit Termin kein Wecker, z. B. Schulferien, Urlaub)")}
      <div class="ms"><div class="lbl">Werktag-Sensor</div><select data-o="set" data-f="workday_entity">${this._opts(c.workday, s.workday_entity, "automatisch")}</select></div>
      <button class="primary" data-act="savesettings">Speichern</button></div>`;
  }

  _rStations() {
    const rows = this._st.map((s, i) => `<div class="strow">
      <input type="text" placeholder="Name" value="${esc(s.name)}" data-act="st" data-i="${i}" data-k="name">
      <input type="text" placeholder="Alexa-Name (optional)" value="${esc(s.alexa)}" data-act="st" data-i="${i}" data-k="alexa">
      <input type="text" placeholder="Adresse: Stream-URL / Music-Assistant-Link" value="${esc(s.media_id)}" data-act="st" data-i="${i}" data-k="media_id">
      <button class="link danger" data-act="stdel" data-i="${i}">✕</button></div>`).join("");
    return `<div class="card"><h2>📻 Sender und Töne</h2>
      <p class="hint">Alexa-Lautsprecher spielen den Sender per Sprachbefehl (Alexa-Name, sonst Name). Andere Lautsprecher und Music Assistant brauchen eine Adresse.</p>
      ${rows || `<p class="muted">Noch keine Sender.</p>`}
      <button class="link" data-act="staddd">+ Sender</button>
      <div class="ms"><div class="lbl">Alexa-Sprachbefehl</div><input class="txt" type="text" value="${esc(this._tpl)}" data-act="tpl"><p class="hint">{sender} wird durch den Alexa-Namen ersetzt.</p></div>
      <button class="primary" data-act="savestations">Sender speichern</button></div>`;
  }

  _rProfiles() {
    const users = this._data.users || [];
    const uopts = (sel) => `<option value="">– kein Benutzer –</option>` + users.map((u) => `<option value="${esc(u.id)}" ${u.id === sel ? "selected" : ""}>${esc(u.name)}</option>`).join("");
    const rows = this._profDraft.map((p, i) => `<div class="srow">
      <input type="text" value="${esc(p.name)}" maxlength="30" data-act="pname" data-i="${i}">
      <select data-act="puser" data-i="${i}">${uopts(p.user_id)}</select>
      <button class="link" data-act="profsave" data-i="${i}">Speichern</button>
      <button class="link danger" data-act="profdel" data-i="${i}">✕</button></div>`).join("");
    return `<div class="card"><h2>🧑‍🤝‍🧑 Profile</h2>
      <p class="hint">Ein Profil je Person. Mit dem HA-Benutzer verknüpft, sieht die Person nur ihre eigenen Wecker und kann sie selbst stellen.</p>
      ${rows || `<p class="muted">Noch keine Profile.</p>`}
      <div class="srow"><input type="text" placeholder="Name" maxlength="30" value="${esc(this._newProf.name)}" data-o="newProf" data-f="name">
        <select data-o="newProf" data-f="user_id">${uopts(this._newProf.user_id)}</select>
        <button data-act="profadd">+ Anlegen</button></div></div>`;
  }

  _rParents() {
    const users = (this._data.users || []);
    const rows = users.map((u) => `<label class="chk"><input type="checkbox" data-act="parent" data-uid="${esc(u.id)}" ${this._parentsDraft.includes(u.id) ? "checked" : ""}> ${esc(u.name)}</label>`).join("");
    return `<div class="card"><h2>👪 Eltern</h2>
      <p class="hint">Eltern sehen alle Profile, können oben umschalten und Einstellungen ändern. Administratoren dürfen das immer.</p>
      ${rows}<button class="primary" data-act="saveparents">Eltern speichern</button></div>`;
  }

  _rBackup() {
    return `<div class="card"><h2>💾 Sichern und Übertragen</h2>
      <p class="hint">Alle Profile, Wecker und Sender als Datei – zum Sichern oder für eine neue Installation.</p>
      <div class="row"><button data-act="export">Exportieren</button>
      <label class="filebtn">Importieren …<input type="file" accept="application/json,.json" data-act="import"></label></div></div>`;
  }

}

const CSS = `
:host { display:block; background:var(--primary-background-color); color:var(--primary-text-color); min-height:100vh; font-family:var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
header { display:flex; align-items:center; gap:8px; height:56px; padding:0 8px; background:var(--app-header-background-color, var(--primary-color)); color:var(--app-header-text-color, #fff); }
header h1 { font-size:20px; font-weight:400; margin:0; flex:1; }
.pswitch { display:flex; gap:4px; background:rgba(255,255,255,.15); border-radius:18px; padding:3px; overflow-x:auto; max-width:70vw; }
.pswitch button { border:0; border-radius:15px; padding:5px 12px; background:none; color:inherit; font-size:14px; white-space:nowrap; }
.pswitch button.on { background:var(--card-background-color); color:var(--primary-text-color); font-weight:500; }
nav { display:flex; background:var(--card-background-color); border-bottom:1px solid var(--divider-color); }
.tab { flex:1; padding:12px 8px; border:0; background:none; color:var(--secondary-text-color); font-size:15px; border-bottom:3px solid transparent; }
.tab.on { color:var(--primary-color); border-bottom-color:var(--primary-color); font-weight:500; }
main { max-width:760px; margin:0 auto; padding:12px 16px 80px; box-sizing:border-box; }
.card { background:var(--card-background-color); border-radius:12px; padding:12px 14px; margin:10px 0; box-shadow:var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.15)); }
.card.err, .err { color:var(--error-color); }
.card.warn, .warn { color:var(--warning-color, #f57c00); }
h2 { font-size:17px; margin:2px 0 10px; font-weight:500; }
button { font:inherit; border-radius:8px; border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color); padding:8px 12px; cursor:pointer; }
button.primary { background:var(--primary-color); color:var(--text-primary-color, #fff); border-color:var(--primary-color); width:100%; padding:12px; font-size:16px; margin-top:10px; }
button.primary.inline { width:auto; margin-top:0; }
button.link { border:0; background:none; color:var(--primary-color); padding:4px 6px; }
.danger, button.danger { color:var(--error-color); }
.seg { border-radius:16px; padding:6px 12px; }
.seg.on { background:var(--primary-color); color:var(--text-primary-color,#fff); border-color:var(--primary-color); }
.seg.day { min-width:44px; padding:6px 0; }
.row { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:8px 0; }
.row.sm { margin:2px 0 0 -6px; gap:0; }
.row.end { margin-top:14px; }
.muted { color:var(--secondary-text-color); }
.hint { color:var(--secondary-text-color); font-size:13px; margin:4px 0; }
.small { font-size:13px; }
.alarm { display:flex; align-items:center; gap:10px; background:var(--card-background-color); border-radius:12px; padding:10px 14px; margin:10px 0; box-shadow:var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.15)); }
.alarm.dim .time, .alarm.dim .meta b { opacity:.45; }
.al { flex:1; min-width:0; }
.time { font-size:40px; font-weight:300; line-height:1.1; }
.meta { font-size:14px; color:var(--secondary-text-color); margin:2px 0; }
.meta b { color:var(--primary-text-color); font-weight:500; }
.ex { letter-spacing:2px; }
.sw { position:relative; width:52px; height:30px; flex:none; }
.sw input { opacity:0; width:0; height:0; }
.sw span { position:absolute; inset:0; background:var(--divider-color); border-radius:15px; transition:.2s; cursor:pointer; }
.sw span:before { content:""; position:absolute; width:24px; height:24px; left:3px; top:3px; background:#fff; border-radius:50%; transition:.2s; box-shadow:0 1px 3px rgba(0,0,0,.3); }
.sw input:checked + span { background:var(--primary-color); }
.sw input:checked + span:before { transform:translateX(22px); }
.ring { background:var(--error-color, #db4437); color:#fff; border-radius:12px; padding:14px; margin:10px 0; text-align:center; animation:pulse 1.4s infinite; }
.ring.sn { background:var(--info-color, #039be5); animation:none; }
.ring .rt { font-size:20px; font-weight:500; }
.ring .row { justify-content:center; }
.ring button.big { font-size:18px; padding:12px 22px; border:0; background:rgba(255,255,255,.2); color:#fff; }
.ring button.stop { background:#fff; color:#b71c1c; font-weight:600; }
@keyframes pulse { 50% { opacity:.8; } }
.txt, .tm, select, input[type=text], input[type=date], input[type=number] { font:inherit; padding:8px; border-radius:8px; border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color); box-sizing:border-box; }
.txt { flex:1; min-width:0; }
.tm { font-size:22px; padding:4px 8px; }
.ms { margin:10px 0; }
.ms .lbl { font-size:14px; font-weight:500; margin-bottom:4px; }
.ms select { width:100%; }
.chips { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:6px; }
.chip { display:inline-flex; align-items:center; gap:2px; background:var(--primary-background-color); border:1px solid var(--divider-color); border-radius:16px; padding:2px 4px 2px 10px; font-size:13px; }
.chip .x { border:0; background:none; padding:2px 6px; color:var(--secondary-text-color); }
.chk { display:flex; gap:8px; align-items:center; margin:8px 0; font-size:15px; }
.chk input { width:20px; height:20px; flex:none; }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:8px; margin:8px 0; }
.num { display:flex; flex-direction:column; font-size:13px; color:var(--secondary-text-color); gap:2px; }
.num input { width:90px; }
.srow, .strow { display:flex; gap:6px; align-items:center; margin:6px 0; flex-wrap:wrap; }
.srow input[type=text], .srow select { flex:1; min-width:120px; }
.strow input { flex:1; min-width:110px; }
.strow input:nth-child(3) { flex:2; min-width:180px; }
.filebtn { border:1px solid var(--divider-color); border-radius:8px; padding:8px 12px; cursor:pointer; }
.filebtn input { display:none; }
code { font-size:12px; background:var(--primary-background-color); padding:1px 4px; border-radius:4px; }
.toast { position:fixed; left:50%; bottom:24px; transform:translateX(-50%); background:#323232; color:#fff; padding:10px 18px; border-radius:8px; font-size:14px; z-index:10; max-width:90vw; }
`;

customElements.define("wecker-panel", WeckerPanel);

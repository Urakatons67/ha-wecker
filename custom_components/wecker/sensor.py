"""Sensor „Nächster Wecker“ je Profil (Zeitstempel)."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from .const import DOMAIN
from .entity import WeckerEntity, profile_ids


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    rt = hass.data[DOMAIN]
    async_add_entities(NextAlarmSensor(rt, pid) for pid in profile_ids(rt))


class NextAlarmSensor(WeckerEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:alarm"

    def __init__(self, rt, pid: str) -> None:
        super().__init__(rt, pid, "naechster")

    @property
    def native_value(self):
        nxt = self.rt.engine.profile_next(self.pid)
        return nxt[0] if nxt else None

    @property
    def extra_state_attributes(self) -> dict:
        nxt = self.rt.engine.profile_next(self.pid)
        prof = self.profile or {}
        alarm = prof.get("alarms", {}).get(nxt[1], {}) if nxt else {}
        return {"wecker": alarm.get("name"), "wecker_id": nxt[1] if nxt else None,
                "aktive_wecker": sum(1 for (p, _a) in self.rt.engine.alarm_next if p == self.pid)}

"""„Klingelt“ je Profil: an, solange der Wecker klingelt oder schlummert."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity

from .const import DOMAIN
from .entity import WeckerEntity, profile_ids


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    rt = hass.data[DOMAIN]
    async_add_entities(RingingSensor(rt, pid) for pid in profile_ids(rt))


class RingingSensor(WeckerEntity, BinarySensorEntity):
    _attr_icon = "mdi:alarm-light"

    def __init__(self, rt, pid: str) -> None:
        super().__init__(rt, pid, "klingelt")

    @property
    def is_on(self) -> bool:
        return self.pid in self.rt.engine.ringing

    @property
    def extra_state_attributes(self) -> dict:
        r = self.rt.engine.ringing.get(self.pid)
        if not r:
            return {"status": "aus"}
        alarm = (self.profile or {}).get("alarms", {}).get(r.aid, {})
        return {"status": r.status, "wecker": alarm.get("name"), "seit": r.started.isoformat(),
                "schlummer": r.snoozes, "test": r.test}

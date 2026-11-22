"""Hauptschalter je Profil (z. B. im Urlaub alle Wecker der Person aus)."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity

from .const import DOMAIN
from .entity import WeckerEntity, profile_ids


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    rt = hass.data[DOMAIN]
    async_add_entities(MasterSwitch(rt, pid) for pid in profile_ids(rt))


class MasterSwitch(WeckerEntity, SwitchEntity):
    _attr_icon = "mdi:alarm-check"

    def __init__(self, rt, pid: str) -> None:
        super().__init__(rt, pid, "aktiv")

    @property
    def is_on(self) -> bool:
        return bool((self.profile or {}).get("settings", {}).get("enabled", True))

    async def _set(self, on: bool) -> None:
        if self.profile is None:
            return
        self.profile["settings"]["enabled"] = on
        self.rt.store.save()
        self.rt.engine.request_reschedule()
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)

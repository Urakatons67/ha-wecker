"""Tasten Stopp und Schlummern je Profil."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .const import DOMAIN
from .entity import WeckerEntity, profile_ids


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    rt = hass.data[DOMAIN]
    ents = []
    for pid in profile_ids(rt):
        ents += [StopButton(rt, pid), SnoozeButton(rt, pid)]
    async_add_entities(ents)


class StopButton(WeckerEntity, ButtonEntity):
    _attr_icon = "mdi:alarm-off"

    def __init__(self, rt, pid: str) -> None:
        super().__init__(rt, pid, "stopp")

    async def async_press(self) -> None:
        await self.rt.engine.async_stop(self.pid, "taste")


class SnoozeButton(WeckerEntity, ButtonEntity):
    _attr_icon = "mdi:alarm-snooze"

    def __init__(self, rt, pid: str) -> None:
        super().__init__(rt, pid, "schlummern")

    async def async_press(self) -> None:
        await self.rt.engine.async_snooze(self.pid)

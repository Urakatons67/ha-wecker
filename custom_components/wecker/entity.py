"""Gemeinsame Basis: ein Gerät „Wecker <Name>“ je Profil."""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_UPDATE, VERSION


class WeckerEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, rt, pid: str, key: str) -> None:
        self.rt = rt
        self.pid = pid
        self._attr_unique_id = f"{pid}_{key}"
        self._attr_translation_key = key
        name = rt.store.data["profiles"][pid].get("name") or pid
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, pid)}, name=f"Wecker {name}", manufacturer="Wecker",
            model="Profil", sw_version=VERSION)

    @property
    def profile(self) -> dict | None:
        return self.rt.store.data["profiles"].get(self.pid)

    @property
    def available(self) -> bool:
        return self.profile is not None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._refresh))

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()


def profile_ids(rt) -> list[str]:
    return list(rt.store.data["profiles"])

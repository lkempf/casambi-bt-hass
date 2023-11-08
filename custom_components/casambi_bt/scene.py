"""Support for scenes."""

from __future__ import annotations

import logging
from typing import Any

from CasambiBt import Scene

from . import CasambiApi

from .const import (
    DOMAIN,
    IDENTIFIER_NETWORK_ID,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.components.scene import Scene as SceneEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    casa_api: CasambiApi = hass.data[DOMAIN][config_entry.entry_id]

    scenes = [CasambiScene(casa_api, scene) for scene in casa_api.get_scenes()]
    async_add_entities(scenes)


class CasambiScene(SceneEntity):
    def __init__(self, api: CasambiApi, scene: Scene) -> None:
        self._api = api
        self.scene = scene

    @property
    def name(self) -> str:
        return self.scene.name

    @property
    def unique_id(self) -> str:
        return f"{self._api.casa.networkId}-scene-{self.scene.sceneId}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(IDENTIFIER_NETWORK_ID, self._api.casa.networkId)},
        )

    async def async_activate(self, **kwargs: Any) -> None:
        _LOGGER.info(f"Switching to scene {self.name}")
        brightness = kwargs.get(ATTR_BRIGHTNESS, 0xFF)
        await self._api.casa.switchToScene(self.scene, brightness)

"""Support for scenes."""

from __future__ import annotations

import logging
from typing import Any

from CasambiBt import Scene

from . import CasambiApi

from .const import (
    DOMAIN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.components.scene import Scene as SceneEntity

from .entities import CasambiEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    casa_api: CasambiApi = hass.data[DOMAIN][config_entry.entry_id]

    scenes = [CasambiScene(casa_api, scene) for scene in casa_api.get_scenes()]
    async_add_entities(scenes)


class CasambiScene(SceneEntity, CasambiEntity):

    _attr_should_poll = True

    def __init__(self, api: CasambiApi, scene: Scene) -> None:
        super().__init__(api, scene, EntityDescription(key=scene.sceneId, name=scene.name))

    async def async_activate(self, **kwargs: Any) -> None:
        _LOGGER.info(f"Switching to scene {self.name}")
        brightness = kwargs.get(ATTR_BRIGHTNESS, 0xFF)
        await self._api.casa.switchToScene(self.scene, brightness)

"""Support for scenes."""

from __future__ import annotations

import logging
from typing import Any

from CasambiBt import Scene

from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.components.scene import Scene as SceneEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CasambiApi
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the Casambi scene entities."""
    casa_api: CasambiApi = hass.data[DOMAIN][config_entry.entry_id]

    scenes = [CasambiScene(casa_api, scene) for scene in casa_api.get_scenes()]
    async_add_entities(scenes)


class CasambiScene(SceneEntity):
    """Defines a Casambi scene entity."""

    def __init__(self, api: CasambiApi, scene: Scene) -> None:
        """Initialize a Casambi scene entity."""
        self._api = api
        self.scene = scene

    @property
    def name(self) -> str:
        """Return the name of the scene."""
        return self.scene.name

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self._api.casa.networkId}-scene-{self.scene.sceneId}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info of the network device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._api.casa.networkId)},
        )

    async def async_activate(self, **kwargs: Any) -> None:
        """Activate a scene."""
        _LOGGER.info("Switching to scene %s", self.name)
        brightness = kwargs.get(ATTR_BRIGHTNESS, 0xFF)
        await self._api.casa.switchToScene(self.scene, brightness)

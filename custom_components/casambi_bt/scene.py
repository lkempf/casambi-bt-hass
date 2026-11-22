"""Support for scenes."""

import logging
from typing import Any

from CasambiBt import Scene

from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.components.scene import Scene as SceneEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CasambiApi, CasambiConfigEntry
from .entity import CasambiNetworkEntity

_LOGGER = logging.getLogger(__name__)

# Commands share one bluetooth connection which the library already
# serializes, so don't add another limit on top.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: CasambiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the Casambi scene entities."""
    casa_api = config_entry.runtime_data

    scenes = [CasambiScene(casa_api, scene) for scene in casa_api.get_scenes()]
    async_add_entities(scenes)


class CasambiScene(SceneEntity, CasambiNetworkEntity):
    """Defines a Casambi scene entity."""

    def __init__(self, api: CasambiApi, scene: Scene) -> None:
        """Initialize a Casambi scene entity."""
        self._obj: Scene
        super().__init__(
            api=api,
            description=EntityDescription(key=str(scene.sceneId), name=scene.name),
            obj=scene,
        )

    async def async_activate(self, **kwargs: Any) -> None:
        """Activate a scene."""
        _LOGGER.info("Switching to scene %s", self.name)
        brightness = kwargs.get(ATTR_BRIGHTNESS, 0xFF)
        await self._api.casa.switchToScene(self._obj, brightness)

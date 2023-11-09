import logging

from typing import Final

from homeassistant.helpers import device_registry
from homeassistant.helpers.entity import DeviceInfo, Entity, EntityDescription

from . import DOMAIN, CasambiApi

_LOGGER: Final = logging.getLogger(__name__)


class CasambiEntity(Entity):
    """Defines a Casambi Entity."""

    entity_description: EntityDescription
    _attr_has_entity_name = True

    def __init__(self, api: CasambiApi, description: EntityDescription):
        """Initialize Casambi Entity."""
        self.entity_description = description
        self._api = api
        self._attr_name = description.name

    @property
    def unique_id(self) -> str:
        """Return the unique ID for this entity."""
        name = f"{self._api.casa.networkId}"
        if hasattr(self, "_attr_name"):
            name += f"-{self._attr_name}"
        return name.lower()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this Casambi entity."""
        # return device info for network
        return DeviceInfo(
            name=self._api.casa.networkName,
            manufacturer="Casambi",
            model="Network",
            identifiers={(DOMAIN, self._api.casa.networkId)},
            connections={(device_registry.CONNECTION_BLUETOOTH, self._api.address)}
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._api.available

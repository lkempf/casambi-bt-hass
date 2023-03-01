import logging

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import callback
from homeassistant.helpers.entity import EntityCategory

from CasambiBt import Unit

from .CasambiBinarySensorEntity import CasambiBinarySensorEntity
from .. import CasambiApi

_LOGGER = logging.getLogger(__name__)


class CasambiStatusBinarySensorEntity(CasambiBinarySensorEntity):
    """Defines a Casambi Status Binary Sensor Entity."""

    def __init__(self, unit: Unit, api: CasambiApi):
        CasambiBinarySensorEntity.__init__(self, unit, api, "Status", BinarySensorDeviceClass.CONNECTIVITY)

    @property
    def entity_category(self):
        """Getter for entity category."""
        return EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """Getter for entity availability, status/connectivity entity is always available."""
        return True

    @callback
    def change_callback(self, unit: Unit) -> None:
        _LOGGER.debug("Handling state change for unit %i", unit.deviceId)
        if unit.state:
            self._unit = unit
        else:
            self._unit.online = unit.online
        return super().change_callback(unit)

    async def async_added_to_hass(self) -> None:
        self._api.register_unit_updates(self._unit, self.change_callback)

    async def async_will_remove_from_hass(self) -> None:
        self._api.unregister_unit_updates(self._unit, self.change_callback)

    @property
    def state(self):
        """Getter for state."""
        return STATE_ON if super().available and self._unit.online else STATE_OFF

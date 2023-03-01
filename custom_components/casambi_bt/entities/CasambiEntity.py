import logging

from typing import Final

from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo, Entity

from CasambiBt import Unit

from .. import DOMAIN, CasambiApi

_LOGGER: Final = logging.getLogger(__name__)


class CasambiEntity(Entity):
    """Defines a Casambi Entity."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, unit: Unit, api: CasambiApi, name: str = None):
        """Initialize Casambi Entity."""
        Entity.__init__(self)
        self._unit = unit
        self._api = api
        self._attr_name = name

    @property
    def unique_id(self) -> str:
        """Return the unique ID for this entity."""
        name = f"{self._api.casa.networkId}_{self._unit.uuid}"
        if self._attr_name:
            name += f"_{self._attr_name}"
        return name.lower()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this Casambi entity."""
        return DeviceInfo(
            name=self._unit.name,
            manufacturer=self._unit.unitType.manufacturer,
            model=self._unit.unitType.model,
            sw_version=self._unit.firmwareVersion,
            identifiers={(DOMAIN, self._unit.uuid)},
            via_device=(DOMAIN, self._api.casa.networkId),
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._api.available

    @callback
    def change_callback(self, unit: Unit) -> None:
        self.schedule_update_ha_state(False)

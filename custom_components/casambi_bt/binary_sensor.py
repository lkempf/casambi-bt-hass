"""Binary Sensor implementation for Casambi"""
import logging

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN, CasambiApi
from .entities import CasambiEntity

_LOGGER = logging.getLogger(__name__)


NETWORK_SENSORS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key="status",
        name="Status",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

async def async_unload_entry(_hass: HomeAssistant, _entry: ConfigEntry) -> bool:
    """Support unloading of entry"""
    return True

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setting up binary sensor"""
    _LOGGER.debug("Setting up binary sensor entities. config_entry: %s", config_entry)
    api: CasambiApi = hass.data[DOMAIN][config_entry.entry_id]
    binary_sensors = []

    # create network sensors
    for description in NETWORK_SENSORS:
        _LOGGER.debug("Adding CasambiBinarySensorEntity for network...")
        binary_sensors.append(CasambiBinarySensorEntity(api, description))

    if binary_sensors:
        _LOGGER.debug("Adding binary sensor entities...")
        async_add_entities(binary_sensors)
    else:
        _LOGGER.debug("No binary sensor entities available.")

    return True


class CasambiBinarySensorEntity(BinarySensorEntity, CasambiEntity):
    """Defines a Casambi Binary Sensor Entity."""

    entity_description: BinarySensorEntityDescription
    _attr_is_on = False

    def __init__(self, api: CasambiApi, description: BinarySensorEntityDescription):
        super().__init__(api, description)
        self.entity_description = description

    @property
    def state(self):
        """Getter for state."""
        return STATE_ON if self._api.available else STATE_OFF

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return True

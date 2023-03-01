"""Binary Sensor implementation for Casambi"""
import logging

from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from CasambiBt import UnitControlType

from . import DOMAIN, CasambiApi
from .entities.CasambiStatusBinarySensorEntity import CasambiStatusBinarySensorEntity

CASA_LIGHT_CTRL_TYPES: Final[list[UnitControlType]] = [
    UnitControlType.DIMMER,
    UnitControlType.RGB,
    UnitControlType.WHITE,
]

_LOGGER = logging.getLogger(__name__)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Support unloading of entry
    """
    return True

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setting up binary sensor"""
    _LOGGER.debug(f"Setting up binary sensor entities. config_entry:{config_entry}")
    api: CasambiApi = hass.data[DOMAIN][config_entry.entry_id]
    binary_sensors = []

    for unit in api.get_units(CASA_LIGHT_CTRL_TYPES):
        _LOGGER.debug("Adding CasambiStatusBinarySensorEntity...")
        binary_sensors.append(CasambiStatusBinarySensorEntity(unit, api))

    if binary_sensors:
        _LOGGER.debug("Adding binary sensor entities...")
        async_add_entities(binary_sensors)
    else:
        _LOGGER.debug("No binary sensor entities available.")

    return True

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity

from CasambiBt import Unit

from .CasambiEntity import CasambiEntity
from .. import CasambiApi

_LOGGER = logging.getLogger(__name__)


class CasambiBinarySensorEntity(BinarySensorEntity, CasambiEntity):
    """Defines a Casambi Binary Sensor Entity."""

    _attr_is_on = False

    def __init__(self, unit: Unit, api: CasambiApi, name: str = None, device_class = None, icon = None):
        BinarySensorEntity.__init__(self)
        CasambiEntity.__init__(self, unit, api, name)
        self._attr_device_class = device_class
        self._attr_icon = icon

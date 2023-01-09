"""Support for Casambi compatible lights."""

from __future__ import annotations

from abc import ABCMeta
from copy import copy
import logging
from typing import Any, Final, cast

from CasambiBt import Group, Unit, UnitControlType, UnitState

from .const import (
    CONF_IMPORT_GROUPS,
    IDENTIFIER_NETWORK_ID,
)
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_COLOR_TEMP_KELVIN,
    COLOR_MODE_BRIGHTNESS,
    COLOR_MODE_ONOFF,
    COLOR_MODE_RGB,
    COLOR_MODE_RGBW,
    COLOR_MODE_COLOR_TEMP,
    COLOR_MODE_UNKNOWN,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN, CasambiApi

CASA_LIGHT_CTRL_TYPES: Final[list[UnitControlType]] = [
    UnitControlType.DIMMER,
    UnitControlType.RGB,
    UnitControlType.WHITE,
]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    casa_api: CasambiApi = hass.data[DOMAIN][config_entry.entry_id]

    light_entities: list[CasambiLight] = [
        CasambiLightUnit(casa_api, u) for u in casa_api.get_units(CASA_LIGHT_CTRL_TYPES)
    ]

    group_entities: list[CasambiLight] = []
    if config_entry.data[CONF_IMPORT_GROUPS]:
        group_entities = [CasambiLightGroup(casa_api, g) for g in casa_api.get_groups()]

    async_add_entities(light_entities + group_entities)


class CasambiLight(LightEntity, metaclass=ABCMeta):
    def __init__(self, api: CasambiApi, obj: Group | Unit) -> None:
        self._api = api
        self._obj = obj

        # Effects and transitions aren't supported
        self._attr_supported_features = 0
        self._attr_should_poll = False

        self._attr_color_mode = self._mode_helper(self.supported_color_modes)

    @property
    def available(self) -> bool:
        return self._api.available

    @callback
    def change_callback(self, unit: Unit) -> None:
        self.schedule_update_ha_state(False)

    def _capabilities_helper(self, unit: Unit) -> set[str]:
        supported: set[str] = set()
        unit_modes = [uc.type for uc in unit.unitType.controls]

        if UnitControlType.RGB in unit_modes and UnitControlType.WHITE in unit_modes:
            supported.add(COLOR_MODE_RGBW)
        elif UnitControlType.RGB in unit_modes:
            supported.add(COLOR_MODE_RGB)
        if UnitControlType.DIMMER in unit_modes:
            supported.add(COLOR_MODE_BRIGHTNESS)
            supported.add(COLOR_MODE_ONOFF)
        elif UnitControlType.ONOFF in unit_modes:
            supported.add(COLOR_MODE_ONOFF)
        if UnitControlType.TEMPERATURE in unit_modes:
            supported.add(COLOR_MODE_COLOR_TEMP)

        if len(supported) == 0:
            supported.add(COLOR_MODE_UNKNOWN)

        return supported

    def _mode_helper(self, modes: set[ColorMode] | set[str] | None) -> str:
        if not modes:
            return COLOR_MODE_UNKNOWN

        if COLOR_MODE_RGBW in modes:
            return COLOR_MODE_RGBW
        elif COLOR_MODE_RGB in modes:
            return COLOR_MODE_RGB
        elif COLOR_MODE_COLOR_TEMP in modes:
            return COLOR_MODE_COLOR_TEMP
        elif COLOR_MODE_BRIGHTNESS in modes:
            return COLOR_MODE_BRIGHTNESS
        elif COLOR_MODE_ONOFF in modes:
            return COLOR_MODE_ONOFF
        else:
            return COLOR_MODE_UNKNOWN

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._api.casa.setLevel(self._obj, 0)


class CasambiLightUnit(CasambiLight):
    def __init__(self, api: CasambiApi, unit: Unit) -> None:
        self._attr_supported_color_modes = self._capabilities_helper(unit)
        self._attr_name = None
        self._attr_has_entity_name = True

        temp_control = unit.unitType.get_control(UnitControlType.TEMPERATURE)
        self._attr_min_color_temp_kelvin = temp_control.min
        self._attr_max_color_temp_kelvin = temp_control.max

        super().__init__(api, unit)

    @property
    def unique_id(self) -> str:
        """Return an unique identifier for the unit."""
        unit = cast(Unit, self._obj)
        return f"{self._api.casa.networkId}-unit-{unit.uuid}-light"

    @property
    def device_info(self) -> DeviceInfo:
        unit = cast(Unit, self._obj)
        return DeviceInfo(
            name=unit.name,
            manufacturer=unit.unitType.manufacturer,
            model=unit.unitType.model,
            sw_version=unit.firmwareVersion,
            identifiers={(DOMAIN, unit.uuid)},
            via_device=(DOMAIN, self._api.casa.networkId),
        )

    @property
    def is_on(self) -> bool:
        unit = cast(Unit, self._obj)
        return unit.is_on

    @property
    def brightness(self) -> int | None:
        unit = cast(Unit, self._obj)
        return unit.state.dimmer

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        unit = cast(Unit, self._obj)
        return unit.state.rgb

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        unit = cast(Unit, self._obj)
        return (*unit.state.rgb, unit.state.white)  # type: ignore[return-value]

    @property
    def color_temp_kelvin(self) -> int | None:
        unit = cast(Unit, self._obj)
        return unit.state.temperature

    @property
    def available(self) -> bool:
        unit = cast(Unit, self._obj)
        return super().available and unit.online

    @callback
    def change_callback(self, unit: Unit) -> None:
        _LOGGER.debug("Handling state change for unit %i", unit.deviceId)
        if unit.state:
            self._obj = unit
        else:
            own_unit = cast(Unit, self._obj)
            own_unit.online = unit.online
        return super().change_callback(unit)

    async def async_added_to_hass(self) -> None:
        unit = cast(Unit, self._obj)
        self._api.register_unit_updates(unit, self.change_callback)

    async def async_will_remove_from_hass(self) -> None:
        unit = cast(Unit, self._obj)
        self._api.unregister_unit_updates(unit, self.change_callback)

    async def async_turn_on(self, **kwargs: Any) -> None:
        unit = cast(Unit, self._obj)
        state = copy(unit.state)
        if not state:
            state = UnitState()

        # According to docs (https://developers.home-assistant.io/docs/core/entity/light#turn-on-light-device)
        # we only ever get a single color attribute but there may be other non-color ones.
        set_state = False
        if ATTR_BRIGHTNESS in kwargs:
            state.dimmer = kwargs[ATTR_BRIGHTNESS]
            set_state = True
        if ATTR_RGBW_COLOR in kwargs:
            state.rgb = kwargs[ATTR_RGBW_COLOR][:3]
            state.white = kwargs[ATTR_RGBW_COLOR][3]
            set_state = True
        if ATTR_RGB_COLOR in kwargs:
            state.rgb = kwargs[ATTR_RGB_COLOR]
            set_state = True
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            state.temperature = kwargs[ATTR_COLOR_TEMP_KELVIN]
            set_state = True

        if set_state:
            await self._api.casa.setUnitState(self._obj, state)
        else:
            await self._api.casa.turnOn(self._obj)


class CasambiLightGroup(CasambiLight):
    def __init__(self, api: CasambiApi, group: Group) -> None:
        # Find union of supported color modes.
        supported_modes = set()
        for unit in group.units:
            supported_modes = supported_modes.union(self._capabilities_helper(unit))

        # Color temperature for groups isn't supported yet.
        # Oen problems:
        #  - How do we determin min and max temperature? Is it the union or intersection of the intervals?
        #  - How does the SetTemperature opcode work (for casambi-bt)?
        #    We can't really scale the temperature since we don't have a min or max.
        if COLOR_MODE_COLOR_TEMP in supported_modes:
            supported_modes.remove(COLOR_MODE_COLOR_TEMP)

        if len(supported_modes) == 0:
            supported_modes.add(COLOR_MODE_UNKNOWN)
        self._attr_supported_color_modes = supported_modes
        self._attr_name = group.name
        super().__init__(api, group)

    @property
    def unique_id(self) -> str:
        group = cast(Group, self._obj)
        return f"{self._api.casa.networkId}-group-{group.groudId}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(IDENTIFIER_NETWORK_ID, self._api.casa.networkId)},
        )

    @property
    def is_on(self) -> bool:
        group = cast(Group, self._obj)
        return any([u.is_on for u in group.units])

    @property
    def brightness(self) -> int | None:
        group = cast(Group, self._obj)
        for unit in group.units:
            if unit.unitType.get_control(UnitControlType.DIMMER):
                return unit.state.dimmer

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        group = cast(Group, self._obj)
        for unit in group.units:
            if unit.unitType.get_control(UnitControlType.RGB):
                return unit.state.rgb

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        group = cast(Group, self._obj)
        for unit in group.units:
            if unit.unitType.get_control(UnitControlType.DIMMER):
                return (*unit.state.rgb, unit.state.white)  # type: ignore[return-value]

    async def async_turn_on(self, **kwargs: Any) -> None:
        was_set = False
        if ATTR_BRIGHTNESS in kwargs:
            await self._api.casa.setLevel(self._obj, kwargs[ATTR_BRIGHTNESS])
            was_set = True
        if ATTR_RGB_COLOR in kwargs:
            await self._api.casa.setColor(self._obj, kwargs[ATTR_RGB_COLOR])
            was_set = True
        elif ATTR_RGBW_COLOR in kwargs:
            rgb, w = kwargs[ATTR_RGBW_COLOR][:3], kwargs[ATTR_RGBW_COLOR][3]
            await self._api.casa.setColor(self._obj, rgb)
            await self._api.casa.setWhite(self._obj, w)
            was_set = True

        if not was_set:
            await self._api.casa.turnOn(self._obj)
        elif ATTR_BRIGHTNESS not in kwargs:
            # Sync brightness for group so that everything turns on.
            # This might be a bit confusing because the rest isn't synced.
            await self._api.casa.setLevel(self._obj, self.brightness)

    async def async_added_to_hass(self) -> None:
        group = cast(Group, self._obj)
        for unit in group.units:
            self._api.register_unit_updates(unit, self.change_callback)

    async def async_will_remove_from_hass(self) -> None:
        group = cast(Group, self._obj)
        for unit in group.units:
            self._api.unregister_unit_updates(unit, self.change_callback)

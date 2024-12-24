"""Support for Casambi compatible lights."""

from __future__ import annotations

from abc import ABCMeta
from copy import copy
import logging
from typing import Any, Final, cast

from CasambiBt import Group, Unit, UnitControlType, UnitState, _operation

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_XY_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CasambiApi
from .const import CONF_IMPORT_GROUPS, DOMAIN
from .entities import (
    CasambiEntity,
    CasambiNetworkGroup,
    CasambiUnitEntity,
    TypedEntityDescription,
)

CASA_LIGHT_CTRL_TYPES: Final[list[UnitControlType]] = [
    UnitControlType.DIMMER,
    UnitControlType.RGB,
    UnitControlType.WHITE,
    UnitControlType.ONOFF,
    UnitControlType.TEMPERATURE,
]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the Casambi light entities."""
    casa_api: CasambiApi = hass.data[DOMAIN][config_entry.entry_id]

    light_entities: list[CasambiLight] = [
        CasambiLightUnit(casa_api, u) for u in casa_api.get_units(CASA_LIGHT_CTRL_TYPES)
    ]

    group_entities: list[CasambiLight] = []
    if config_entry.data[CONF_IMPORT_GROUPS]:
        group_entities = [CasambiLightGroup(casa_api, g) for g in casa_api.get_groups()]

    async_add_entities(light_entities + group_entities)


class CasambiLight(CasambiEntity, LightEntity, metaclass=ABCMeta):
    """Defines a Casambi light entity base class.

    This class contains common functionality for units and groups.
    """

    def __init__(
        self, api: CasambiApi, description: TypedEntityDescription, obj: Group | Unit
    ) -> None:
        """Initialize a Casambi light entity base class."""

        # Effects and transitions aren't supported
        self._attr_supported_features = LightEntityFeature(0)

        self._attr_color_mode = self._mode_helper(self.supported_color_modes)

        self._obj: Group | Unit
        super().__init__(api, description, obj)

    def _capabilities_helper(self, unit: Unit) -> set[str]:
        supported: set[str] = set()
        unit_modes = [uc.type for uc in unit.unitType.controls]

        if UnitControlType.RGB in unit_modes and UnitControlType.WHITE in unit_modes:
            supported.add(ColorMode.RGBW)
        elif UnitControlType.RGB in unit_modes:
            supported.add(ColorMode.RGB)
        if UnitControlType.TEMPERATURE in unit_modes:
            supported.add(ColorMode.COLOR_TEMP)
        if UnitControlType.XY in unit_modes:
            supported.add(ColorMode.XY)

        if len(supported) == 0:
            if UnitControlType.DIMMER in unit_modes:
                supported.add(ColorMode.BRIGHTNESS)
            elif UnitControlType.ONOFF in unit_modes:
                supported.add(ColorMode.ONOFF)
            else:
                supported.add(ColorMode.UNKNOWN)

        return supported

    def _mode_helper(self, modes: set[ColorMode] | set[str] | None) -> str:
        if modes:
            if ColorMode.RGBW in modes:
                return ColorMode.RGBW
            if ColorMode.RGB in modes:
                return ColorMode.RGB
            if ColorMode.XY in modes:
                return ColorMode.XY
            if ColorMode.COLOR_TEMP in modes:
                return ColorMode.COLOR_TEMP
            if ColorMode.BRIGHTNESS in modes:
                return ColorMode.BRIGHTNESS
            if ColorMode.ONOFF in modes:
                return ColorMode.ONOFF
        return ColorMode.UNKNOWN

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity of."""
        await self._api.casa.setLevel(self._obj, 0)


class CasambiLightUnit(CasambiLight, CasambiUnitEntity):
    """Defines a Casambi light entity."""

    def __init__(self, api: CasambiApi, unit: Unit) -> None:
        """Initialize a Casambi light entity."""
        self._attr_supported_color_modes = self._capabilities_helper(unit)

        temp_control = unit.unitType.get_control(UnitControlType.TEMPERATURE)
        if temp_control is not None:
            self._attr_min_color_temp_kelvin = temp_control.min
            self._attr_max_color_temp_kelvin = temp_control.max

        desc = TypedEntityDescription(key=unit.uuid, entity_type="light")

        self._obj: Unit
        super().__init__(api, desc, unit)

    @property
    def is_on(self) -> bool:
        """Return True if the unit is on."""
        return self._obj.is_on

    @property
    def brightness(self) -> int | None:
        """Return the brightness of the unit."""
        unit = cast(Unit, self._obj)
        if unit.state is not None:
            return unit.state.dimmer
        return None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the rgb color of the unit."""
        unit = cast(Unit, self._obj)
        if unit.state is not None:
            return unit.state.rgb
        return None

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """Return the rgbw color of the unit."""
        unit = cast(Unit, self._obj)
        if (
            unit.state is not None
            and unit.state.rgb is not None
            and unit.state.white is not None
        ):
            return (*unit.state.rgb, unit.state.white)
        return None

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the color temperature in Kelvin."""
        unit = cast(Unit, self._obj)
        if unit.state is not None:
            return unit.state.temperature
        return None

    @property
    def xy_color(self) -> tuple[float, float] | None:
        """Return the XY color value."""
        unit = cast(Unit, self._obj)
        if unit.state is not None:
            return unit.state.xy
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the unit."""
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
            state.colorsource = 1
            set_state = True
        if ATTR_RGB_COLOR in kwargs:
            state.rgb = kwargs[ATTR_RGB_COLOR]
            state.colorsource = 1
            set_state = True
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            state.temperature = kwargs[ATTR_COLOR_TEMP_KELVIN]
            state.colorsource = 0
            set_state = True
        if ATTR_XY_COLOR in kwargs:
            state.xy = kwargs[ATTR_XY_COLOR]
            state.colorsource = 2
            set_state = True

        if set_state:
            await self._api.casa.setUnitState(unit, state)
        else:
            await self._api.casa.turnOn(self._obj)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the unit."""
        # HACK: Try to get lights only supporting ONOFF to turn off.
        # SetLevel doesn't seem to work for unknown reasons.
        if self.color_mode == ColorMode.ONOFF:
            unit = cast(Unit, self._obj)
            await self._api.casa._send(
                unit, bytes(unit.unitType.stateLength), _operation.OpCode.SetState
            )
        else:
            await super().async_turn_off(**kwargs)


class CasambiLightGroup(CasambiLight, CasambiNetworkGroup):
    """Defines a Casambi group entity."""

    def __init__(self, api: CasambiApi, group: Group) -> None:
        """Initialize a Casambi group entity."""

        # Find union of supported color modes.
        supported_modes: set[str] = set()
        for unit in group.units:
            supported_modes = supported_modes.union(self._capabilities_helper(unit))

        # Color temperature for groups isn't supported yet.
        # Open problems:
        #  - How do we determine min and max temperature? Is it the union or intersection of the intervals?
        #    We can't really scale the temperature since we don't have a min or max.
        #  - How does the SetTemperature opcode work (for casambi-bt)?
        if ColorMode.COLOR_TEMP in supported_modes:
            supported_modes.remove(ColorMode.COLOR_TEMP)

        if len(supported_modes) == 0:
            supported_modes.add(ColorMode.UNKNOWN)
        self._attr_supported_color_modes = supported_modes

        desc = TypedEntityDescription(
            key=str(group.groudId), name=group.name, entity_type="light"
        )

        super().__init__(api, desc, group)

    @property
    def is_on(self) -> bool:
        """Return True if any unit in the group is on."""
        return any(u.is_on for u in self._unit_map.values())

    @property
    def brightness(self) -> int | None:
        """Return the brightness of the first fitting unit of the group."""
        for unit in self._unit_map.values():
            if (
                unit.unitType.get_control(UnitControlType.DIMMER)
                and unit.state is not None
            ):
                return unit.state.dimmer
        return None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the rgb color of the first fitting unit of the group."""
        for unit in self._unit_map.values():
            if (
                unit.unitType.get_control(UnitControlType.RGB)
                and unit.state is not None
            ):
                return unit.state.rgb
        return None

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """Return the rgw color of the first fitting unit of the group."""
        for unit in self._unit_map.values():
            if (
                unit.unitType.get_control(UnitControlType.RGB)
                and unit.unitType.get_control(UnitControlType.WHITE)
                and unit.state is not None
            ):
                return (*unit.state.rgb, unit.state.white)  # type: ignore[misc]
        return None

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the color temperature in Kelvin of the first fitting unit of the group."""
        for unit in self._unit_map.values():
            if (
                unit.unitType.get_control(UnitControlType.TEMPERATURE)
                and unit.state is not None
            ):
                return unit.state.temperature
        return None

    @property
    def xy_color(self) -> tuple[float, float] | None:
        """Return the XY color value of the first fitting unit of the group."""
        for unit in self._unit_map.values():
            if unit.unitType.get_control(UnitControlType.XY) and unit.state is not None:
                return unit.state.xy
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on all units in the group."""
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
            if self.brightness is not None:
                await self._api.casa.setLevel(self._obj, self.brightness)

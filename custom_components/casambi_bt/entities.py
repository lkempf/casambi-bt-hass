"""Common functionality for entities."""

from abc import ABCMeta
from dataclasses import dataclass
import logging
from typing import Final, cast

from CasambiBt import Group as CasambiGroup, Scene as CasambiScene, Unit as CasambiUnit

from homeassistant.core import callback
from homeassistant.helpers import device_registry
from homeassistant.helpers.entity import DeviceInfo, Entity, EntityDescription

from . import DOMAIN, CasambiApi

_LOGGER: Final = logging.getLogger(__name__)


@dataclass(kw_only=True)
class TypedEntityDescription(EntityDescription):
    """Describes a CasambiUnitEntity."""

    entity_type: str | None = None


class CasambiEntity(Entity, metaclass=ABCMeta):
    """Placeholder class for storing a Casambi object."""

    def __init__(
        self,
        api: CasambiApi,
        description: EntityDescription,
        obj: CasambiUnit | CasambiGroup | CasambiScene | None,
    ):
        """Initialize Casambi Entity."""
        self._api = api
        self._obj = obj

        self.entity_description = description
        self._attr_has_entity_name = True

        self._attr_should_poll = False

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._api.available

    @callback
    def _change_callback(self, _unit: CasambiUnit) -> None:
        self.schedule_update_ha_state(False)


class CasambiNetworkEntity(CasambiEntity, metaclass=ABCMeta):
    """Defines a Casambi Entity belonging to the network device."""

    def __init__(
        self,
        api: CasambiApi,
        description: EntityDescription | TypedEntityDescription,
        obj: CasambiGroup | CasambiScene | None = None,
    ):
        """Initialize Casambi Entity."""
        super().__init__(api, description, obj)

    @property
    def unique_id(self) -> str:
        """Return the unique ID for this entity."""
        name = f"{self._api.casa.networkId}"
        if self._obj is not None:
            if isinstance(self._obj, CasambiScene):
                name += "-scene"
            elif isinstance(self._obj, CasambiGroup):
                name += "-group"
        name += f"-{self.entity_description.key}"
        return name

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this Casambi entity."""
        return DeviceInfo(
            name=self._api.casa.networkName,
            manufacturer="Casambi",
            model="Network",
            identifiers={(DOMAIN, self._api.casa.networkId)},
            connections={(device_registry.CONNECTION_BLUETOOTH, self._api.address)},
        )


class CasambiNetworkGroup(CasambiNetworkEntity, metaclass=ABCMeta):
    """Base entity for Casambi groups."""

    def __init__(
        self,
        api: CasambiApi,
        description: TypedEntityDescription,
        obj: CasambiGroup,
    ):
        """Initialize Casambi Entity."""
        super().__init__(api, description, obj)

        self._unit_map = dict(
            zip([u.deviceId for u in obj.units], obj.units, strict=True)
        )

    @property
    def available(self) -> bool:
        """Return true if any of the units in the group is available."""
        return super().available and any(
            unit.online for unit in self._unit_map.values()
        )

    @callback
    def _change_callback(self, unit: CasambiUnit) -> None:
        group = cast(CasambiGroup, self._obj)
        _LOGGER.debug(
            "Handling state change for unit %i in group %i",
            unit.deviceId,
            group.groudId,
        )
        if unit.state:
            self._unit_map[unit.deviceId] = unit
        else:
            own_unit = self._unit_map[unit.deviceId]
            # This update doesn't have a state.
            # This can happen if the unit isn't online so only look at that part.
            own_unit._online = unit.online  # noqa: SLF001
        return super()._change_callback(unit)

    async def async_added_to_hass(self) -> None:
        """Run when the group is about to be added to hass."""
        group = cast(CasambiGroup, self._obj)
        for unit in group.units:
            self._api.register_unit_updates(unit, self._change_callback)

    async def async_will_remove_from_hass(self) -> None:
        """Run when the group will be removed from hass."""
        group = cast(CasambiGroup, self._obj)
        for unit in group.units:
            self._api.unregister_unit_updates(unit, self._change_callback)


class CasambiUnitEntity(CasambiEntity, metaclass=ABCMeta):
    """Base entity for Casambi units."""

    def __init__(
        self, api: CasambiApi, description: TypedEntityDescription, obj: CasambiUnit
    ) -> None:
        """Initialize Casambi Entity."""
        super().__init__(api, description, obj)

    @property
    def unique_id(self) -> str:
        """Return an unique identifier for the unit."""
        unit = cast(CasambiUnit, self._obj)
        desc = cast(TypedEntityDescription, self.entity_description)
        return f"{self._api.casa.networkId}-unit-{unit.uuid}-{desc.entity_type}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return specific device information."""
        unit = cast(CasambiUnit, self._obj)
        return DeviceInfo(
            name=unit.name,
            manufacturer=unit.unitType.manufacturer,
            model=unit.unitType.model,
            sw_version=unit.firmwareVersion,
            identifiers={(DOMAIN, unit.uuid)},
            via_device=(DOMAIN, self._api.casa.networkId),
        )

    @property
    def available(self) -> bool:
        """Return True if the unit is available."""
        unit = cast(CasambiUnit, self._obj)
        return super().available and unit.online

    @callback
    def _change_callback(self, unit: CasambiUnit) -> None:
        _LOGGER.debug("Handling state change for unit %i", unit.deviceId)
        if unit.state:
            self._obj = unit
        else:
            own_unit = cast(CasambiUnit, self._obj)
            # This update doesn't have a state.
            # This can happen if the unit isn't online so only look at that part.
            own_unit._online = unit.online  # noqa: SLF001
        return super()._change_callback(unit)

    async def async_added_to_hass(self) -> None:
        """Run when the unit is about to be added to hass."""
        unit = cast(CasambiUnit, self._obj)
        self._api.register_unit_updates(unit, self._change_callback)

    async def async_will_remove_from_hass(self) -> None:
        """Run when the unit will be removed from hass."""
        unit = cast(CasambiUnit, self._obj)
        self._api.unregister_unit_updates(unit, self._change_callback)

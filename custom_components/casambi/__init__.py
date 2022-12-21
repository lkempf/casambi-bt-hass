"""The Casambi Bluetooth integration."""
from __future__ import annotations

from collections.abc import Iterable
import logging
from typing import Callable, Final

from CasambiBt import Casambi, Group, Unit, UnitControlType
from CasambiBt.errors import AuthenticationError, BluetoothError, NetworkNotFoundError

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import DOMAIN

PLATFORMS = ["light"]
_LOGGER: Final = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Casambi Bluetooth from a config entry."""
    conf = entry.data
    casa_api = await async_casmbi_api_setup(
        hass, conf[CONF_ADDRESS], conf[CONF_PASSWORD]
    )

    if not casa_api:
        return False

    casa_api.casa.registerUnitChangedHandler(casa_api._unit_changed_handler)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = casa_api
    hass.config_entries.async_setup_platforms(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    casa_api: CasambiApi = hass.data[DOMAIN][entry.entry_id]

    casa_api.casa.unregisterUnitChangedHandler(casa_api._unit_changed_handler)
    await casa_api.disconnect()

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_casmbi_api_setup(
    hass: HomeAssistant, address: str, password: str
) -> CasambiApi | None:
    client = hass.helpers.httpx_client.get_async_client()
    try:
        casa = Casambi(client)
        device = async_ble_device_from_address(hass, address, connectable=True)
        await casa.connect(device, password)
    except BluetoothError as err:
        _LOGGER.warn("Failed to use bluetooth")
        raise ConfigEntryNotReady from err
    except AuthenticationError as err:
        _LOGGER.debug("Failed to authenticate to network %s", address)
        raise ConfigEntryAuthFailed from err
    except NetworkNotFoundError:
        _LOGGER.error("Network with address %s wasn't found", address)
        return None
    except Exception:  # pylint: disable=broad-except
        _LOGGER.error("Unexpected error creating network %s", address, exc_info=True)
        return None

    api = CasambiApi(casa)
    return api


class CasambiApi:
    _callback_map: dict[int, list[Callable[[Unit], None]]] = {}

    def __init__(self, casa: Casambi) -> None:
        self.casa = casa

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.casa.connected

    def get_units(
        self, control_types: list[UnitControlType] | None = None
    ) -> Iterable[Unit]:
        """Return all units in the network optionally filtered by control type."""

        if not control_types:
            return self.casa.units

        return filter(
            lambda u: any([uc.type in control_types for uc in u.unitType.controls]),  # type: ignore[arg-type]
            self.casa.units,
        )

    def get_groups(self) -> Iterable[Group]:
        """Return all groups in the network."""

        return self.casa.groups

    async def disconnect(self) -> None:
        await self.casa.disconnect()

    def register_unit_updates(
        self, unit: Unit, callback: Callable[[Unit], None]
    ) -> None:
        self._callback_map.setdefault(unit.deviceId, []).append(callback)

    def unregister_unit_updates(
        self, unit: Unit, callback: Callable[[Unit], None]
    ) -> None:
        self._callback_map[unit.deviceId].remove(callback)

    def _unit_changed_handler(self, unit: Unit) -> None:
        for c in self._callback_map[unit.deviceId]:
            c(unit)

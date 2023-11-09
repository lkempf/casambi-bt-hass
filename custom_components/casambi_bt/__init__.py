"""The Casambi Bluetooth integration."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Callable, Final

import homeassistant.components.bluetooth as bluetooth
from CasambiBt import Casambi, Group, Scene, Unit, UnitControlType
from CasambiBt.errors import AuthenticationError, BluetoothError, NetworkNotFoundError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)

from .const import DOMAIN, PLATFORMS

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
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

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
        device = bluetooth.async_ble_device_from_address(
            hass, address, connectable=True
        )
        if not device:
            raise NetworkNotFoundError
        await casa.connect(device, password)
    except BluetoothError as err:
        raise ConfigEntryNotReady("Failed to use bluetooth") from err
    except AuthenticationError as err:
        raise ConfigEntryAuthFailed(
            f"Failed to authenticate to network {address}"
        ) from err
    except NetworkNotFoundError as err:
        raise ConfigEntryNotReady(
            f"Network with address {address} wasn't found"
        ) from err
    except Exception as err:  # pylint: disable=broad-except
        raise ConfigEntryError(f"Unexpected error creating network {address}") from err

    api = CasambiApi(casa, hass, address, password)
    return api


class CasambiApi:
    _callback_map: dict[int, list[Callable[[Unit], None]]] = {}
    _cancel_bluetooth_callback: Callable[[], None] = None
    _reconnect_lock = asyncio.Lock()

    def __init__(
        self, casa: Casambi, hass: HomeAssistant, address: str, password: str
    ) -> None:
        self.casa = casa
        self.hass = hass
        self.address = address
        self.password = password

        self._register_bluetooth_callback()

    def _register_bluetooth_callback(self):
        self._cancel_bluetooth_callback = bluetooth.async_register_callback(
            self.hass,
            self._bluetooth_callback,
            {"address": self.address, "connectable": True},
            bluetooth.BluetoothScanningMode.ACTIVE,
        )

    @property
    def available(self) -> bool:
        """Return True if the controller is available."""
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

    def get_scenes(self) -> Iterable[Scene]:
        """Return all scenes in the network."""

        return self.casa.scenes

    async def disconnect(self) -> None:
        """Disconnects from the controller and disables automatic reconnect."""
        if self._cancel_bluetooth_callback:
            self._cancel_bluetooth_callback()
            self._cancel_bluetooth_callback = None
        await self.casa.disconnect()

    async def try_reconnect(self) -> None:
        if self._reconnect_lock.locked():
            return

        # Use locking to ensure that only one reconnect can happen at a time.
        # Not sure if this is necessary.
        await self._reconnect_lock.acquire()

        try:
            device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if not device:
                return
            try:
                await self.casa.disconnect()
            # HACK: This is a workaround for https://github.com/lkempf/casambi-bt-hass/issues/26
            # We don't actually need to disconnect except to clean up so this should be ok to ignore.
            except AttributeError:
                _LOGGER.debug("Unexpected failure during disconnect.")
            await self.casa.connect(device, self.password)

            if not self._cancel_bluetooth_callback:
                self._register_bluetooth_callback()
        finally:
            self._reconnect_lock.release()

    def register_unit_updates(
        self, unit: Unit, callback: Callable[[Unit], None]
    ) -> None:
        self._callback_map.setdefault(unit.deviceId, []).append(callback)

    def unregister_unit_updates(
        self, unit: Unit, callback: Callable[[Unit], None]
    ) -> None:
        self._callback_map[unit.deviceId].remove(callback)

    @callback
    def _unit_changed_handler(self, unit: Unit) -> None:
        if unit.deviceId not in self._callback_map:
            return
        for c in self._callback_map[unit.deviceId]:
            c(unit)

    @callback
    def _bluetooth_callback(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        if not self.casa.connected and service_info.connectable:
            self.hass.async_create_task(self.try_reconnect())

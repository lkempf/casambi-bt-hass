"""The Casambi Bluetooth integration - Fixed for HA 2026.6 with hybrid gateway support."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
import logging
from pathlib import Path
from typing import Final

from CasambiBt import Casambi, Group, Scene, Unit, UnitControlType
from CasambiBt.errors import AuthenticationError, BluetoothError, NetworkNotFoundError

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers.httpx_client import get_async_client

from .const import DOMAIN, PLATFORMS

_LOGGER: Final = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Casambi Bluetooth from a config entry."""
    api = CasambiApi(hass, entry, entry.data[CONF_ADDRESS], entry.data[CONF_PASSWORD])
    await api.connect()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = api

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    casa_api: CasambiApi = hass.data[DOMAIN][entry.entry_id]
    await casa_api.disconnect()

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


def get_cache_dir(hass: HomeAssistant) -> Path:
    """Return the cache dir that should be used by CasambiBt."""
    conf_path = Path(hass.config.config_dir)
    return conf_path / ".storage" / DOMAIN


class CasambiApi:
    """Defines a Casambi API - Fixed for HA 2026.6."""

    def __init__(
        self,
        hass: HomeAssistant,
        conf_entry: ConfigEntry,
        address: str,
        password: str,
    ) -> None:
        """Initialize a Casambi API."""

        self.hass = hass
        self.conf_entry = conf_entry
        self.address = address
        self.password = password
        self.casa: Casambi = Casambi(get_async_client(hass), get_cache_dir(hass))

        self._callback_map: dict[int, list[Callable[[Unit], None]]] = {}
        self._cancel_bluetooth_callback: Callable[[], None] | None = None
        self._reconnect_lock = asyncio.Lock()
        
        # HA 2026.6 Fix: Replace _first_disconnect with proper attempt tracking
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._reconnect_delay = 5  # Start with 5 seconds
        self._health_check_task: asyncio.Task | None = None
        self._connection_lost_time: float | None = None

    def _register_bluetooth_callback(self) -> None:
        self._cancel_bluetooth_callback = bluetooth.async_register_callback(
            self.hass,
            self._bluetooth_callback,
            {"address": self.address, "connectable": True},
            bluetooth.BluetoothScanningMode.ACTIVE,
        )

    async def connect(self) -> None:
        """Connect to the Casmabi network."""
        try:
            device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if not device:
                raise NetworkNotFoundError  # noqa: TRY301

            self.casa.registerDisconnectCallback(self._casa_disconnect)
            self.casa.registerUnitChangedHandler(self._unit_changed_handler)

            await self.casa.connect(device, self.password)
            
            # HA 2026.6 Fix: Reset counters on successful connection
            self._reconnect_attempts = 0
            self._reconnect_delay = 5
            self._connection_lost_time = None
            
            _LOGGER.info(
                "Successfully connected to Casambi network at %s", self.address
            )
            
            # Start health check to detect silent disconnects (HA 2026.6 workaround)
            if self._health_check_task:
                self._health_check_task.cancel()
            self._health_check_task = asyncio.create_task(self._health_check_loop())
            
        except BluetoothError as err:
            raise ConfigEntryNotReady("Failed to use bluetooth") from err
        except NetworkNotFoundError as err:
            raise ConfigEntryNotReady(
                f"Network with address {self.address} wasn't found"
            ) from err
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(
                f"Failed to authenticate to network {self.address}"
            ) from err
        except Exception as err:  # pylint: disable=broad-except
            raise ConfigEntryError(
                f"Unexpected error creating network {self.address}"
            ) from err

        # Only register bluetooth callback after connection.
        # Otherwise we get an immediate callback and attempt two connections at once.
        if not self._cancel_bluetooth_callback:
            self._register_bluetooth_callback()

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
            lambda u: any(uc.type in control_types for uc in u.unitType.controls),  # type: ignore[arg-type]
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
        async with self._reconnect_lock:
            if self._cancel_bluetooth_callback is not None:
                self._cancel_bluetooth_callback()
                self._cancel_bluetooth_callback = None

            # Cancel health check
            if self._health_check_task:
                self._health_check_task.cancel()
                self._health_check_task = None

            # This needs to happen before we disconnect.
            # We don't want to be informed about disconnects initiated by us.
            self.casa.unregisterDisconnectCallback(self._casa_disconnect)

            try:
                await self.casa.disconnect()
            except Exception:
                _LOGGER.exception("Error during disconnect.")
            self.casa.unregisterUnitChangedHandler(self._unit_changed_handler)

    @callback
    def _casa_disconnect(self) -> None:
        """HA 2026.6 Fix: Proper disconnect handling without _first_disconnect flag."""
        import time
        self._connection_lost_time = time.time()
        
        _LOGGER.warning(
            "Casambi network disconnected. Scheduling reconnect "
            "(attempt %d/%d)",
            self._reconnect_attempts + 1,
            self._max_reconnect_attempts
        )
        self.conf_entry.async_create_background_task(
            self.hass, self._delayed_reconnect(), "Delayed reconnect"
        )

    async def _delayed_reconnect(self) -> None:
        """Attempt to reconnect after exponential backoff delay."""
        await asyncio.sleep(self._reconnect_delay)

        async with self._reconnect_lock:
            if self.casa.connected:
                _LOGGER.debug("Already reconnected, skipping delayed reconnect")
                return

            if self._reconnect_attempts >= self._max_reconnect_attempts:
                _LOGGER.error(
                    "Maximum reconnection attempts (%d) reached. "
                    "Integration will remain unavailable. "
                    "Restart Home Assistant to retry.",
                    self._max_reconnect_attempts
                )
                return

        _LOGGER.debug(
            "Starting delayed reconnect (attempt %d/%d, delay %.1fs)",
            self._reconnect_attempts + 1,
            self._max_reconnect_attempts,
            self._reconnect_delay
        )
        
        device = bluetooth.async_ble_device_from_address(self.hass, self.address)
        if device is not None:
            try:
                await self.try_reconnect()
            except Exception as err:
                _LOGGER.exception(
                    "Error during reconnect attempt %d: %s",
                    self._reconnect_attempts + 1,
                    err
                )
                # Exponential backoff: 5s → 10s → 20s → 40s → 60s (capped)
                self._reconnect_delay = min(self._reconnect_delay * 2, 60)
                self._reconnect_attempts += 1
                
                # Schedule next attempt if we haven't exceeded max attempts
                if self._reconnect_attempts < self._max_reconnect_attempts:
                    self.conf_entry.async_create_background_task(
                        self.hass, self._delayed_reconnect(), "Delayed reconnect"
                    )
        else:
            _LOGGER.debug(
                "Device not found in BLE scan. "
                "Will retry when device is discovered."
            )

    async def try_reconnect(self) -> None:
        """Attemtps to reconnect to the Casambi network. Disconnects first to ensure a consitent state."""
        if self._reconnect_lock.locked():
            _LOGGER.debug("Reconnect already in progress")
            return

        # Use locking to ensure that only one reconnect can happen at a time.
        await self._reconnect_lock.acquire()

        try:
            try:
                await self.casa.disconnect()
            # HACK: This is a workaround for https://github.com/lkempf/casambi-bt-hass/issues/26
            # We don't actually need to disconnect except to clean up so this should be ok to ignore.
            except AttributeError:
                _LOGGER.debug("Unexpected failure during disconnect.")
            except Exception:
                _LOGGER.debug("Error during disconnect (may be expected)", exc_info=True)
            
            await self.connect()
        finally:
            self._reconnect_lock.release()

    async def _health_check_loop(self) -> None:
        """HA 2026.6 Fix: Periodic health check to detect silent BLE disconnects.
        
        In HA 2026.6+, the BLE stack may not always call disconnect callbacks
        immediately. This loop detects those silent disconnects and forces a reconnection.
        """
        try:
            while True:
                await asyncio.sleep(60)  # Check every 60 seconds
                
                if not self.casa.connected:
                    _LOGGER.warning(
                        "Health check detected BLE disconnection (no callback received)"
                    )
                    self._casa_disconnect()
                    return
                
                _LOGGER.debug("Health check: BLE connection OK")
        except asyncio.CancelledError:
            _LOGGER.debug("Health check task cancelled")
        except Exception:
            _LOGGER.exception("Unexpected error in health check loop")

    def register_unit_updates(self, unit: Unit, c: Callable[[Unit], None]) -> None:
        """Register a callback for unit updates.

        :param unit: The unit for which changes should be reported.
        :param c: The callback.
        """
        self._callback_map.setdefault(unit.deviceId, []).append(c)

    def unregister_unit_updates(self, unit: Unit, c: Callable[[Unit], None]) -> None:
        """Unregister a callback for unit updates.

        :param unit: The unit for which changes should no longer be reported.
        :param c: The callback.
        """
        self._callback_map[unit.deviceId].remove(c)

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
        _change: bluetooth.BluetoothChange,
    ) -> None:
        if not self.casa.connected and service_info.connectable:
            _LOGGER.debug("BLE device discovered, attempting reconnect")
            self.conf_entry.async_create_background_task(
                self.hass, self.try_reconnect(), "Reconnect"
            )

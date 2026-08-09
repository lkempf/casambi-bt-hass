"""The Casambi Bluetooth integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
import inspect
import logging
from pathlib import Path
from typing import Any, Final, cast

from CasambiBt import Casambi, Group, Scene, Unit, UnitControlType
from CasambiBt.errors import (
    AuthenticationError,
    BluetoothDeviceNotFoundError,
    BluetoothError,
    ProtocolError,
)

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.httpx_client import get_async_client

from .const import (
    DOMAIN,
    PLATFORMS,
    RECONNECT_BACKOFF_MAX,
    RECONNECT_BACKOFF_START,
    RECONNECT_BACKOFF_STEP,
)

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
    """Defines a Casambi API."""

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
        self._casa = Casambi(get_async_client(hass), get_cache_dir(hass))
        self.casa = cast(Casambi, CasambiProxy(self, self._casa))

        self._callback_map: dict[int, list[Callable[[Unit], None]]] = {}
        self._cancel_bluetooth_callback: Callable[[], None] | None = None
        self._reconnect_task: asyncio.Task | None = None

    def _register_bluetooth_callback(self) -> None:
        self._cancel_bluetooth_callback = bluetooth.async_register_callback(
            self.hass,
            self._bluetooth_callback,
            {"address": self.address, "connectable": True},
            bluetooth.BluetoothScanningMode.ACTIVE,
        )

    async def connect(self) -> None:
        """Connect initially to the Casmabi network."""
        try:
            device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if not device:
                raise BluetoothDeviceNotFoundError  # noqa: TRY301

            self._casa.registerDisconnectCallback(self._casa_disconnect)
            self._casa.registerUnitChangedHandler(self._unit_changed_handler)

            await self._casa.connect(device, self.password)
        except BluetoothError as err:
            raise ConfigEntryNotReady("Failed to use bluetooth") from err
        except BluetoothDeviceNotFoundError as err:
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

    async def reconnect(self) -> None:
        """Start reconnection attempt to the Casmabi network."""
        backoff = RECONNECT_BACKOFF_START
        protocol_error = False
        while True:
            try:
                device = bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )
                if not device:
                    raise BluetoothDeviceNotFoundError  # noqa: TRY301

                await self._casa.reconnect(device)
                protocol_error = False
                break
            except BluetoothError:
                _LOGGER.debug(
                    "Connecting failed due to bluetooth error. Retrying...",
                    exc_info=True,
                )
            except BluetoothDeviceNotFoundError:
                # Sometimes HA notifications seem not to work.
                # We are still registered for HA notifications but retrying very seldomly should be fine.
                backoff = max(backoff, 60)
            except AuthenticationError as err:
                raise HomeAssistantError from err
            except asyncio.CancelledError:
                _LOGGER.debug("Reconnect task cancelled.")
                break
            except ProtocolError as err:
                # We retry once on Protocol errors to make sure they aren't permanent.
                if not protocol_error:
                    _LOGGER.debug("Retrying once on protocol error.", exc_info=True)
                    protocol_error = True
                else:
                    raise HomeAssistantError from err
            except Exception as err:  # pylint: disable=broad-except
                raise HomeAssistantError from err

            await asyncio.sleep(backoff)
            backoff = min(RECONNECT_BACKOFF_MAX, backoff * RECONNECT_BACKOFF_STEP)

    @property
    def available(self) -> bool:
        """Return True if the controller is available."""
        return self._casa.connected

    def get_units(
        self, control_types: list[UnitControlType] | None = None
    ) -> Iterable[Unit]:
        """Return all units in the network optionally filtered by control type."""

        if not control_types:
            return self._casa.units

        return filter(
            lambda u: any(uc.type in control_types for uc in u.unitType.controls),  # type: ignore[arg-type]
            self._casa.units,
        )

    def get_groups(self) -> Iterable[Group]:
        """Return all groups in the network."""

        return self._casa.groups

    def get_scenes(self) -> Iterable[Scene]:
        """Return all scenes in the network."""

        return self._casa.scenes

    async def disconnect(self) -> None:
        """Disconnects from the controller and disables automatic reconnect."""
        if (
            self._reconnect_task is not None
            and not self._reconnect_task.cancelled()
            and not self._reconnect_task.done()
        ):
            self._reconnect_task.cancel()
            try:
                await asyncio.wait_for(self._reconnect_task, 5)
            except asyncio.CancelledError:
                pass
            except TimeoutError:
                _LOGGER.debug(
                    "Timeout while cancelling reconnect as part of disconnect."
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Got exception when cancelling reconnect. Ignoring.", exc_info=True
                )

        if self._cancel_bluetooth_callback is not None:
            self._cancel_bluetooth_callback()
            self._cancel_bluetooth_callback = None

        # This needs to happen before we disconnect.
        # We don't want to be informed about disconnects initiated by us.
        self._casa.unregisterDisconnectCallback(self._casa_disconnect)

        try:
            await self._casa.disconnect()
        except Exception:
            _LOGGER.exception("Error during disconnect.")
        self._casa.unregisterUnitChangedHandler(self._unit_changed_handler)

    @callback
    def _casa_disconnect(self) -> None:
        self._schedule_reconnect()

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
        if not self._casa.connected and service_info.connectable:
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        # We assume that a reconnect task is only cancelled when disconnected.
        # So never reconnect when the disconnect task has been cancelled.
        if self._reconnect_task is not None and self._reconnect_task.cancelled():
            _LOGGER.debug(
                "Attempted to schedule reconnect after task was canceled. Disconnect already called?"
            )
            return
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = self.conf_entry.async_create_background_task(
                self.hass, self.reconnect(), "Reconnect"
            )


class CasambiProxy:
    """Proxy the write operations so that bluetooth errors automatically trigger a reconnect."""

    def __init__(self, api: CasambiApi, casa: Casambi) -> None:
        """Initialize a CasambiProxy."""
        self._api = api
        self._casa = casa

    def __getattr__(self, name: str) -> Any:
        """Wrap all async calls to drop BluetoothError and trigger a reconnect instead."""
        attr = getattr(self._casa, name)

        if inspect.iscoroutinefunction(attr):

            async def async_wrapper(*args, **kwargs):
                try:
                    return await attr(*args, **kwargs)
                except BluetoothError:
                    _LOGGER.debug("Bluetooth error during write.", exc_info=True)
                    _LOGGER.info("Triggering reconnect after write failed.")
                    self._api._schedule_reconnect()  # noqa: SLF001

            return async_wrapper

        return attr

"""The Casambi Bluetooth integration."""

import asyncio
from collections.abc import Callable, Iterable
import inspect
import logging
from pathlib import Path
import random
import shutil
from typing import Any, Final, cast

from CasambiBt import Casambi, Group, Scene, Unit, UnitControlType
from CasambiBt.errors import (
    AuthenticationError,
    BluetoothDeviceNotFoundError,
    BluetoothError,
    CasambiBtError,
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
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.httpx_client import get_async_client

from .const import (
    CONNECT_TIMEOUT,
    DOMAIN,
    PLATFORMS,
    RECONNECT_BACKOFF_MAX,
    RECONNECT_BACKOFF_START,
    RECONNECT_BACKOFF_STEP,
)
from .migration import migrate_legacy_cache

_LOGGER: Final = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Casambi Bluetooth from a config entry."""
    uuid = entry.data[CONF_ADDRESS].replace(":", "").lower()
    await hass.async_add_executor_job(migrate_legacy_cache, get_cache_dir(hass), uuid)

    api = CasambiApi(hass, entry, entry.data[CONF_ADDRESS], entry.data[CONF_PASSWORD])
    await api.connect()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = api

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        # Clean up the connection and callbacks if platforms fail to set up.
        # Otherwise the bluetooth callback and a live connection would leak.
        hass.data[DOMAIN].pop(entry.entry_id, None)
        await api.disconnect()
        raise

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    casa_api: CasambiApi = hass.data[DOMAIN][entry.entry_id]
    await casa_api.disconnect()

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up persistent data when the config entry is deleted."""
    uuid = entry.data[CONF_ADDRESS].replace(":", "").lower()
    cache_dir = get_cache_dir(hass) / uuid

    def _remove_cache() -> None:
        shutil.rmtree(cache_dir, ignore_errors=True)

    await hass.async_add_executor_job(_remove_cache)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: DeviceEntry,
) -> bool:
    """Allow removing a device that is no longer part of this Casambi network."""
    api: CasambiApi = hass.data[DOMAIN][config_entry.entry_id]
    live = {(DOMAIN, api.casa.networkId)}
    live |= {(DOMAIN, unit.uuid) for unit in api.casa.units}
    return not (device_entry.identifiers & live)


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
        self._connection_callbacks: list[Callable[[], None]] = []
        # The library runs its disconnect callbacks while it still reports
        # itself as connected, so the link state is tracked here instead.
        self._link_up = False
        self._cancel_bluetooth_callback: Callable[[], None] | None = None
        self._reconnect_task: asyncio.Task | None = None

    def _register_bluetooth_callback(self) -> None:
        self._cancel_bluetooth_callback = bluetooth.async_register_callback(
            self.hass,
            self._bluetooth_callback,
            {"address": self.address, "connectable": True},
            bluetooth.BluetoothScanningMode.PASSIVE,
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

            try:
                await asyncio.wait_for(
                    self._casa.connect(device, self.password), CONNECT_TIMEOUT
                )
            except TimeoutError as err:
                raise BluetoothError(
                    f"Connection attempt to {self.address} timed out"
                ) from err
            self._link_up = True
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

        # Safety margin: the library may still report itself connected while
        # its disconnect callback is running. Yield once so the teardown
        # finishes before we check the connection state. The reconnect task
        # is also created with eager_start=False to avoid this race.
        await asyncio.sleep(0)

        while True:
            try:
                device = bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )
                if not device:
                    raise BluetoothDeviceNotFoundError  # noqa: TRY301

                try:
                    await asyncio.wait_for(
                        self._casa.reconnect(device), CONNECT_TIMEOUT
                    )
                except TimeoutError as err:
                    raise BluetoothError("Reconnect attempt timed out.") from err
                if not self._casa.connected:
                    raise BluetoothError(  # noqa: TRY301
                        "Reconnect returned without a connection."
                    )
                self._link_up = True
                self._notify_connection_state()
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

            # Add jitter so multiple config entries don't retry in lockstep.
            await asyncio.sleep(backoff + random.uniform(0, 1))
            backoff = min(RECONNECT_BACKOFF_MAX, backoff * RECONNECT_BACKOFF_STEP)

    @property
    def available(self) -> bool:
        """Return True if the controller is available."""
        return self._link_up and self._casa.connected

    def diagnostics(self) -> dict[str, Any]:
        """Return diagnostic information about the connection."""
        task = self._reconnect_task
        task_info: dict[str, Any] | None = None
        if task is not None:
            task_info = {"done": task.done(), "cancelled": task.cancelled()}

        data: dict[str, Any] = {
            "available": self.available,
            "link_up": self._link_up,
            "library_connected": self._casa.connected,
            "reconnect_task": task_info,
            "connect_timeout": CONNECT_TIMEOUT,
            "backoff": {
                "start": RECONNECT_BACKOFF_START,
                "step": RECONNECT_BACKOFF_STEP,
                "max": RECONNECT_BACKOFF_MAX,
            },
        }

        try:
            data["units"] = [
                {
                    "id": unit.deviceId,
                    "name": unit.name,
                    "online": unit.online,
                    "on": unit.is_on,
                }
                for unit in self._casa.units
            ]
            data["group_count"] = len(self._casa.groups)
            data["scene_count"] = len(self._casa.scenes)
        except CasambiBtError:
            data["units"] = None
            _LOGGER.debug("Network information not available for diagnostics.")

        return data

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
        self._link_up = False
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
            except Exception:
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
        self._link_up = False
        self._notify_connection_state()
        self._schedule_reconnect()

    def register_connection_updates(self, c: Callable[[], None]) -> None:
        """Register a callback for changes of the connection state.

        :param c: The callback.
        """
        self._connection_callbacks.append(c)

    def unregister_connection_updates(self, c: Callable[[], None]) -> None:
        """Unregister a callback for changes of the connection state.

        :param c: The callback.
        """
        self._connection_callbacks.remove(c)

    @callback
    def _notify_connection_state(self) -> None:
        for c in self._connection_callbacks:
            c()

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
        if not self.available and service_info.connectable:
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
                self.hass,
                self.reconnect(),
                "Reconnect",
                # Do not start the task synchronously inside the library's
                # disconnect callback. The library still reports itself as
                # connected while running that callback, so an eager start
                # would make Casambi.reconnect() return without doing
                # anything. See https://github.com/lkempf/casambi-bt-hass/issues/170.
                eager_start=False,
            )


class CasambiProxy:
    """Proxy the write operations so that bluetooth errors automatically trigger a reconnect."""

    def __init__(self, api: CasambiApi, casa: Casambi) -> None:
        """Initialize a CasambiProxy."""
        self._api = api
        self._casa = casa

    def __getattr__(self, name: str) -> Any:
        """Wrap all async calls to trigger a reconnect on bluetooth errors."""
        attr = getattr(self._casa, name)

        if inspect.iscoroutinefunction(attr):

            async def async_wrapper(*args, **kwargs):
                try:
                    return await attr(*args, **kwargs)
                except BluetoothError as err:
                    _LOGGER.debug("Bluetooth error during write.", exc_info=True)
                    _LOGGER.info("Triggering reconnect after write failed.")
                    self._api._schedule_reconnect()  # noqa: SLF001
                    # Don't swallow the error: the caller (service call)
                    # must learn that the command wasn't delivered.
                    raise HomeAssistantError(
                        "Bluetooth communication failed. A reconnect was triggered."
                    ) from err

            return async_wrapper

        return attr

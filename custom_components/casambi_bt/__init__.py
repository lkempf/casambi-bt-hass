"""The Casambi Bluetooth integration."""

import asyncio
from collections.abc import Callable, Iterable
import contextlib
import inspect
import logging
from pathlib import Path
import random
import re
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
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.httpx_client import get_async_client

from .const import (
    CONF_IMPORT_GROUPS,
    CONNECT_TIMEOUT,
    DOMAIN,
    PLATFORMS,
    RECONNECT_BACKOFF_MAX,
    RECONNECT_BACKOFF_START,
    RECONNECT_BACKOFF_STEP,
)
from .migration import migrate_legacy_cache

_LOGGER: Final = logging.getLogger(__name__)

# casambi-bt reports the http status of a failed cloud login only in the
# message of the AuthenticationError ("Login failed: <status>\n<body>").
_LOGIN_STATUS_REGEX: Final = re.compile(r"Login failed: (\d{3})")
# The Casambi cloud answers 403 when it refuses new sessions for a while.
_RATE_LIMIT_STATUSES: Final = frozenset({403, 429})

type CasambiConfigEntry = ConfigEntry[CasambiApi]


async def async_setup_entry(hass: HomeAssistant, entry: CasambiConfigEntry) -> bool:
    """Set up Casambi Bluetooth from a config entry."""
    uuid = entry.data[CONF_ADDRESS].replace(":", "").lower()
    await hass.async_add_executor_job(migrate_legacy_cache, get_cache_dir(hass), uuid)

    api = CasambiApi(hass, entry, entry.data[CONF_ADDRESS], entry.data[CONF_PASSWORD])
    await api.connect()
    entry.runtime_data = api

    # Register the network (hub) device up front. Unit devices reference it
    # through via_device_id, which must resolve no matter in which order the
    # platforms are set up.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, api.casa.networkId)},
        connections={(dr.CONNECTION_BLUETOOTH, api.address)},
        manufacturer="Casambi",
        model="Network",
        name=api.casa.networkName,
    )

    _async_remove_stale_devices(hass, entry, api)

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        # Clean up the connection and callbacks if platforms fail to set up.
        # Otherwise the bluetooth callback and a live connection would leak.
        await api.disconnect()
        raise

    return True


def _live_identifiers(api: CasambiApi) -> set[tuple[str, str]]:
    """Return the device identifiers of the network and its units."""
    live = {(DOMAIN, api.casa.networkId)}
    live |= {(DOMAIN, unit.uuid) for unit in api.casa.units}
    return live


@callback
def _async_remove_stale_devices(
    hass: HomeAssistant, entry: CasambiConfigEntry, api: CasambiApi
) -> None:
    """Remove devices of units that were deleted from the network.

    The unit list comes from the network configuration loaded on connect,
    so a missing unit was removed in the Casambi app. Skip the cleanup if
    the list is empty, which rather points at a problem than at a network
    without units.
    """
    if not api.casa.units:
        return

    live = _live_identifiers(api)
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if not device.identifiers & live:
            _LOGGER.debug("Removing stale device %s", device.name)
            device_registry.async_remove_device(device.id)


async def async_migrate_entry(hass: HomeAssistant, entry: CasambiConfigEntry) -> bool:
    """Migrate old config entries.

    Home Assistant itself refuses entries of a newer version than the
    config flow, so only older versions reach this function.
    """
    if entry.version == 1:
        # Version 2 moved the group import setting to the options because
        # it doesn't affect the connection.
        data = dict(entry.data)
        import_groups = data.pop(CONF_IMPORT_GROUPS, True)
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options={**entry.options, CONF_IMPORT_GROUPS: import_groups},
            version=2,
        )
        _LOGGER.debug("Migrated config entry %s to version 2", entry.entry_id)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: CasambiConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Keep the connection when a platform refused to unload. The entry stays
    # loaded in that case and its remaining entities still need it.
    if unload_ok:
        await entry.runtime_data.disconnect()

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: CasambiConfigEntry) -> None:
    """Clean up persistent data when the config entry is deleted."""
    uuid = entry.data[CONF_ADDRESS].replace(":", "").lower()
    cache_dir = get_cache_dir(hass) / uuid

    def _remove_cache() -> None:
        shutil.rmtree(cache_dir, ignore_errors=True)

    await hass.async_add_executor_job(_remove_cache)
    ir.async_delete_issue(hass, DOMAIN, f"no_connectable_scanner_{entry.entry_id}")


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: CasambiConfigEntry,
    device_entry: DeviceEntry,
) -> bool:
    """Allow removing a device that is no longer part of this Casambi network."""
    return not device_entry.identifiers & _live_identifiers(config_entry.runtime_data)


def login_status(err: AuthenticationError) -> int | None:
    """Return the http status of a failed cloud login, if known."""
    if match := _LOGIN_STATUS_REGEX.match(str(err)):
        return int(match.group(1))
    return None


def is_rate_limited(err: AuthenticationError) -> bool:
    """Return True if the cloud refused the login temporarily."""
    return login_status(err) in _RATE_LIMIT_STATUSES


def get_cache_dir(hass: HomeAssistant) -> Path:
    """Return the cache dir that should be used by CasambiBt."""
    conf_path = Path(hass.config.config_dir)
    return conf_path / ".storage" / DOMAIN


class CasambiApi:
    """Defines a Casambi API."""

    def __init__(
        self,
        hass: HomeAssistant,
        conf_entry: CasambiConfigEntry,
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
        self._reconnect_task: asyncio.Task[None] | None = None
        # Set when the network rejected the credentials. No reconnect is
        # attempted until the entry is reloaded (e.g. by a successful
        # reauth), otherwise every advertisement would trigger a login.
        self._auth_failed = False

    def _register_bluetooth_callback(self) -> None:
        self._cancel_bluetooth_callback = bluetooth.async_register_callback(
            self.hass,
            self._bluetooth_callback,
            {"address": self.address, "connectable": True},
            bluetooth.BluetoothScanningMode.PASSIVE,
        )

    @property
    def _scanner_issue_id(self) -> str:
        return f"no_connectable_scanner_{self.conf_entry.entry_id}"

    async def connect(self) -> None:
        """Connect initially to the Casmabi network."""
        if bluetooth.async_scanner_count(self.hass, connectable=True) < 1:
            # The user has to add an adapter or an active proxy; retrying
            # alone can't fix this, so tell them in the repairs dashboard.
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._scanner_issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="no_connectable_scanner",
                translation_placeholders={"address": self.address},
            )
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="network_not_found",
                translation_placeholders={"address": self.address},
            )
        ir.async_delete_issue(self.hass, DOMAIN, self._scanner_issue_id)

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
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="bluetooth_error",
                translation_placeholders={"address": self.address},
            ) from err
        except BluetoothDeviceNotFoundError as err:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="network_not_found",
                translation_placeholders={"address": self.address},
            ) from err
        except AuthenticationError as err:
            if is_rate_limited(err):
                # A rate limit says nothing about the password, so retry
                # later instead of asking the user to re-authenticate.
                raise ConfigEntryNotReady(
                    translation_domain=DOMAIN,
                    translation_key="rate_limited",
                    translation_placeholders={"address": self.address},
                ) from err
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"address": self.address},
            ) from err
        except Exception as err:  # pylint: disable=broad-except
            # Most failures here are transient (cloud hiccups, a network
            # update in progress, flaky bluetooth proxies). Retrying keeps
            # the entry alive instead of failing it until a manual reload.
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="unexpected_error",
                translation_placeholders={"address": self.address},
            ) from err

        # Only register bluetooth callback after connection.
        # Otherwise we get an immediate callback and attempt two connections at once.
        if not self._cancel_bluetooth_callback:
            self._register_bluetooth_callback()

    async def reconnect(self) -> None:
        """Start reconnection attempt to the Casmabi network."""
        backoff = RECONNECT_BACKOFF_START
        protocol_error = False
        unexpected_error_logged = False

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
                _LOGGER.info("Reconnected to Casambi network %s", self.address)
                self._notify_connection_state()
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
                if not is_rate_limited(err):
                    # Retrying cannot fix wrong credentials. Hand over to the
                    # reauth flow which reloads the entry once it succeeds.
                    _LOGGER.warning(
                        "Authentication to Casambi network %s failed while "
                        "reconnecting",
                        self.address,
                    )
                    self._auth_failed = True
                    self.conf_entry.async_start_reauth(self.hass)
                    break
                _LOGGER.debug("Login rate limited, retrying later.")
                backoff = RECONNECT_BACKOFF_MAX
            except Exception as err:  # pylint: disable=broad-except
                # Never let the reconnect loop die: a dead loop leaves the
                # integration unavailable until the user reloads it. Retry
                # a protocol error once quickly, then fall back to the
                # maximum backoff for any persistent error.
                if isinstance(err, ProtocolError) and not protocol_error:
                    _LOGGER.debug("Retrying once on protocol error.", exc_info=True)
                    protocol_error = True
                else:
                    if not unexpected_error_logged:
                        _LOGGER.warning(
                            "Unexpected error while reconnecting to %s, retrying "
                            "every %s seconds",
                            self.address,
                            RECONNECT_BACKOFF_MAX,
                            exc_info=True,
                        )
                        unexpected_error_logged = True
                    else:
                        _LOGGER.debug(
                            "Unexpected error while reconnecting.", exc_info=True
                        )
                    backoff = RECONNECT_BACKOFF_MAX

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
            lambda u: any(uc.type in control_types for uc in u.unitType.controls),
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
        if self._link_up:
            _LOGGER.info("Lost connection to Casambi network %s", self.address)
        self._link_up = False
        self._notify_connection_state()
        self._schedule_reconnect()

    def register_connection_updates(self, c: Callable[[], None]) -> CALLBACK_TYPE:
        """Register a callback for changes of the connection state.

        :param c: The callback.
        :return: A function that unregisters the callback.
        """
        self._connection_callbacks.append(c)

        @callback
        def _unregister() -> None:
            with contextlib.suppress(ValueError):
                self._connection_callbacks.remove(c)

        return _unregister

    @callback
    def _notify_connection_state(self) -> None:
        for c in list(self._connection_callbacks):
            c()

    def register_unit_updates(
        self, unit: Unit, c: Callable[[Unit], None]
    ) -> CALLBACK_TYPE:
        """Register a callback for unit updates.

        :param unit: The unit for which changes should be reported.
        :param c: The callback.
        :return: A function that unregisters the callback.
        """
        callbacks = self._callback_map.setdefault(unit.deviceId, [])
        callbacks.append(c)

        @callback
        def _unregister() -> None:
            with contextlib.suppress(ValueError):
                callbacks.remove(c)

        return _unregister

    @callback
    def _unit_changed_handler(self, unit: Unit) -> None:
        for c in list(self._callback_map.get(unit.deviceId, ())):
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
        if self._auth_failed:
            return
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

            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await attr(*args, **kwargs)
                except BluetoothError as err:
                    _LOGGER.debug("Bluetooth error during write.", exc_info=True)
                    _LOGGER.info("Triggering reconnect after write failed.")
                    self._api._schedule_reconnect()  # noqa: SLF001
                    # Don't swallow the error: the caller (service call)
                    # must learn that the command wasn't delivered.
                    raise HomeAssistantError(
                        translation_domain=DOMAIN, translation_key="write_failed"
                    ) from err
                except CasambiBtError as err:
                    # E.g. the connection is down and a reconnect is
                    # already running. Report the failed action.
                    _LOGGER.debug("Write failed.", exc_info=True)
                    raise HomeAssistantError(
                        translation_domain=DOMAIN, translation_key="write_failed"
                    ) from err

            return async_wrapper

        return attr

"""The Casambi Bluetooth integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
import logging
from pathlib import Path
import time
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

    # Event deduplication cache: (unit_id, button, payload_hex) -> timestamp
    event_cache: dict[tuple[int, int, str], float] = {}
    DEDUP_WINDOW_SECONDS = 10.0

    _LOGGER.info("Switch event deduplication enabled with %ss window", DEDUP_WINDOW_SECONDS)

    # Register switch event handler that fires Home Assistant events
    def handle_switch_event(event_data: dict) -> None:
        """Fire a Home Assistant event when a switch is pressed/released."""
        # Convert any bytes objects to hex strings for JSON serialization
        raw_packet = event_data.get("raw_packet")
        decrypted_data = event_data.get("decrypted_data")
        payload_hex = event_data.get("payload_hex")
        extra_data = event_data.get("extra_data")

        # Convert payload_hex to string if it's bytes
        payload_hex_str = payload_hex.hex() if isinstance(payload_hex, bytes) else payload_hex

        # Check for duplicate events
        unit_id = event_data.get("unit_id")
        button = event_data.get("button")

        if unit_id is not None and button is not None and payload_hex_str:
            cache_key = (unit_id, button, payload_hex_str)
            current_time = time.time()

            # Clean up old entries from cache
            for key in list(event_cache.keys()):
                if current_time - event_cache[key] > DEDUP_WINDOW_SECONDS:
                    del event_cache[key]

            # Check if this event was seen recently
            if cache_key in event_cache:
                _LOGGER.debug(
                    "Skipping duplicate event: unit=%s, button=%s, payload=%s (last seen %.1fs ago)",
                    unit_id, button, payload_hex_str[:8],
                    current_time - event_cache[cache_key]
                )
                return

            # Record this event
            event_cache[cache_key] = current_time

        hass.bus.async_fire(
            f"{DOMAIN}_switch_event",
            {
                "entry_id": entry.entry_id,
                "unit_id": event_data.get("unit_id"),
                "button": event_data.get("button"),
                "action": event_data.get("event"),  # "button_press", "button_hold", "button_release", or "button_release_after_hold"
                "message_type": event_data.get("message_type"),
                "flags": event_data.get("flags"),
                "packet_sequence": event_data.get("packet_sequence"),
                "raw_packet": raw_packet.hex() if isinstance(raw_packet, bytes) else raw_packet,
                "decrypted_data": decrypted_data.hex() if isinstance(decrypted_data, bytes) else decrypted_data,
                "message_position": event_data.get("message_position"),
                "payload_hex": payload_hex_str,
                "extra_data": extra_data.hex() if isinstance(extra_data, bytes) else None,
            }
        )
        _LOGGER.debug(
            "Fired %s_switch_event for unit %s button %s - %s",
            DOMAIN,
            event_data.get('unit_id'),
            event_data.get('button'),
            event_data.get('event'),
        )

    # Register the event handler if the library supports it
    if hasattr(api.casa, 'registerSwitchEventHandler'):
        api.register_switch_event_callback(handle_switch_event)
        _LOGGER.info("Switch event handler registered - events will fire as casambi_bt_switch_event")

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
        self.casa: Casambi = Casambi(get_async_client(hass), get_cache_dir(hass))

        self._callback_map: dict[int, list[Callable[[Unit], None]]] = {}
        self._switch_event_callbacks: list[Callable[[dict], None]] = []
        self._cancel_bluetooth_callback: Callable[[], None] | None = None
        self._reconnect_lock = asyncio.Lock()
        self._first_disconnect = True

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

            # Register switch event handler if available (new in casambi-bt 0.3.0)
            if hasattr(self.casa, 'registerSwitchEventHandler'):
                self.casa.registerSwitchEventHandler(self._switch_event_handler)
            else:
                _LOGGER.warning("Switch event handler not available in casambi-bt library. Please update to latest version.")

            await self.casa.connect(device, self.password)
            self._first_disconnect = True
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

            # This needs to happen before we disconnect.
            # We don't want to be informed about disconnects initiated by us.
            self.casa.unregisterDisconnectCallback(self._casa_disconnect)

            try:
                await self.casa.disconnect()
            except Exception:
                _LOGGER.exception("Error during disconnect.")
            self.casa.unregisterUnitChangedHandler(self._unit_changed_handler)

            # Unregister switch event handler if available
            if hasattr(self.casa, 'unregisterSwitchEventHandler'):
                self.casa.unregisterSwitchEventHandler(self._switch_event_handler)

    @callback
    def _casa_disconnect(self) -> None:
        if self._first_disconnect:
            self._first_disconnect = False
            self.conf_entry.async_create_background_task(
                self.hass, self._delayed_reconnect(), "Delayed reconnect"
            )

    async def _delayed_reconnect(self) -> None:
        await asyncio.sleep(30)

        async with self._reconnect_lock:
            if self.casa.connected:
                return

        _LOGGER.debug("Starting delayed reconnect.")
        device = bluetooth.async_ble_device_from_address(self.hass, self.address)
        if device is not None:
            try:
                await self.try_reconnect()
            except Exception:
                _LOGGER.exception("Error during reconnect. This is not unusual.")
        else:
            _LOGGER.debug("Skipping reconnect. HA reports device not present.")

    async def try_reconnect(self) -> None:
        """Attemtps to reconnect to the Casambi network. Disconnects first to ensure a consitent state."""
        if self._reconnect_lock.locked():
            return

        # Use locking to ensure that only one reconnect can happen at a time.
        # Not sure if this is necessary.
        await self._reconnect_lock.acquire()

        try:
            try:
                await self.casa.disconnect()
            # HACK: This is a workaround for https://github.com/lkempf/casambi-bt-hass/issues/26
            # We don't actually need to disconnect except to clean up so this should be ok to ignore.
            except AttributeError:
                _LOGGER.debug("Unexpected failure during disconnect.")
            await self.connect()
        finally:
            self._reconnect_lock.release()

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
    def _switch_event_handler(self, event_data: dict) -> None:
        """Handle switch events from the Casambi network."""
        _LOGGER.debug("Switch event received: %s", event_data)
        for cb in self._switch_event_callbacks:
            if asyncio.iscoroutinefunction(cb):
                self.conf_entry.async_create_task(
                    self.hass, cb(event_data), "switch_event_callback"
                )
            else:
                cb(event_data)

    def register_switch_event_callback(self, callback: Callable[[dict], None]) -> None:
        """Register a callback for switch events."""
        self._switch_event_callbacks.append(callback)
        _LOGGER.debug("Registered switch event callback: %s", callback)

    def unregister_switch_event_callback(self, callback: Callable[[dict], None]) -> None:
        """Unregister a callback for switch events."""
        if callback in self._switch_event_callbacks:
            self._switch_event_callbacks.remove(callback)
            _LOGGER.debug("Unregistered switch event callback: %s", callback)

    @callback
    def _bluetooth_callback(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        if not self.casa.connected and service_info.connectable:
            self.conf_entry.async_create_background_task(
                self.hass, self.try_reconnect(), "Reconnect"
            )

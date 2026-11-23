"""Config flow for Casambi Bluetooth integration."""

from collections.abc import Awaitable, Callable, Mapping
from functools import partial
import json
import logging
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Final

from bleak.backends.device import BLEDevice
from CasambiBt import Casambi, normalizeAddress
from CasambiBt.errors import (
    AuthenticationError,
    CasambiBtError,
    CloudUnavailableError,
    NetworkNotFoundError,
    NetworkOnlineUpdateNeededError,
    OfflineBackupError,
)
import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_scanner_count,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.selector import (
    BooleanSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import (
    CasambiConfigEntry,
    async_migrate_cache,
    cache_uuid,
    credentials_rejected,
    get_cache_dir,
    is_rate_limited,
)
from .const import CONF_BACKUP, CONF_IMPORT_GROUPS, CONF_OFFLINE_MODE, DOMAIN

_LOGGER = logging.getLogger(__name__)

MAC_ADDRESS_REGEX: Final = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")

PASSWORD_SELECTOR: Final = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD)
)

USER_SCHEMA: Final = vol.Schema(
    {
        vol.Required(CONF_ADDRESS): TextSelector(),
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
        vol.Required(CONF_IMPORT_GROUPS, default=True): BooleanSelector(),
    }
)

BLUETOOTH_CONFIRM_SCHEMA: Final = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
        vol.Required(CONF_IMPORT_GROUPS, default=True): BooleanSelector(),
    }
)

PASSWORD_SCHEMA: Final = vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR})

BACKUP_SELECTOR: Final = TextSelector(TextSelectorConfig(multiline=True))

BACKUP_SCHEMA: Final = vol.Schema(
    {
        vol.Required(CONF_BACKUP): BACKUP_SELECTOR,
        vol.Required(CONF_OFFLINE_MODE, default=True): BooleanSelector(),
        vol.Optional(CONF_PASSWORD, default=""): PASSWORD_SELECTOR,
        vol.Required(CONF_IMPORT_GROUPS, default=True): BooleanSelector(),
    }
)

RESTORE_SCHEMA: Final = vol.Schema({vol.Required(CONF_BACKUP): BACKUP_SELECTOR})

OPTIONS_SCHEMA: Final = vol.Schema(
    {
        vol.Required(CONF_IMPORT_GROUPS, default=True): BooleanSelector(),
        vol.Required(CONF_OFFLINE_MODE, default=False): BooleanSelector(),
    }
)


def normalize_address(address: str) -> str:
    """Return the address in the upper case, colon separated notation."""
    return format_mac(address.strip()).upper()


async def _async_connect_once(casa: Casambi, device: BLEDevice, password: str) -> str:
    """Connect, read the network name and always disconnect again.

    The entry can't connect while the validation holds the connection, and
    bluetooth proxies only offer a few connection slots, so the connection
    is closed even if the flow is cancelled meanwhile.
    """
    try:
        await casa.connect(device, password)
        return casa.networkName
    finally:
        try:
            await casa.disconnect()
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug("Disconnecting after the validation failed", exc_info=True)


async def _validate_input(
    hass: HomeAssistant, address: str, password: str, *, verify_password: bool = False
) -> str:
    """Connect to the network once and return its name.

    With verify_password the login happens in an empty, temporary cache, so
    the cloud has to check the password: casambi-bt skips the login while a
    cached session is valid and falls back to the cache when the cloud is
    unreachable. The cache of the entry is left untouched.

    Raises the library errors if the network can't be reached or the
    password is rejected.
    """
    client = get_async_client(hass)
    bt_device = async_ble_device_from_address(hass, address, connectable=True)

    if not bt_device:
        raise NetworkNotFoundError

    if verify_password:
        temp_dir = Path(await hass.async_add_executor_job(tempfile.mkdtemp))
        try:
            # A missing cache directory is created by the library with
            # restrictive permissions and a valid version file.
            return await _async_connect_once(
                Casambi(client, temp_dir / "cache"), bt_device, password
            )
        finally:
            await hass.async_add_executor_job(
                partial(shutil.rmtree, temp_dir, ignore_errors=True)
            )

    uuid = cache_uuid(bt_device.address)
    await async_migrate_cache(hass, uuid)

    casa = Casambi(client, get_cache_dir(hass))
    try:
        # Connect with the existing cache first. A valid cached session
        # skips the cloud login, which keeps adding a network working even
        # while the cloud refuses new sessions.
        return await _async_connect_once(casa, bt_device, password)
    except AuthenticationError as err:
        if not credentials_rejected(err):
            raise
        # The cached session was rejected. Drop the cache and retry with a
        # genuine cloud login so a wrong password is still reported.
        _LOGGER.info(
            "Login with cached session failed (status %s), retrying fresh",
            err.status_code,
        )
        await casa.invalidateCache(uuid)
        return await _async_connect_once(
            Casambi(client, get_cache_dir(hass)), bt_device, password
        )


def _parse_backup(text: str) -> tuple[str, dict[str, Any]]:
    """Return the address and the content of a pasted offline backup.

    Only the address is read here to find the network; the library
    validates the rest when restoring it.

    :raises ValueError: The text isn't an offline backup.
    :raises TypeError: The text isn't a JSON object.
    """
    backup = json.loads(text)
    if not isinstance(backup, dict):
        raise TypeError("The backup isn't an object.")
    return normalizeAddress(str(backup["address"])), backup


async def _restore_backup(
    hass: HomeAssistant, backup: dict[str, Any], password: str
) -> str:
    """Restore an offline backup into the cache and connect once.

    The connection uses the restored configuration and never the cloud.

    Raises the library errors if the backup is invalid or the network
    can't be reached.
    """
    casa = Casambi(get_async_client(hass), get_cache_dir(hass))
    address = await casa.importOfflineBackup(backup)

    bt_device = async_ble_device_from_address(hass, address, connectable=True)
    if not bt_device:
        raise NetworkNotFoundError

    try:
        await casa.connect(bt_device, password, preferCache=True)
        return casa.networkName
    finally:
        try:
            await casa.disconnect()
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug("Disconnecting after the validation failed", exc_info=True)


class CasambiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Casambi Bluetooth."""

    VERSION = 2
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CasambiOptionsFlow:
        """Create the options flow."""
        return CasambiOptionsFlow()

    @callback
    def _async_create_casa_entry(
        self, title: str, address: str, user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        """Create the entry, keeping settings that don't affect the connection in options."""
        return self.async_create_entry(
            title=title,
            data={CONF_ADDRESS: address, CONF_PASSWORD: user_input[CONF_PASSWORD]},
            options={
                CONF_IMPORT_GROUPS: user_input[CONF_IMPORT_GROUPS],
                CONF_OFFLINE_MODE: user_input.get(CONF_OFFLINE_MODE, False),
            },
        )

    async def _async_try_connect(
        self, address: str, password: str, *, verify_password: bool = False
    ) -> tuple[str | None, dict[str, str]]:
        """Validate the credentials and map failures to form errors."""
        return await self._async_try(
            lambda: _validate_input(
                self.hass, address, password, verify_password=verify_password
            )
        )

    async def _async_try(
        self, connect: Callable[[], Awaitable[str]]
    ) -> tuple[str | None, dict[str, str]]:
        """Run a connection attempt and map failures to form errors."""
        errors: dict[str, str] = {}
        try:
            title = await connect()
        except OfflineBackupError:
            _LOGGER.debug("Invalid offline backup", exc_info=True)
            errors[CONF_BACKUP] = "invalid_backup"
        except AuthenticationError as err:
            _LOGGER.warning("Casambi login failed with status %s", err.status_code)
            errors["base"] = (
                "invalid_auth" if credentials_rejected(err) else "cannot_connect"
            )
        except CloudUnavailableError as err:
            _LOGGER.debug("Casambi cloud can't be used", exc_info=True)
            errors["base"] = (
                "rate_limited" if is_rate_limited(err) else "cloud_unreachable"
            )
        except NetworkOnlineUpdateNeededError:
            _LOGGER.debug("Casambi cloud not reachable", exc_info=True)
            errors["base"] = "cloud_unreachable"
        except CasambiBtError:
            _LOGGER.debug("Connecting failed", exc_info=True)
            errors["base"] = "cannot_connect"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            return title, errors
        return None, errors

    async def _async_try_connect_for_entry(
        self, entry: CasambiConfigEntry, password: str
    ) -> dict[str, str]:
        """Validate new credentials of an existing entry."""
        # A new password must be checked by the cloud, a cached session
        # would accept any password.
        return await self._async_with_entry_unloaded(
            entry,
            lambda: _validate_input(
                self.hass, entry.data[CONF_ADDRESS], password, verify_password=True
            ),
        )

    async def _async_with_entry_unloaded(
        self, entry: CasambiConfigEntry, connect: Callable[[], Awaitable[str]]
    ) -> dict[str, str]:
        """Run a connection attempt for an existing entry.

        A loaded entry holds the bluetooth connection to the network, which
        blocks a second connection. Unload it while validating and set it up
        again if the validation fails, so an aborted flow leaves it running.
        """
        was_loaded = entry.state is ConfigEntryState.LOADED
        if was_loaded:
            await self.hass.config_entries.async_unload(entry.entry_id)

        _, errors = await self._async_try(connect)

        if errors and was_loaded:
            await self.hass.config_entries.async_setup(entry.entry_id)
        return errors

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle bluetooth discovery."""
        _LOGGER.debug(
            "Discovery: [%s] %s from %s. Advertisement: %s.",
            discovery_info.address,
            discovery_info.name,
            discovery_info.source,
            discovery_info.advertisement,
        )

        await self.async_set_unique_id(normalize_address(discovery_info.address))
        self._abort_if_unique_id_configured()

        if not discovery_info.connectable:
            return self.async_abort(reason="not_supported")

        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {
            "name": discovery_info.name or discovery_info.address
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password of a discovered network."""
        assert self._discovery_info is not None
        address = normalize_address(self._discovery_info.address)
        errors: dict[str, str] = {}

        if user_input is not None:
            title, errors = await self._async_try_connect(
                address, user_input[CONF_PASSWORD]
            )
            if title is not None:
                return self._async_create_casa_entry(title, address, user_input)

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                BLUETOOTH_CONFIRM_SCHEMA, user_input
            ),
            description_placeholders=self.context["title_placeholders"],
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how to set up the network."""
        if async_scanner_count(self.hass, connectable=True) < 1:
            return self.async_abort(reason="bluetooth_error")
        return self.async_show_menu(step_id="user", menu_options=["cloud", "backup"])

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a network with the configuration from the Casambi cloud."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = normalize_address(user_input[CONF_ADDRESS])
            if not MAC_ADDRESS_REGEX.match(address):
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()

                title, errors = await self._async_try_connect(
                    address, user_input[CONF_PASSWORD]
                )
                if title is not None:
                    return self._async_create_casa_entry(title, address, user_input)

        return self.async_show_form(
            step_id="cloud",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_backup(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a network from an offline backup, without the cloud."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                address, backup = _parse_backup(user_input[CONF_BACKUP])
            except ValueError, KeyError, TypeError:
                errors[CONF_BACKUP] = "invalid_backup"
            else:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()

                password = user_input.get(CONF_PASSWORD, "")
                offline = user_input[CONF_OFFLINE_MODE]
                if not offline and not password:
                    # Getting changes from the cloud needs the password.
                    errors[CONF_PASSWORD] = "password_required"
                else:
                    title, errors = await self._async_try(
                        lambda: _restore_backup(self.hass, backup, password)
                    )
                    if title is not None and not offline:
                        # The cloud has to accept the password, otherwise
                        # the entry would ask for it right after the setup.
                        _, errors = await self._async_try_connect(
                            address, password, verify_password=True
                        )
                    if title is not None and not errors:
                        return self._async_create_casa_entry(
                            title, address, {**user_input, CONF_PASSWORD: password}
                        )

        return self.async_show_form(
            step_id="backup",
            data_schema=self.add_suggested_values_to_schema(
                BACKUP_SCHEMA,
                # Never show the pasted backup again, it holds the keys.
                {k: v for k, v in (user_input or {}).items() if k != CONF_BACKUP},
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication with Casambi."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-authentication with Casambi."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = await self._async_try_connect_for_entry(
                entry, user_input[CONF_PASSWORD]
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=PASSWORD_SCHEMA,
            description_placeholders={"name": entry.title},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose what to change on a configured network."""
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=["reconfigure_password", "restore_backup"],
            description_placeholders={"name": self._get_reconfigure_entry().title},
        )

    async def async_step_reconfigure_password(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the password of a configured network.

        The address identifies the network and is the unique id of the
        entry, so a different network has to be added as a new entry.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = await self._async_try_connect_for_entry(
                entry, user_input[CONF_PASSWORD]
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )

        return self.async_show_form(
            step_id="reconfigure_password",
            data_schema=PASSWORD_SCHEMA,
            description_placeholders={"name": entry.title},
            errors=errors,
        )

    async def async_step_restore_backup(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Restore the configuration of a network from an offline backup.

        Needed when the cache was lost and the cloud can't be used.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                address, backup = _parse_backup(user_input[CONF_BACKUP])
            except ValueError, KeyError, TypeError:
                errors[CONF_BACKUP] = "invalid_backup"
            else:
                if address != entry.data[CONF_ADDRESS]:
                    errors[CONF_BACKUP] = "wrong_network"
                else:
                    errors = await self._async_with_entry_unloaded(
                        entry,
                        lambda: _restore_backup(
                            self.hass, backup, entry.data[CONF_PASSWORD]
                        ),
                    )
                    if not errors:
                        return self.async_update_reload_and_abort(
                            entry, reload_even_if_entry_is_unchanged=True
                        )

        return self.async_show_form(
            step_id="restore_backup",
            data_schema=RESTORE_SCHEMA,
            description_placeholders={"name": entry.title},
            errors=errors,
        )


class CasambiOptionsFlow(OptionsFlowWithReload):
    """Handle the options of a Casambi network."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_OFFLINE_MODE] and not self.config_entry.data.get(
                CONF_PASSWORD
            ):
                # The password is part of the entry data and is checked by
                # the cloud, so it is set with reconfigure, not here.
                errors["base"] = "password_required"
            else:
                return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, user_input or self.config_entry.options
            ),
            errors=errors,
        )

"""Config flow for Casambi Bluetooth integration."""

from collections.abc import Mapping
import logging
import re
from typing import Any, Final

from CasambiBt import Casambi
from CasambiBt.errors import AuthenticationError, CasambiBtError, NetworkNotFoundError
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

from . import CasambiConfigEntry, get_cache_dir
from .const import CONF_IMPORT_GROUPS, DOMAIN
from .migration import migrate_legacy_cache

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

OPTIONS_SCHEMA: Final = vol.Schema(
    {vol.Required(CONF_IMPORT_GROUPS, default=True): BooleanSelector()}
)


def normalize_address(address: str) -> str:
    """Return the address in the upper case, colon separated notation."""
    return format_mac(address.strip()).upper()


async def _validate_input(hass: HomeAssistant, address: str, password: str) -> str:
    """Connect to the network once and return its name.

    Raises the library errors if the network can't be reached or the
    password is rejected.
    """
    client = get_async_client(hass)
    casa = Casambi(client, get_cache_dir(hass))
    bt_device = async_ble_device_from_address(hass, address, connectable=True)

    if not bt_device:
        raise NetworkNotFoundError

    # The library keys its cache directory by the mac without colons in
    # lower case.
    uuid = bt_device.address.replace(":", "").lower()
    await hass.async_add_executor_job(migrate_legacy_cache, get_cache_dir(hass), uuid)

    try:
        # Connect with the existing cache first. A valid cached session
        # skips the cloud login, which keeps the flow working even while
        # the cloud refuses new sessions.
        await casa.connect(bt_device, password)
    except AuthenticationError as err:
        # The cached session is missing, expired or rejected. Drop the
        # cache and retry with a genuine cloud login so a wrong password
        # is still reported to the user.
        _LOGGER.warning("Login with cached session failed, retrying fresh: %s", err)
        await casa.invalidateCache(uuid)
        casa = Casambi(client, get_cache_dir(hass))
        await casa.connect(bt_device, password)

    network_name = casa.networkName

    # Disconnect again, otherwise setting up the entry can't connect.
    await casa.disconnect()

    return network_name


class CasambiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Casambi Bluetooth."""

    VERSION = 2

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
            options={CONF_IMPORT_GROUPS: user_input[CONF_IMPORT_GROUPS]},
        )

    async def _async_try_connect(
        self, address: str, password: str
    ) -> tuple[str | None, dict[str, str]]:
        """Validate the credentials and map failures to form errors."""
        errors: dict[str, str] = {}
        try:
            title = await _validate_input(self.hass, address, password)
        except AuthenticationError as err:
            # The error carries the http status code and the cloud
            # response which tells apart a wrong password (401) from
            # a rate limit (403) or other issues.
            _LOGGER.warning("Casambi login failed: %s", err)
            errors["base"] = "invalid_auth"
        except CasambiBtError:
            _LOGGER.debug("Connecting to %s failed", address, exc_info=True)
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
        """Validate new credentials of an existing entry.

        A loaded entry holds the bluetooth connection to the network, which
        blocks a second connection. Unload it while validating and set it up
        again if the validation fails, so an aborted flow leaves it running.
        """
        was_loaded = entry.state is ConfigEntryState.LOADED
        if was_loaded:
            await self.hass.config_entries.async_unload(entry.entry_id)

        _, errors = await self._async_try_connect(entry.data[CONF_ADDRESS], password)

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
        """Handle entry of network information and attempt to connect."""
        if async_scanner_count(self.hass, connectable=True) < 1:
            return self.async_abort(reason="bluetooth_error")

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
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
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
            step_id="reconfigure",
            data_schema=PASSWORD_SCHEMA,
            description_placeholders={"name": entry.title},
            errors=errors,
        )


class CasambiOptionsFlow(OptionsFlowWithReload):
    """Handle the options of a Casambi network."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
        )

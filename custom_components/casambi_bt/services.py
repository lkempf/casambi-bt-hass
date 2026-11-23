"""Actions of the Casambi Bluetooth integration."""

from typing import Any, Final

from CasambiBt.errors import CasambiBtError
import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import (
    async_get_config_entry,
    async_register_admin_service,
)

from .const import DOMAIN

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
SERVICE_EXPORT_OFFLINE_BACKUP: Final = "export_offline_backup"

EXPORT_OFFLINE_BACKUP_SCHEMA: Final = vol.Schema(
    {vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string}
)


async def _async_export_offline_backup(call: ServiceCall) -> dict[str, Any]:
    """Return everything needed to use a network without the Casambi cloud.

    The response contains the keys of the network. The action is restricted
    to administrators.
    """
    entry = async_get_config_entry(call.hass, DOMAIN, call.data[ATTR_CONFIG_ENTRY_ID])
    try:
        backup: dict[str, Any] = await entry.runtime_data.casa.exportOfflineBackup()
    except CasambiBtError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="backup_failed"
        ) from err
    return backup


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions of the integration."""
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_EXPORT_OFFLINE_BACKUP,
        _async_export_offline_backup,
        schema=EXPORT_OFFLINE_BACKUP_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

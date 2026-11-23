"""Diagnostics support for Casambi Bluetooth."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import CasambiApi
from .const import DOMAIN

# Credentials and network identifiers (the network id is derived from the
# mac address) must not leak into diagnostic downloads.
TO_REDACT = {
    CONF_PASSWORD,
    CONF_ADDRESS,
    "address",
    "mac",
    "networkId",
    "network_id",
    "unique_id",
    "title",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    api: CasambiApi | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "connection": api.diagnostics() if api is not None else None,
    }

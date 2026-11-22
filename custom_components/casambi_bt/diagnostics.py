"""Diagnostics support for Casambi Bluetooth."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import CasambiApi, CasambiConfigEntry

# Credentials and network identifiers (the network id is derived from the
# mac address) must not leak into diagnostic downloads. discovery_keys hold
# the bluetooth address of networks that were discovered.
TO_REDACT = {
    CONF_PASSWORD,
    CONF_ADDRESS,
    "address",
    "discovery_keys",
    "mac",
    "networkId",
    "network_id",
    "unique_id",
    "title",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CasambiConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    api: CasambiApi | None = getattr(entry, "runtime_data", None)

    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "connection": api.diagnostics() if api is not None else None,
    }

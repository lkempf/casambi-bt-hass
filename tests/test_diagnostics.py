"""Test the diagnostics platform."""

from unittest.mock import AsyncMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_bt.diagnostics import async_get_config_entry_diagnostics
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .conftest import MockCasambi


async def test_diagnostics_redacts_secrets(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    enable_bluetooth: None,
) -> None:
    """Diagnostics must not contain the password or the raw address."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    result = await async_get_config_entry_diagnostics(hass, config_entry)

    assert result["entry"]["data"]["password"] == "**REDACTED**"
    assert result["entry"]["data"]["address"] == "**REDACTED**"
    assert result["entry"]["unique_id"] == "**REDACTED**"

    connection = result["connection"]
    assert connection["available"] is True
    assert connection["library_connected"] is True
    assert connection["units"] == []
    assert connection["reconnect_task"] is None

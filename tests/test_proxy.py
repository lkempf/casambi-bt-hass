"""Test the write proxy behaviour."""

from unittest.mock import AsyncMock

from CasambiBt.errors import BluetoothError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_bt import CasambiApi
from custom_components.casambi_bt.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import MockCasambi, drain


async def test_write_error_raises_and_schedules_reconnect(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    enable_bluetooth: None,
) -> None:
    """A bluetooth error during a write must raise and trigger a reconnect."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    api: CasambiApi = hass.data[DOMAIN][config_entry.entry_id]

    mock_casambi.setLevel = AsyncMock(side_effect=BluetoothError())

    with pytest.raises(HomeAssistantError):
        await api.casa.setLevel(None, 128)

    # A reconnect was scheduled and recovers the connection.
    await drain(hass)
    mock_casambi.reconnect.assert_awaited()
    assert api.available

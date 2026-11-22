"""Test sending commands to the network."""

from unittest.mock import AsyncMock

from CasambiBt._constants import ConnectionState
from CasambiBt.errors import BluetoothError, ConnectionStateError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_bt import CasambiApi
from custom_components.casambi_bt.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

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

    api: CasambiApi = config_entry.runtime_data

    mock_casambi.setLevel = AsyncMock(side_effect=BluetoothError())

    with pytest.raises(HomeAssistantError) as err:
        await api.async_send(api.casa.setLevel, None, 128)
    assert err.value.translation_key == "write_failed"

    # A reconnect was scheduled and recovers the connection.
    await drain(hass)
    mock_casambi.reconnect.assert_awaited()
    assert api.available


@pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")
async def test_library_error_raises_translated_error(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Other library errors must fail the action without a reconnect."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    api: CasambiApi = config_entry.runtime_data

    mock_casambi.turnOn = AsyncMock(
        side_effect=ConnectionStateError(
            ConnectionState.AUTHENTICATED, ConnectionState.NONE
        )
    )

    with pytest.raises(HomeAssistantError) as err:
        await api.async_send(api.casa.turnOn, None)

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "write_failed"
    mock_casambi.reconnect.assert_not_awaited()


@pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")
async def test_invalid_value_raises_validation_error(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """A value the library rejects must fail the action as invalid input."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    api: CasambiApi = config_entry.runtime_data

    mock_casambi.setColorXY = AsyncMock(side_effect=ValueError("Color out of range."))

    with pytest.raises(ServiceValidationError) as err:
        await api.async_send(api.casa.setColorXY, None, (2.0, 0.5))

    assert err.value.translation_key == "invalid_value"
    mock_casambi.reconnect.assert_not_awaited()

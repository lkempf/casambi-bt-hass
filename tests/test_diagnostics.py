"""Test the diagnostics platform."""

from unittest.mock import AsyncMock, PropertyMock

from CasambiBt import UnitControlType
from CasambiBt.errors import CasambiBtError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_bt.diagnostics import async_get_config_entry_diagnostics
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .conftest import MockCasambi, drain, make_state, make_unit


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


@pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")
async def test_diagnostics_with_units_and_reconnect(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Diagnostics must list the units and the reconnect task."""
    unit = make_unit(1, [UnitControlType.DIMMER], state=make_state(dimmer=5))
    mock_casambi.units = [unit]
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    config_entry.runtime_data._schedule_reconnect()  # noqa: SLF001
    await drain(hass)

    result = await async_get_config_entry_diagnostics(hass, config_entry)

    connection = result["connection"]
    assert connection["units"] == [
        {"id": 1, "name": "Unit 1", "online": True, "on": True}
    ]
    assert connection["reconnect_task"] == {"done": True, "cancelled": False}


async def test_diagnostics_not_loaded(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Diagnostics of an entry that is not loaded have no connection data."""
    config_entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, config_entry)

    assert result["connection"] is None


@pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")
async def test_diagnostics_without_network(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Missing network data must not break the diagnostics."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    type(mock_casambi).units = PropertyMock(side_effect=CasambiBtError())
    try:
        result = await async_get_config_entry_diagnostics(hass, config_entry)
    finally:
        del type(mock_casambi).units

    assert result["connection"]["units"] is None

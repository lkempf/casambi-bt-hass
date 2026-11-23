"""Test the shared entity behaviour."""

from copy import copy

from CasambiBt import Group, UnitControlType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_bt import CasambiApi
from custom_components.casambi_bt.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from .conftest import MockCasambi, make_state, make_unit

pytestmark = pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")


async def _setup(hass: HomeAssistant, config_entry: MockConfigEntry) -> CasambiApi:
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    return hass.data[DOMAIN][config_entry.entry_id]


async def test_unit_updates_are_written(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    group_config_entry: MockConfigEntry,
) -> None:
    """Unit updates from the library must update unit and group states."""
    unit = make_unit(1, [UnitControlType.DIMMER], state=make_state(dimmer=0))
    mock_casambi.units = [unit]
    mock_casambi.groups = [Group(groudId=3, name="Hall", units=[unit])]
    await _setup(hass, group_config_entry)

    unit_entity = "light.unit_1"
    group_entity = "light.test_network_hall"
    assert hass.states.get(unit_entity).state == STATE_OFF
    assert hass.states.get(group_entity).state == STATE_OFF

    handler = mock_casambi.registerUnitChangedHandler.call_args.args[0]
    updated = copy(unit)
    updated._state = make_state(dimmer=200)  # noqa: SLF001
    handler(updated)
    await hass.async_block_till_done()

    assert hass.states.get(unit_entity).state == STATE_ON
    assert hass.states.get(group_entity).state == STATE_ON

    # An update without a state only carries the online flag.
    offline = copy(unit)
    offline._state = None  # noqa: SLF001
    offline._online = False  # noqa: SLF001
    handler(offline)
    await hass.async_block_till_done()

    assert hass.states.get(unit_entity).state == STATE_UNAVAILABLE
    assert hass.states.get(group_entity).state == STATE_UNAVAILABLE


async def test_connection_state_updates_entities(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Losing the connection must be reflected by the status sensor."""
    api = await _setup(hass, config_entry)
    status = "binary_sensor.test_network_status"
    assert hass.states.get(status).state == STATE_ON

    api._link_up = False  # noqa: SLF001
    api._notify_connection_state()  # noqa: SLF001
    await hass.async_block_till_done()

    assert hass.states.get(status).state == STATE_OFF


async def test_callbacks_removed_on_unload(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    group_config_entry: MockConfigEntry,
) -> None:
    """Unloading must unregister every entity callback."""
    unit = make_unit(1, [UnitControlType.DIMMER, UnitControlType.VERTICAL])
    mock_casambi.units = [unit]
    mock_casambi.groups = [Group(groudId=3, name="Hall", units=[unit])]
    api = await _setup(hass, group_config_entry)

    assert api._callback_map[unit.deviceId]  # noqa: SLF001
    assert api._connection_callbacks  # noqa: SLF001

    await hass.config_entries.async_unload(group_config_entry.entry_id)
    await hass.async_block_till_done()

    assert api._callback_map[unit.deviceId] == []  # noqa: SLF001
    assert api._connection_callbacks == []  # noqa: SLF001

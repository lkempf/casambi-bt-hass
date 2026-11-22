"""Test the number platform."""

from unittest.mock import AsyncMock

from CasambiBt import Group, UnitControlType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import MockCasambi, make_state, make_unit

pytestmark = pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")


async def test_vertical_unit_and_group(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    group_config_entry: MockConfigEntry,
) -> None:
    """Vertical units and groups must expose and set their value."""
    unit_a = make_unit(
        1,
        [UnitControlType.DIMMER, UnitControlType.VERTICAL],
        state=make_state(dimmer=255, vertical=100),
    )
    unit_b = make_unit(
        2,
        [UnitControlType.DIMMER, UnitControlType.VERTICAL],
        state=make_state(dimmer=255, vertical=200),
    )
    plain = make_unit(3, [UnitControlType.DIMMER])
    mock_casambi.units = [unit_a, unit_b, plain]
    mock_casambi.groups = [
        Group(groudId=5, name="Office", units=[unit_a, unit_b]),
        Group(groudId=6, name="Plain", units=[plain]),
    ]
    mock_casambi.setVertical = AsyncMock()

    group_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(group_config_entry.entry_id)
    await hass.async_block_till_done()
    assert group_config_entry.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    network_id = mock_casambi.networkId
    unit_entity = registry.async_get_entity_id(
        "number", "casambi_bt", f"{network_id}-unit-{unit_a.uuid}-vertical"
    )
    group_entity = registry.async_get_entity_id(
        "number", "casambi_bt", f"{network_id}-group-5"
    )
    assert unit_entity is not None
    assert group_entity is not None
    # Only units and groups with a vertical control get an entity.
    assert len(hass.states.async_entity_ids("number")) == 3

    assert float(hass.states.get(unit_entity).state) == 100
    assert float(hass.states.get(group_entity).state) == 150

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": unit_entity, "value": 42},
        blocking=True,
    )
    mock_casambi.setVertical.assert_awaited_once_with(unit_a, 42)

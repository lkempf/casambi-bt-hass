"""Test the light platform."""

from unittest.mock import AsyncMock

from CasambiBt import ColorSource, Group, UnitControlType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.light import ATTR_COLOR_MODE, ATTR_RGBW_COLOR, ColorMode
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import MockCasambi, make_state, make_unit

pytestmark = pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")


async def _setup(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED


def _entity_id(hass: HomeAssistant, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("light", "casambi_bt", unique_id)
    assert entity_id is not None
    return entity_id


@pytest.mark.parametrize(
    ("colorsource", "expected"),
    [
        (ColorSource.TEMPERATURE, ColorMode.COLOR_TEMP),
        (ColorSource.RGB, ColorMode.RGB),
        (ColorSource.XY, ColorMode.XY),
        (None, ColorMode.RGB),
    ],
)
async def test_color_mode_follows_color_source(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
    colorsource: ColorSource | None,
    expected: ColorMode,
) -> None:
    """The reported color mode must follow the active color source."""
    state = make_state(dimmer=255, rgb=(255, 0, 0), temperature=3000, xy=(0.3, 0.3))
    if colorsource is not None:
        state.colorsource = colorsource
    unit = make_unit(
        1,
        [
            UnitControlType.DIMMER,
            UnitControlType.RGB,
            UnitControlType.TEMPERATURE,
            UnitControlType.XY,
        ],
        state=state,
    )
    mock_casambi.units = [unit]

    await _setup(hass, config_entry)

    entity_id = _entity_id(hass, f"{mock_casambi.networkId}-unit-{unit.uuid}-light")
    assert hass.states.get(entity_id).attributes[ATTR_COLOR_MODE] == expected


async def test_group_rgbw_without_state_values(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    group_config_entry: MockConfigEntry,
) -> None:
    """A group of RGBW units without color values must not crash."""
    unit = make_unit(
        1,
        [UnitControlType.DIMMER, UnitControlType.RGB, UnitControlType.WHITE],
        state=make_state(dimmer=128),
    )
    mock_casambi.units = [unit]
    mock_casambi.groups = [Group(groudId=7, name="Living", units=[unit])]
    await _setup(hass, group_config_entry)

    entity_id = _entity_id(hass, f"{mock_casambi.networkId}-group-7")
    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes[ATTR_RGBW_COLOR] is None
    assert state.attributes[ATTR_COLOR_MODE] == ColorMode.RGBW


async def test_turn_on_unit_with_color(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Turning on with a color must send the full unit state."""
    unit = make_unit(
        1,
        [UnitControlType.DIMMER, UnitControlType.RGB],
        state=make_state(dimmer=10, rgb=(0, 0, 0)),
    )
    mock_casambi.units = [unit]
    mock_casambi.setUnitState = AsyncMock()

    await _setup(hass, config_entry)
    entity_id = _entity_id(hass, f"{mock_casambi.networkId}-unit-{unit.uuid}-light")

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": entity_id, "rgb_color": (1, 2, 3), "brightness": 200},
        blocking=True,
    )

    mock_casambi.setUnitState.assert_awaited_once()
    sent = mock_casambi.setUnitState.await_args.args[1]
    assert sent.rgb == (1, 2, 3)
    assert sent.dimmer == 200
    assert sent.colorsource is ColorSource.RGB

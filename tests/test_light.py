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


async def test_turn_on_group_with_xy_color(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    group_config_entry: MockConfigEntry,
) -> None:
    """Turning on a group of xy lights with a color must set the color."""
    unit = make_unit(
        1,
        [UnitControlType.DIMMER, UnitControlType.XY],
        state=make_state(dimmer=100, xy=(0.3, 0.3)),
    )
    mock_casambi.units = [unit]
    mock_casambi.groups = [Group(groudId=7, name="Living", units=[unit])]
    mock_casambi.setColorXY = AsyncMock()
    mock_casambi.setLevel = AsyncMock()
    mock_casambi.turnOn = AsyncMock()

    await _setup(hass, group_config_entry)
    entity_id = _entity_id(hass, f"{mock_casambi.networkId}-group-7")

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": entity_id, "xy_color": (0.4, 0.5)},
        blocking=True,
    )

    group = mock_casambi.groups[0]
    mock_casambi.setColorXY.assert_awaited_once_with(group, (0.4, 0.5))
    mock_casambi.setLevel.assert_awaited_once_with(group, 100)
    mock_casambi.turnOn.assert_not_awaited()


@pytest.mark.parametrize(
    ("controls", "modes"),
    [
        ([UnitControlType.ONOFF], [ColorMode.ONOFF]),
        ([UnitControlType.DIMMER], [ColorMode.BRIGHTNESS]),
        (
            [UnitControlType.DIMMER, UnitControlType.RGB, UnitControlType.WHITE],
            [ColorMode.RGBW],
        ),
        ([UnitControlType.DIMMER, UnitControlType.TEMPERATURE], [ColorMode.COLOR_TEMP]),
        ([UnitControlType.DIMMER, UnitControlType.XY], [ColorMode.XY]),
    ],
)
async def test_supported_color_modes(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
    controls: list[UnitControlType],
    modes: list[ColorMode],
) -> None:
    """The supported color modes must follow the unit controls."""
    unit = make_unit(1, controls, state=make_state(dimmer=10))
    mock_casambi.units = [unit]

    await _setup(hass, config_entry)

    entity_id = _entity_id(hass, f"{mock_casambi.networkId}-unit-{unit.uuid}-light")
    state = hass.states.get(entity_id)
    assert state.attributes["supported_color_modes"] == modes


@pytest.mark.parametrize(
    ("service_data", "expected"),
    [
        ({"rgbw_color": (1, 2, 3, 4)}, {"rgb": (1, 2, 3), "white": 4}),
        ({"color_temp_kelvin": 4000}, {"temperature": 4000}),
        ({"xy_color": (0.2, 0.3)}, {"xy": (0.2, 0.3)}),
    ],
)
async def test_turn_on_unit_colors(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
    service_data: dict,
    expected: dict,
) -> None:
    """Every color attribute must be sent as part of the unit state."""
    unit = make_unit(
        1,
        [
            UnitControlType.DIMMER,
            UnitControlType.RGB,
            UnitControlType.WHITE,
            UnitControlType.TEMPERATURE,
            UnitControlType.XY,
        ],
    )
    mock_casambi.units = [unit]
    mock_casambi.setUnitState = AsyncMock()

    await _setup(hass, config_entry)
    entity_id = _entity_id(hass, f"{mock_casambi.networkId}-unit-{unit.uuid}-light")

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id, **service_data}, blocking=True
    )

    sent = mock_casambi.setUnitState.await_args.args[1]
    for key, value in expected.items():
        assert getattr(sent, key) == value


async def test_turn_on_and_off_unit(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Turning on without attributes restores the last level."""
    unit = make_unit(1, [UnitControlType.DIMMER], state=make_state(dimmer=10))
    mock_casambi.units = [unit]
    mock_casambi.turnOn = AsyncMock()
    mock_casambi.turnOff = AsyncMock()

    await _setup(hass, config_entry)
    entity_id = _entity_id(hass, f"{mock_casambi.networkId}-unit-{unit.uuid}-light")
    assert hass.states.get(entity_id).attributes["brightness"] == 10

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": entity_id}, blocking=True
    )

    mock_casambi.turnOn.assert_awaited_once_with(unit)
    mock_casambi.turnOff.assert_awaited_once_with(unit)


async def test_group_attributes_and_turn_on(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    group_config_entry: MockConfigEntry,
) -> None:
    """Groups must report their units' values and forward commands."""
    state = make_state(
        dimmer=50, rgb=(1, 2, 3), white=4, temperature=3000, xy=(0.1, 0.2)
    )
    state.colorsource = ColorSource.TEMPERATURE
    unit = make_unit(
        1,
        [
            UnitControlType.DIMMER,
            UnitControlType.RGB,
            UnitControlType.WHITE,
            UnitControlType.TEMPERATURE,
            UnitControlType.XY,
        ],
        state=state,
    )
    mock_casambi.units = [unit]
    group = Group(groudId=7, name="Living", units=[unit])
    mock_casambi.groups = [group]
    for name in ("setLevel", "setTemperature", "setColor", "setWhite", "turnOn"):
        setattr(mock_casambi, name, AsyncMock())

    await _setup(hass, group_config_entry)
    entity_id = _entity_id(hass, f"{mock_casambi.networkId}-group-7")

    attributes = hass.states.get(entity_id).attributes
    assert attributes[ATTR_COLOR_MODE] == ColorMode.COLOR_TEMP
    assert attributes["brightness"] == 50
    assert attributes["color_temp_kelvin"] == 3000

    async def turn_on(**data) -> None:
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": entity_id, **data}, blocking=True
        )

    await turn_on()
    mock_casambi.turnOn.assert_awaited_once_with(group)

    await turn_on(brightness=80, color_temp_kelvin=4000)
    mock_casambi.setLevel.assert_awaited_with(group, 80)
    mock_casambi.setTemperature.assert_awaited_with(group, 4000)

    await turn_on(rgbw_color=(9, 8, 7, 6))
    mock_casambi.setColor.assert_awaited_with(group, (9, 8, 7))
    mock_casambi.setWhite.assert_awaited_with(group, 6)
    # Without a brightness the group level is synced so all units turn on.
    mock_casambi.setLevel.assert_awaited_with(group, 50)


async def test_group_without_values(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    group_config_entry: MockConfigEntry,
) -> None:
    """A group whose units have no state must report no values."""
    unit = make_unit(
        1,
        [
            UnitControlType.DIMMER,
            UnitControlType.RGB,
            UnitControlType.TEMPERATURE,
            UnitControlType.XY,
        ],
        on=False,
    )
    mock_casambi.units = [unit]
    mock_casambi.groups = [Group(groudId=7, name="Living", units=[unit])]

    await _setup(hass, group_config_entry)

    entity_id = _entity_id(hass, f"{mock_casambi.networkId}-group-7")
    group = hass.states.get(entity_id)
    assert group.state == "off"
    light = hass.data["light"].get_entity(entity_id)
    assert light.brightness is None
    assert light.rgb_color is None
    assert light.color_temp_kelvin is None
    assert light.xy_color is None


async def test_turn_on_group_with_rgb_color(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    group_config_entry: MockConfigEntry,
) -> None:
    """Turning on a group of rgb lights with a color must set the color."""
    unit = make_unit(
        1, [UnitControlType.DIMMER, UnitControlType.RGB], state=make_state(dimmer=9)
    )
    mock_casambi.units = [unit]
    mock_casambi.groups = [Group(groudId=7, name="Living", units=[unit])]
    mock_casambi.setColor = AsyncMock()
    mock_casambi.setLevel = AsyncMock()

    await _setup(hass, group_config_entry)
    entity_id = _entity_id(hass, f"{mock_casambi.networkId}-group-7")

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": entity_id, "rgb_color": (9, 8, 7)},
        blocking=True,
    )

    group = mock_casambi.groups[0]
    mock_casambi.setColor.assert_awaited_once_with(group, (9, 8, 7))
    mock_casambi.setLevel.assert_awaited_once_with(group, 9)


@pytest.mark.parametrize(
    ("controls", "expected"),
    [
        # Dimmers and color lights: brightness is implied by the color mode.
        (
            [[UnitControlType.DIMMER], [UnitControlType.DIMMER, UnitControlType.RGB]],
            [ColorMode.RGB],
        ),
        # Switches and dimmers.
        ([[UnitControlType.ONOFF], [UnitControlType.DIMMER]], [ColorMode.BRIGHTNESS]),
        # Tunable white and color lights.
        (
            [
                [UnitControlType.DIMMER, UnitControlType.TEMPERATURE],
                [UnitControlType.DIMMER, UnitControlType.XY],
            ],
            [ColorMode.COLOR_TEMP, ColorMode.XY],
        ),
    ],
)
async def test_mixed_groups_are_added(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    group_config_entry: MockConfigEntry,
    controls: list[list[UnitControlType]],
    expected: list[ColorMode],
) -> None:
    """Groups of different lights must be added with valid color modes.

    Home Assistant refuses entities with e.g. BRIGHTNESS and RGB, which
    used to drop such groups entirely.
    """
    units = [
        make_unit(i, unit_controls, state=make_state(dimmer=10))
        for i, unit_controls in enumerate(controls, start=1)
    ]
    mock_casambi.units = units
    mock_casambi.groups = [Group(groudId=7, name="Mixed", units=units)]

    await _setup(hass, group_config_entry)

    entity_id = _entity_id(hass, f"{mock_casambi.networkId}-group-7")
    state = hass.states.get(entity_id)
    assert sorted(state.attributes["supported_color_modes"]) == sorted(expected)


async def test_group_without_lights_is_skipped(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    group_config_entry: MockConfigEntry,
) -> None:
    """A group of units without light controls must not become a light."""
    sensor = make_unit(1, [UnitControlType.LUX])
    mock_casambi.units = [sensor]
    mock_casambi.groups = [Group(groudId=7, name="Sensors", units=[sensor])]

    await _setup(hass, group_config_entry)

    assert hass.states.async_entity_ids("light") == []


async def test_white_only_unit_is_on_off_light(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """A unit with only a white channel must be added as on/off light."""
    unit = make_unit(1, [UnitControlType.WHITE])
    mock_casambi.units = [unit]

    await _setup(hass, config_entry)

    entity_id = _entity_id(hass, f"{mock_casambi.networkId}-unit-{unit.uuid}-light")
    assert hass.states.get(entity_id).attributes["supported_color_modes"] == [
        ColorMode.ONOFF
    ]


async def test_xy_only_unit_is_light(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """A unit with only a xy color control must be added as light."""
    unit = make_unit(1, [UnitControlType.XY])
    mock_casambi.units = [unit]

    await _setup(hass, config_entry)

    entity_id = _entity_id(hass, f"{mock_casambi.networkId}-unit-{unit.uuid}-light")
    assert hass.states.get(entity_id).attributes["supported_color_modes"] == [
        ColorMode.XY
    ]


async def test_turn_on_off_unit_with_color_only(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Setting only a color on an off unit must turn it on."""
    state = make_state(dimmer=0, rgb=(0, 0, 0), onoff=False)
    unit = make_unit(
        1,
        [UnitControlType.ONOFF, UnitControlType.DIMMER, UnitControlType.RGB],
        state=state,
        on=False,
    )
    mock_casambi.units = [unit]
    calls: list[str] = []
    mock_casambi.setUnitState = AsyncMock(
        side_effect=lambda *_: calls.append("setUnitState")
    )
    mock_casambi.turnOn = AsyncMock(side_effect=lambda *_: calls.append("turnOn"))

    await _setup(hass, config_entry)
    entity_id = _entity_id(hass, f"{mock_casambi.networkId}-unit-{unit.uuid}-light")
    assert hass.states.get(entity_id).state == "off"

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": entity_id, "rgb_color": (255, 0, 0)},
        blocking=True,
    )

    sent = mock_casambi.setUnitState.await_args.args[1]
    assert sent.rgb == (255, 0, 0)
    assert sent.onoff is True
    # The level of the off unit is unknown, so the last level is restored
    # after the color is set.
    assert calls == ["setUnitState", "turnOn"]


async def test_turn_on_on_unit_with_color_keeps_level(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Changing the color of an on unit must not touch its level."""
    unit = make_unit(
        1,
        [UnitControlType.DIMMER, UnitControlType.RGB],
        state=make_state(dimmer=80, rgb=(0, 0, 0)),
    )
    mock_casambi.units = [unit]
    mock_casambi.setUnitState = AsyncMock()
    mock_casambi.turnOn = AsyncMock()

    await _setup(hass, config_entry)
    entity_id = _entity_id(hass, f"{mock_casambi.networkId}-unit-{unit.uuid}-light")

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": entity_id, "rgb_color": (0, 255, 0)},
        blocking=True,
    )

    assert mock_casambi.setUnitState.await_args.args[1].dimmer == 80
    mock_casambi.turnOn.assert_not_awaited()


async def test_turn_on_off_group_with_color_only(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    group_config_entry: MockConfigEntry,
) -> None:
    """Setting only a color on an off group must turn it on, not off."""
    unit = make_unit(
        1,
        [UnitControlType.DIMMER, UnitControlType.RGB],
        state=make_state(dimmer=0, rgb=(0, 0, 0)),
        on=False,
    )
    mock_casambi.units = [unit]
    mock_casambi.groups = [Group(groudId=7, name="Living", units=[unit])]
    mock_casambi.setColor = AsyncMock()
    mock_casambi.setLevel = AsyncMock()
    mock_casambi.turnOn = AsyncMock()

    await _setup(hass, group_config_entry)
    entity_id = _entity_id(hass, f"{mock_casambi.networkId}-group-7")

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": entity_id, "rgb_color": (1, 2, 3)},
        blocking=True,
    )

    group = mock_casambi.groups[0]
    mock_casambi.setColor.assert_awaited_once_with(group, (1, 2, 3))
    mock_casambi.setLevel.assert_not_awaited()
    mock_casambi.turnOn.assert_awaited_once_with(group)

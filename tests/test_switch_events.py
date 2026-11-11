"""Tests for forwarding Casambi switch events to the Home Assistant event bus."""

from unittest.mock import AsyncMock, MagicMock, patch

from CasambiBt._switch import ButtonEventType, SwitchEvent
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.casambi_bt import CasambiApi
from custom_components.casambi_bt.const import DOMAIN, EVENT_SWITCH
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

ADDRESS = "00:11:22:33:44:55"


def _switch_event(
    unit_id: int = 7,
    button: int = 2,
    event: ButtonEventType = ButtonEventType.PRESS,
) -> SwitchEvent:
    return SwitchEvent(
        button_event_index=button - 1,
        button=button,
        unit_id=unit_id,
        target_type=0x06,
        event=event,
        flags=0,
        extra_data=b"\x01",
    )


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a config entry added to hass."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_PASSWORD: "secret"},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def api(hass: HomeAssistant, entry: MockConfigEntry) -> CasambiApi:
    """Return a CasambiApi with a mocked Casambi client."""
    with (
        patch("custom_components.casambi_bt.Casambi") as casambi_cls,
        patch("custom_components.casambi_bt.get_async_client"),
    ):
        casa = casambi_cls.return_value
        casa.connect = AsyncMock()
        casa.disconnect = AsyncMock()
        casa.units = []
        return CasambiApi(hass, entry, ADDRESS, "secret")


async def test_switch_handler_lifecycle(api: CasambiApi) -> None:
    """The switch handler is registered on connect and removed on disconnect."""
    casa = api._casa  # noqa: SLF001
    with (
        patch(
            "custom_components.casambi_bt.bluetooth.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch("custom_components.casambi_bt.bluetooth.async_register_callback"),
    ):
        await api.connect()

    casa.registerSwitchEventHandler.assert_called_once_with(
        api._switch_event_handler  # noqa: SLF001
    )
    casa.unregisterSwitchEventHandler.assert_not_called()

    await api.disconnect()

    casa.unregisterSwitchEventHandler.assert_called_once_with(
        api._switch_event_handler  # noqa: SLF001
    )


async def test_switch_event_fired_for_known_unit(
    hass: HomeAssistant, entry: MockConfigEntry, api: CasambiApi
) -> None:
    """A switch event is fired with JSON-serializable unit and device data."""
    unit = MagicMock(deviceId=7, uuid="unit-uuid")
    unit.name = "Hallway switch"
    api._casa.units = [unit]  # noqa: SLF001
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "unit-uuid")}
    )
    events = async_capture_events(hass, EVENT_SWITCH)

    api._switch_event_handler(  # noqa: SLF001
        _switch_event(event=ButtonEventType.RELEASE_AFTER_HOLD)
    )
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data == {
        "entry_id": entry.entry_id,
        "device_id": device.id,
        "unit_id": 7,
        "unit_uuid": "unit-uuid",
        "unit_name": "Hallway switch",
        "button": 2,
        "event": "release_after_hold",
    }


async def test_switch_event_fired_for_unknown_unit(
    hass: HomeAssistant, entry: MockConfigEntry, api: CasambiApi
) -> None:
    """Events from units missing in the network config are still forwarded."""
    events = async_capture_events(hass, EVENT_SWITCH)

    api._switch_event_handler(_switch_event(unit_id=99))  # noqa: SLF001
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data == {
        "entry_id": entry.entry_id,
        "device_id": None,
        "unit_id": 99,
        "unit_uuid": None,
        "unit_name": None,
        "button": 2,
        "event": "press",
    }


async def test_quick_repeated_presses_are_not_dropped(
    hass: HomeAssistant, api: CasambiApi
) -> None:
    """Consecutive presses are all forwarded so automations can detect double clicks."""
    events = async_capture_events(hass, EVENT_SWITCH)

    for event_type in (
        ButtonEventType.PRESS,
        ButtonEventType.RELEASE,
        ButtonEventType.PRESS,
        ButtonEventType.RELEASE,
    ):
        api._switch_event_handler(_switch_event(event=event_type))  # noqa: SLF001
    await hass.async_block_till_done()

    assert [e.data["event"] for e in events] == [
        "press",
        "release",
        "press",
        "release",
    ]

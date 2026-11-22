"""Test the reconnect task behaviour."""

import asyncio
from unittest.mock import AsyncMock, patch

from CasambiBt.errors import AuthenticationError, BluetoothError, ProtocolError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_bt import CasambiApi
from custom_components.casambi_bt.const import DOMAIN, RECONNECT_BACKOFF_MAX
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .conftest import MockCasambi, drain, real_sleep


async def _setup(hass: HomeAssistant, config_entry: MockConfigEntry) -> CasambiApi:
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    return hass.data[DOMAIN][config_entry.entry_id]


async def test_disconnect_schedules_reconnect(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    enable_bluetooth: None,
) -> None:
    """Regression test for lkempf/casambi-bt-hass#170.

    The library raises its disconnect callback while still reporting
    connected. The reconnect task must not be started eagerly in that
    callback, otherwise it returns without reconnecting and the
    integration stays dead forever.
    """
    api = await _setup(hass, config_entry)

    # Simulate the library state during its disconnect callback:
    # connected is still True while the callback runs.
    mock_casambi.connected = True
    api._casa_disconnect()  # noqa: SLF001
    await drain(hass)

    # The reconnect must have run despite the stale connected flag.
    mock_casambi.reconnect.assert_awaited_once()
    assert api.available


async def test_reconnect_retries_on_bluetooth_error(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    enable_bluetooth: None,
) -> None:
    """A bluetooth error during reconnect must be retried with backoff."""
    api = await _setup(hass, config_entry)

    attempts = []

    async def _reconnect(_device):
        attempts.append(_device)
        if len(attempts) == 1:
            raise BluetoothError
        mock_casambi.connected = True

    mock_casambi.connected = False
    mock_casambi.reconnect = AsyncMock(side_effect=_reconnect)

    with patch("custom_components.casambi_bt.asyncio.sleep", new=AsyncMock()):
        api._schedule_reconnect()  # noqa: SLF001
        await drain(hass)

    assert len(attempts) == 2
    assert api.available


async def test_reconnect_times_out_and_retries(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    enable_bluetooth: None,
) -> None:
    """A hanging reconnect attempt must be aborted after CONNECT_TIMEOUT."""
    api = await _setup(hass, config_entry)

    attempts = []

    async def _reconnect(_device):
        attempts.append(_device)
        if len(attempts) == 1:
            # Never resolves: must be aborted by the timeout wrapper.
            await real_sleep(10)
        mock_casambi.connected = True

    mock_casambi.connected = False
    mock_casambi.reconnect = AsyncMock(side_effect=_reconnect)

    with (
        patch("custom_components.casambi_bt.CONNECT_TIMEOUT", 0.05),
        patch("custom_components.casambi_bt.asyncio.sleep", new=AsyncMock()),
    ):
        api._schedule_reconnect()  # noqa: SLF001
        await drain(hass, rounds=50, tick=0.01)

    assert len(attempts) == 2
    assert api.available


async def test_disconnect_cancels_reconnect(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    enable_bluetooth: None,
) -> None:
    """An intentional disconnect must stop a pending reconnect loop."""
    api = await _setup(hass, config_entry)

    hanging = asyncio.Event()

    async def _reconnect(_device):
        await hanging.wait()
        mock_casambi.connected = True

    mock_casambi.connected = False
    mock_casambi.reconnect = AsyncMock(side_effect=_reconnect)

    api._schedule_reconnect()  # noqa: SLF001
    await drain(hass, rounds=5)

    await api.disconnect()
    await drain(hass, rounds=5)

    assert api._reconnect_task is None or api._reconnect_task.done()  # noqa: SLF001
    assert not api.available


async def test_reconnect_auth_error_starts_reauth(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    enable_bluetooth: None,
) -> None:
    """Rejected credentials while reconnecting must start the reauth flow."""
    api = await _setup(hass, config_entry)

    mock_casambi.connected = False
    mock_casambi.reconnect = AsyncMock(side_effect=AuthenticationError())

    api._schedule_reconnect()  # noqa: SLF001
    await drain(hass)

    mock_casambi.reconnect.assert_awaited_once()
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == "reauth"
    assert not api.available


@pytest.mark.parametrize(
    "errors",
    [
        [ProtocolError(), ProtocolError()],
        [RuntimeError("boom"), RuntimeError("boom")],
    ],
)
@pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")
async def test_reconnect_survives_persistent_errors(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
    errors: list[Exception],
) -> None:
    """Persistent errors must not kill the reconnect loop."""
    api = await _setup(hass, config_entry)

    attempts = []
    sleeps: list[float] = []

    async def _reconnect(_device):
        attempts.append(_device)
        if len(attempts) <= len(errors):
            raise errors[len(attempts) - 1]
        mock_casambi.connected = True

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)

    mock_casambi.connected = False
    mock_casambi.reconnect = AsyncMock(side_effect=_reconnect)

    with patch("custom_components.casambi_bt.asyncio.sleep", new=_sleep):
        api._schedule_reconnect()  # noqa: SLF001
        await drain(hass)

    assert len(attempts) == len(errors) + 1
    assert api.available
    # The second failure falls back to the maximum backoff.
    assert max(sleeps) >= RECONNECT_BACKOFF_MAX

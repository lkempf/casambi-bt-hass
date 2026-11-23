"""Test the offline-first start and the network refresh."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from CasambiBt.errors import (
    AuthenticationError,
    CloudUnavailableError,
    NetworkUpdateError,
)
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import MockCasambi, drain

pytestmark = pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")


async def _setup(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await drain(hass)
    assert config_entry.state is ConfigEntryState.LOADED


async def test_starts_from_cache_and_refreshes(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """The network starts from the cache and checks the cloud afterwards."""
    await _setup(hass, config_entry)

    connect_kwargs = mock_casambi.connect.await_args.kwargs
    assert connect_kwargs["preferCache"] is True
    assert connect_kwargs["bleDeviceCallback"]() is not None
    mock_casambi.refreshNetwork.assert_awaited_once_with("secret-password")

    # Daily refresh.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=25))
    await drain(hass)
    assert mock_casambi.refreshNetwork.await_count == 2
    assert mock_casambi.connect.await_count == 1


async def test_changed_network_reloads_entry(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """A changed configuration is applied by reloading the entry."""
    mock_casambi.refreshNetwork = AsyncMock(side_effect=[True, False])

    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert mock_casambi.connect.await_count == 2


@pytest.mark.parametrize(
    "error",
    [
        CloudUnavailableError("offline"),
        CloudUnavailableError("Login failed: 503", 503),
        NetworkUpdateError("malformed"),
        AuthenticationError("Not authenticated!"),
        RuntimeError("bug"),
    ],
)
async def test_refresh_errors_keep_running(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
    error: Exception,
) -> None:
    """Without the cloud the network keeps running from the cache."""
    mock_casambi.refreshNetwork = AsyncMock(side_effect=error)

    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.runtime_data.available
    assert not hass.config_entries.flow.async_progress()


async def test_refresh_rejected_password_starts_reauth(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """A rejected password asks for a new one; the network keeps running."""
    mock_casambi.refreshNetwork = AsyncMock(
        side_effect=AuthenticationError("Login failed: 401", status_code=401)
    )

    await _setup(hass, config_entry)

    assert config_entry.runtime_data.available
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == "reauth"


async def test_refresh_does_not_overlap(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Refreshes don't run concurrently."""
    await _setup(hass, config_entry)
    api = config_entry.runtime_data
    calls = mock_casambi.refreshNetwork.await_count

    with patch.object(api, "_refreshing", True):
        await api.async_refresh_network()

    assert mock_casambi.refreshNetwork.await_count == calls

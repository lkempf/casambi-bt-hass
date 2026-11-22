"""Tests for the config flow input validation."""

from unittest.mock import AsyncMock

from CasambiBt.errors import AuthenticationError

from custom_components.casambi_bt.config_flow import _validate_input
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .conftest import TEST_ADDRESS, TEST_UUID, MockCasambi


async def test_validate_input_reuses_cached_session(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    mock_bluetooth: AsyncMock,
) -> None:
    """A working cached session must be used without touching the cache."""
    info = await _validate_input(
        hass,
        {CONF_ADDRESS: TEST_ADDRESS, CONF_PASSWORD: "secret-password"},
    )

    assert info == {"title": "Test Network", "id": TEST_ADDRESS}
    mock_casambi.connect.assert_awaited_once()
    mock_casambi.invalidateCache.assert_not_awaited()


async def test_validate_input_falls_back_to_cloud_login(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    mock_bluetooth: AsyncMock,
) -> None:
    """A rejected cached session must trigger a fresh cloud login.

    The cache is invalidated with the normalized uuid (mac without colons
    in lower case) which is how the library names its cache directory.
    """
    mock_casambi.connect = AsyncMock(
        side_effect=[AuthenticationError("Login failed: 401"), None]
    )

    info = await _validate_input(
        hass,
        {CONF_ADDRESS: TEST_ADDRESS, CONF_PASSWORD: "secret-password"},
    )

    assert info == {"title": "Test Network", "id": TEST_ADDRESS}
    assert mock_casambi.connect.await_count == 2
    mock_casambi.invalidateCache.assert_awaited_once_with(TEST_UUID)

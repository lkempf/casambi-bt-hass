"""Test integration setup and unload."""

from unittest.mock import AsyncMock, patch

from CasambiBt.errors import AuthenticationError, BluetoothError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_bt import async_remove_entry
from custom_components.casambi_bt.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .conftest import TEST_UUID, MockCasambi


async def test_setup_and_unload(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    enable_bluetooth: None,
) -> None:
    """Test a full setup and unload cycle."""
    config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    mock_casambi.connect.assert_awaited_once()
    mock_casambi.registerDisconnectCallback.assert_called_once()
    assert DOMAIN in hass.data
    assert config_entry.entry_id in hass.data[DOMAIN]

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    mock_casambi.disconnect.assert_awaited_once()
    assert config_entry.entry_id not in hass.data[DOMAIN]


async def test_setup_retry_on_bluetooth_error(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    enable_bluetooth: None,
) -> None:
    """A bluetooth error during initial connect must schedule a setup retry."""
    mock_casambi.connect = AsyncMock(side_effect=BluetoothError())

    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_auth_failed(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    enable_bluetooth: None,
) -> None:
    """An authentication error must trigger the reauth flow."""
    mock_casambi.connect = AsyncMock(side_effect=AuthenticationError())

    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == "reauth"


async def test_setup_failure_after_connect_cleans_up(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    enable_bluetooth: None,
) -> None:
    """If platform setup fails the connection must not leak."""
    config_entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        new=AsyncMock(side_effect=Exception("platform boom")),
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    mock_casambi.disconnect.assert_awaited_once()
    assert config_entry.entry_id not in hass.data.get(DOMAIN, {})


async def test_remove_entry_deletes_cache(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    tmp_path,
) -> None:
    """Deleting the config entry must remove the library cache directory."""
    hass.config.config_dir = str(tmp_path)
    cache_dir = tmp_path / ".storage" / DOMAIN / TEST_UUID
    cache_dir.mkdir(parents=True)
    (cache_dir / "network.json").write_text("{}", encoding="utf-8")

    await async_remove_entry(hass, config_entry)

    assert not cache_dir.exists()

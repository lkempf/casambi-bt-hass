"""Test integration setup and unload."""

from unittest.mock import AsyncMock, patch

from CasambiBt import UnitControlType
from CasambiBt.errors import AuthenticationError, BluetoothError, NetworkUpdateError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_bt import async_remove_entry
from custom_components.casambi_bt.const import CONF_IMPORT_GROUPS, DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .conftest import TEST_ADDRESS, TEST_UUID, MockCasambi, make_state, make_unit


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
    assert config_entry.runtime_data.available

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    mock_casambi.disconnect.assert_awaited_once()


@pytest.mark.parametrize(
    "error", [BluetoothError(), NetworkUpdateError(), RuntimeError("boom")]
)
@pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")
async def test_setup_retry_on_connect_error(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
    error: Exception,
) -> None:
    """Errors during the initial connect must schedule a setup retry."""
    mock_casambi.connect = AsyncMock(side_effect=error)

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


async def test_failed_unload_keeps_connection(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    enable_bluetooth: None,
) -> None:
    """A failed platform unload must not tear down the connection."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new=AsyncMock(return_value=False),
    ):
        assert not await hass.config_entries.async_unload(config_entry.entry_id)

    mock_casambi.disconnect.assert_not_awaited()
    assert mock_casambi.connected


async def test_network_device_registered_before_platforms(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    mock_bluetooth: AsyncMock,
    config_entry: MockConfigEntry,
    enable_bluetooth: None,
) -> None:
    """Unit entities must be added even if the light platform loads first.

    Unit devices point to the network device via via_device_id. The lookup
    used to fail when the light platform was set up before the binary
    sensor platform which created the network device.
    """
    unit = make_unit(1, [UnitControlType.DIMMER], state=make_state(dimmer=10))
    mock_casambi.units = [unit]
    config_entry.add_to_hass(hass)

    with patch("custom_components.casambi_bt.PLATFORMS", [Platform.LIGHT]):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    network = device_registry.async_get_device_by_identifier(
        (DOMAIN, TEST_UUID), config_entry.entry_id
    )
    unit_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, unit.uuid), config_entry.entry_id
    )
    assert network is not None
    assert unit_device is not None
    assert unit_device.via_device_id == network.id
    assert len(hass.states.async_entity_ids("light")) == 1


@pytest.mark.parametrize(
    ("stored", "expected"), [({"import_groups": False}, False), ({}, True)]
)
@pytest.mark.usefixtures("mock_bluetooth", "mock_casambi", "enable_bluetooth")
async def test_migrate_entry_v1(
    hass: HomeAssistant, stored: dict[str, bool], expected: bool
) -> None:
    """Version 1 entries must move the group import setting to the options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Network",
        data={CONF_ADDRESS: TEST_ADDRESS, CONF_PASSWORD: "secret", **stored},
        unique_id=TEST_ADDRESS,
        version=1,
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 2
    assert entry.data == {CONF_ADDRESS: TEST_ADDRESS, CONF_PASSWORD: "secret"}
    assert entry.options == {CONF_IMPORT_GROUPS: expected}


@pytest.mark.usefixtures("mock_bluetooth", "mock_casambi", "enable_bluetooth")
async def test_migrate_entry_from_future_version(hass: HomeAssistant) -> None:
    """Entries of a future version must not be set up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: TEST_ADDRESS, CONF_PASSWORD: "secret"},
        unique_id=TEST_ADDRESS,
        version=3,
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.MIGRATION_ERROR

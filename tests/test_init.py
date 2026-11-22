"""Test integration setup and unload."""

import importlib
from unittest.mock import AsyncMock, patch

from CasambiBt import UnitControlType
from CasambiBt.errors import AuthenticationError, BluetoothError, NetworkUpdateError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_bt import (
    async_remove_config_entry_device,
    async_remove_entry,
    cache_uuid,
    is_rate_limited,
    login_status,
)
from custom_components.casambi_bt.const import CONF_IMPORT_GROUPS, DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir

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
    # The translated reason is shown on the integration page.
    assert config_entry.reason is not None
    assert "AA:BB:CC:DD:EE:FF" in config_entry.reason


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


@pytest.mark.parametrize("platform", ["binary_sensor", "light", "number", "scene"])
def test_parallel_updates_declared(platform: str) -> None:
    """Every entity platform must declare its parallel updates."""
    module = importlib.import_module(f"custom_components.casambi_bt.{platform}")
    assert module.PARALLEL_UPDATES == 0


@pytest.mark.usefixtures("mock_casambi", "enable_bluetooth")
async def test_setup_retry_network_not_found(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A network that is not in range must schedule a setup retry."""
    config_entry.add_to_hass(hass)
    with patch(
        "homeassistant.components.bluetooth.async_ble_device_from_address",
        return_value=None,
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert "was not found" in config_entry.reason


@pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")
async def test_no_connectable_scanner_creates_issue(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Without a connectable scanner a repair issue must guide the user."""
    config_entry.add_to_hass(hass)
    issue_id = f"no_connectable_scanner_{config_entry.entry_id}"
    issue_registry = ir.async_get(hass)

    with patch(
        "homeassistant.components.bluetooth.async_scanner_count", return_value=0
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_casambi.connect.assert_not_awaited()
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "no_connectable_scanner"
    assert issue.translation_placeholders == {"address": TEST_ADDRESS}

    # Once a scanner is available the setup succeeds and the issue is gone.
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_remove_entry_deletes_issue(
    hass: HomeAssistant, config_entry: MockConfigEntry, tmp_path
) -> None:
    """Deleting the entry must remove its repair issue."""
    hass.config.config_dir = str(tmp_path)
    issue_id = f"no_connectable_scanner_{config_entry.entry_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="no_connectable_scanner",
    )

    await async_remove_entry(hass, config_entry)

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")
async def test_stale_devices_removed_on_setup(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Devices of units deleted from the network must be removed."""
    config_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    stale = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, "gone")}
    )
    unit = make_unit(1, [UnitControlType.DIMMER])
    mock_casambi.units = [unit]

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert device_registry.async_get(stale.id) is None
    assert device_registry.async_get_device_by_identifier(
        (DOMAIN, unit.uuid), config_entry.entry_id
    )
    assert device_registry.async_get_device_by_identifier(
        (DOMAIN, TEST_UUID), config_entry.entry_id
    )


@pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")
async def test_stale_devices_kept_without_units(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """An empty unit list must not wipe the devices."""
    config_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, "unit")}
    )

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert device_registry.async_get(device.id) is not None


@pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")
async def test_remove_config_entry_device(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Only devices that are no longer in the network may be removed."""
    unit = make_unit(1, [UnitControlType.DIMMER])
    mock_casambi.units = [unit]
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    live = device_registry.async_get_device_by_identifier(
        (DOMAIN, unit.uuid), config_entry.entry_id
    )
    assert not await async_remove_config_entry_device(hass, config_entry, live)

    # The unit is removed in the app while Home Assistant is running.
    mock_casambi.units = []
    assert await async_remove_config_entry_device(hass, config_entry, live)


@pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")
async def test_setup_rate_limited_retries(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """A cloud rate limit must retry the setup instead of starting reauth."""
    mock_casambi.connect = AsyncMock(
        side_effect=AuthenticationError("Login failed: 403\nToo many sessions")
    )
    config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress()


@pytest.mark.parametrize(
    ("message", "status", "limited"),
    [
        ("Login failed: 401\nwrong", 401, False),
        ("Login failed: 403\nlater", 403, True),
        ("Login failed: 429", 429, True),
        ("Not authenticated!", None, False),
    ],
)
def test_login_status(message: str, status: int | None, limited: bool) -> None:
    """The login status is parsed from the library error message."""
    err = AuthenticationError(message)
    assert login_status(err) == status
    assert is_rate_limited(err) is limited


@pytest.mark.parametrize(
    ("address", "uuid"),
    [
        ("AA:BB:CC:DD:EE:FF", "aabbccddeeff"),
        ("aa:bb:cc:dd:ee:ff", "aabbccddeeff"),
        ("../../../etc/x", None),
        ("AA:BB:CC:DD:EE", None),
        ("", None),
    ],
)
def test_cache_uuid(address: str, uuid: str | None) -> None:
    """Only mac addresses may be turned into cache paths."""
    if uuid is None:
        with pytest.raises(ValueError):
            cache_uuid(address)
    else:
        assert cache_uuid(address) == uuid


async def test_remove_entry_with_invalid_address_keeps_files(
    hass: HomeAssistant, tmp_path
) -> None:
    """A malformed stored address must never lead to deleting a path."""
    hass.config.config_dir = str(tmp_path)
    victim = tmp_path / "victim"
    victim.mkdir()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: "../../victim", CONF_PASSWORD: "secret"},
        version=2,
    )

    await async_remove_entry(hass, entry)

    assert victim.exists()


@pytest.mark.usefixtures("mock_bluetooth", "mock_casambi", "enable_bluetooth")
async def test_setup_with_invalid_address_fails(hass: HomeAssistant) -> None:
    """An entry with a malformed address must fail instead of using it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: "../../victim", CONF_PASSWORD: "secret"},
        version=2,
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR


@pytest.mark.usefixtures("mock_bluetooth", "mock_casambi", "enable_bluetooth")
async def test_failed_cache_migration_does_not_block_setup(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A cache that can't be migrated must not prevent the setup."""
    config_entry.add_to_hass(hass)

    with patch(
        "custom_components.casambi_bt.migrate_legacy_cache",
        side_effect=PermissionError("read only"),
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED


@pytest.mark.usefixtures("mock_bluetooth", "mock_casambi", "enable_bluetooth")
async def test_setup_restricts_cache_permissions(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The setup must restrict the cache and survive if that fails."""
    config_entry.add_to_hass(hass)

    with patch(
        "custom_components.casambi_bt.restrict_cache_permissions",
        side_effect=PermissionError("not allowed"),
    ) as restrict:
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    restrict.assert_called_once()
    assert restrict.call_args.args[1] == TEST_UUID
    assert config_entry.state is ConfigEntryState.LOADED

"""Tests for the config flow."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from CasambiBt.errors import (
    AuthenticationError,
    BluetoothError,
    CloudUnavailableError,
    NetworkOnlineUpdateNeededError,
    OfflineBackupError,
)
from habluetooth import BluetoothServiceInfoBleak
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_bt.config_flow import _validate_input
from custom_components.casambi_bt.const import (
    CONF_IMPORT_GROUPS,
    CONF_OFFLINE_MODE,
    DOMAIN,
)
from homeassistant.config_entries import (
    SOURCE_BLUETOOTH,
    SOURCE_USER,
    ConfigEntryState,
    ConfigFlowResult,
)
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import TEST_ADDRESS, TEST_UUID, MockCasambi

pytestmark = pytest.mark.usefixtures("enable_bluetooth")

USER_INPUT = {
    CONF_ADDRESS: "aa:bb:cc:dd:ee:ff",
    CONF_PASSWORD: "secret-password",
    CONF_IMPORT_GROUPS: True,
}


def _discovery_info(*, connectable: bool = True) -> BluetoothServiceInfoBleak:
    device = MagicMock()
    device.address = TEST_ADDRESS
    return BluetoothServiceInfoBleak(
        name="Casambi",
        address=TEST_ADDRESS,
        rssi=-60,
        manufacturer_data={963: b"\x00"},
        service_data={},
        service_uuids=["0000fe4d-0000-1000-8000-00805f9b34fb"],
        source="local",
        device=device,
        advertisement=None,
        connectable=connectable,
        time=0,
        tx_power=None,
    )


async def _start_cloud_flow(hass: HomeAssistant) -> ConfigFlowResult:
    """Start a user flow and choose the setup through the cloud."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["cloud", "backup"]
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud"}
    )


@pytest.fixture
def mock_setup_entry() -> AsyncMock:
    """Prevent flows that create an entry from setting it up."""
    with patch(
        "custom_components.casambi_bt.async_setup_entry", return_value=True
    ) as setup:
        yield setup


@pytest.mark.usefixtures("mock_bluetooth")
async def test_validate_input_reuses_cached_session(
    hass: HomeAssistant, mock_casambi: MockCasambi
) -> None:
    """A working cached session must be used without touching the cache."""
    title = await _validate_input(hass, TEST_ADDRESS, "secret-password")

    assert title == "Test Network"
    mock_casambi.connect.assert_awaited_once()
    mock_casambi.invalidateCache.assert_not_awaited()
    mock_casambi.disconnect.assert_awaited_once()


@pytest.mark.usefixtures("mock_bluetooth")
async def test_validate_input_falls_back_to_cloud_login(
    hass: HomeAssistant, mock_casambi: MockCasambi
) -> None:
    """A rejected cached session must trigger a fresh cloud login.

    The cache is invalidated with the normalized uuid (mac without colons
    in lower case) which is how the library names its cache directory.
    """
    mock_casambi.connect = AsyncMock(
        side_effect=[AuthenticationError("Login failed: 410", status_code=410), None]
    )

    title = await _validate_input(hass, TEST_ADDRESS, "secret-password")

    assert title == "Test Network"
    assert mock_casambi.connect.await_count == 2
    mock_casambi.invalidateCache.assert_awaited_once_with(TEST_UUID)


@pytest.mark.usefixtures("mock_bluetooth", "mock_casambi", "mock_setup_entry")
async def test_user_flow(hass: HomeAssistant) -> None:
    """A valid user input must create an entry with a normalized address."""
    result = await _start_cloud_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "cloud"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Network"
    assert result["data"] == {
        CONF_ADDRESS: TEST_ADDRESS,
        CONF_PASSWORD: "secret-password",
    }
    assert result["options"] == {CONF_IMPORT_GROUPS: True, CONF_OFFLINE_MODE: False}
    assert result["result"].unique_id == TEST_ADDRESS


@pytest.mark.usefixtures("mock_bluetooth", "mock_casambi")
async def test_user_flow_invalid_address(hass: HomeAssistant) -> None:
    """A malformed address must be reported on the address field."""
    result = await _start_cloud_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_ADDRESS: "not-a-mac"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ADDRESS: "invalid_address"}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthenticationError("Login failed: 401", status_code=401), "invalid_auth"),
        (CloudUnavailableError("Login failed: 403", 403), "rate_limited"),
        (CloudUnavailableError("Login failed: 503", 503), "cloud_unreachable"),
        (AuthenticationError("Not authenticated!"), "cannot_connect"),
        (BluetoothError(), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
@pytest.mark.usefixtures("mock_bluetooth", "mock_setup_entry")
async def test_user_flow_errors_recover(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    error: Exception,
    expected: str,
) -> None:
    """Connection errors must be shown and the flow must recover."""
    result = await _start_cloud_flow(hass)

    # An authentication error is retried once with a fresh login.
    mock_casambi.connect = AsyncMock(side_effect=[error, error])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    mock_casambi.connect = AsyncMock()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_casambi")
async def test_user_flow_network_not_found(hass: HomeAssistant) -> None:
    """A network that is not in range must be reported as cannot_connect."""
    result = await _start_cloud_flow(hass)
    with patch(
        "custom_components.casambi_bt.config_flow.async_ble_device_from_address",
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.usefixtures("mock_bluetooth")
async def test_user_flow_already_configured(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Adding a configured network again must abort without connecting."""
    config_entry.add_to_hass(hass)

    result = await _start_cloud_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_casambi.connect.assert_not_awaited()


async def test_user_flow_without_bluetooth(hass: HomeAssistant) -> None:
    """Without a connectable scanner the flow must abort."""
    with patch(
        "custom_components.casambi_bt.config_flow.async_scanner_count",
        return_value=0,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "bluetooth_error"


@pytest.mark.usefixtures("mock_bluetooth", "mock_casambi", "mock_setup_entry")
async def test_bluetooth_discovery(hass: HomeAssistant) -> None:
    """A discovered network must only ask for the password."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_discovery_info()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"] == {"name": "Casambi"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PASSWORD: "secret-password", CONF_IMPORT_GROUPS: False},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Network"
    assert result["data"] == {
        CONF_ADDRESS: TEST_ADDRESS,
        CONF_PASSWORD: "secret-password",
    }
    assert result["options"] == {CONF_IMPORT_GROUPS: False, CONF_OFFLINE_MODE: False}
    assert result["result"].unique_id == TEST_ADDRESS


@pytest.mark.usefixtures("mock_bluetooth")
async def test_bluetooth_discovery_wrong_password(
    hass: HomeAssistant, mock_casambi: MockCasambi
) -> None:
    """A wrong password in the discovery flow must be reported."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_discovery_info()
    )
    mock_casambi.connect = AsyncMock(
        side_effect=AuthenticationError("Login failed: 401", status_code=401)
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PASSWORD: "wrong", CONF_IMPORT_GROUPS: True},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_bluetooth_discovery_already_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A discovered network that is configured must abort."""
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_discovery_info()
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_bluetooth_discovery_not_connectable(hass: HomeAssistant) -> None:
    """A device seen only by non connectable scanners must abort."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=_discovery_info(connectable=False),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


@pytest.mark.usefixtures("mock_bluetooth")
async def test_reauth_flow(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Reauth must validate the password, update the entry and reload it."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"] == {"name": "Test Network"}

    # A wrong password keeps the entry running: the forced cloud login of
    # the validation fails, then the entry is set up again with its old data.
    mock_casambi.connect = AsyncMock(
        side_effect=[AuthenticationError("Login failed: 401", status_code=401), None]
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert config_entry.state is ConfigEntryState.LOADED

    mock_casambi.connect = AsyncMock()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new-password"
    assert config_entry.data[CONF_ADDRESS] == TEST_ADDRESS
    assert config_entry.state is ConfigEntryState.LOADED


@pytest.mark.usefixtures("mock_bluetooth")
async def test_options_flow_reloads(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Changing the options must store them and reload the entry."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_casambi.connect.await_count == 1

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_IMPORT_GROUPS: True}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_IMPORT_GROUPS: True, CONF_OFFLINE_MODE: False}
    assert config_entry.state is ConfigEntryState.LOADED
    assert mock_casambi.connect.await_count == 2


@pytest.mark.usefixtures("mock_bluetooth")
async def test_reconfigure_flow(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Reconfigure must validate the new password and reload the entry."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "reconfigure_password"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure_password"

    mock_casambi.connect = AsyncMock(side_effect=[BluetoothError(), None])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.data[CONF_PASSWORD] == "secret-password"

    mock_casambi.connect = AsyncMock()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_PASSWORD] == "new-password"
    assert config_entry.state is ConfigEntryState.LOADED


@pytest.mark.usefixtures("mock_bluetooth")
async def test_reauth_cloud_unreachable(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Without the cloud a new password can't be verified."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reauth_flow(hass)
    mock_casambi.connect = AsyncMock(
        side_effect=[NetworkOnlineUpdateNeededError(), None]
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cloud_unreachable"}
    assert config_entry.data[CONF_PASSWORD] == "secret-password"
    assert config_entry.state is ConfigEntryState.LOADED


@pytest.mark.usefixtures("mock_bluetooth")
async def test_validate_input_disconnects_on_failure(
    hass: HomeAssistant, mock_casambi: MockCasambi
) -> None:
    """The validation must close its connection even when it fails."""
    mock_casambi.connect = AsyncMock(side_effect=BluetoothError())
    mock_casambi.disconnect = AsyncMock(side_effect=RuntimeError("already gone"))

    with pytest.raises(BluetoothError):
        await _validate_input(hass, TEST_ADDRESS, "secret-password")

    mock_casambi.disconnect.assert_awaited_once()


@pytest.mark.usefixtures("mock_bluetooth")
async def test_validate_input_rate_limit_keeps_cache(
    hass: HomeAssistant, mock_casambi: MockCasambi
) -> None:
    """A rate limit must not drop the cache for a retry that can't succeed."""
    mock_casambi.connect = AsyncMock(
        side_effect=AuthenticationError("Login failed: 403")
    )

    with pytest.raises(AuthenticationError):
        await _validate_input(hass, TEST_ADDRESS, "secret-password")

    mock_casambi.connect.assert_awaited_once()
    mock_casambi.invalidateCache.assert_not_awaited()


BACKUP = {
    "format": "casambi-bt-offline-backup",
    "version": 1,
    "address": TEST_ADDRESS,
    "networkId": "net-1",
    "network": {"network": {}},
    "unitTypes": [],
}


async def _start_backup_flow(hass: HomeAssistant) -> ConfigFlowResult:
    """Start a user flow and choose the setup from an offline backup."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "backup"}
    )


@pytest.mark.usefixtures("mock_bluetooth", "mock_setup_entry")
async def test_backup_flow(hass: HomeAssistant, mock_casambi: MockCasambi) -> None:
    """A network is set up from an offline backup without the cloud."""
    result = await _start_backup_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "backup"

    # An invalid backup is reported and the flow recovers.
    mock_casambi.importOfflineBackup = AsyncMock(
        side_effect=[OfflineBackupError("bad"), TEST_ADDRESS]
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"backup": json.dumps(BACKUP), CONF_IMPORT_GROUPS: False}
    )
    assert result["errors"] == {"backup": "invalid_backup"}
    # The pasted backup holds the keys and is never shown again.
    suggested = {
        str(key): (key.description or {}).get("suggested_value")
        for key in result["data_schema"].schema
    }
    assert suggested["backup"] is None
    assert suggested[CONF_IMPORT_GROUPS] is False

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"backup": json.dumps(BACKUP), CONF_IMPORT_GROUPS: False}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Network"
    assert result["data"] == {CONF_ADDRESS: TEST_ADDRESS, CONF_PASSWORD: ""}
    # Restoring a backup defaults to the offline mode.
    assert result["options"] == {CONF_IMPORT_GROUPS: False, CONF_OFFLINE_MODE: True}
    assert result["result"].unique_id == TEST_ADDRESS
    mock_casambi.importOfflineBackup.assert_awaited_with(BACKUP)
    assert mock_casambi.connect.await_args.kwargs["preferCache"] is True


@pytest.mark.parametrize(
    "text",
    ["not json", "[1, 2]", json.dumps({"no": "address"}), json.dumps({"address": "x"})],
)
@pytest.mark.usefixtures("mock_bluetooth", "mock_setup_entry")
async def test_backup_flow_rejects_garbage(
    hass: HomeAssistant, mock_casambi: MockCasambi, text: str
) -> None:
    """Text that isn't a backup is rejected before touching the cache."""
    result = await _start_backup_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"backup": text, CONF_IMPORT_GROUPS: True}
    )
    assert result["errors"] == {"backup": "invalid_backup"}
    mock_casambi.importOfflineBackup.assert_not_awaited()

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"backup": json.dumps(BACKUP), CONF_IMPORT_GROUPS: True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_casambi")
async def test_backup_flow_network_not_in_range(hass: HomeAssistant) -> None:
    """A restored network still has to be reachable."""
    result = await _start_backup_flow(hass)
    with patch(
        "custom_components.casambi_bt.config_flow.async_ble_device_from_address",
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"backup": json.dumps(BACKUP), CONF_IMPORT_GROUPS: True}
        )
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.usefixtures("mock_bluetooth")
async def test_backup_flow_already_configured(
    hass: HomeAssistant, mock_casambi: MockCasambi, config_entry: MockConfigEntry
) -> None:
    """A configured network is restored with reconfigure, not added again."""
    config_entry.add_to_hass(hass)
    result = await _start_backup_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"backup": json.dumps(BACKUP), CONF_IMPORT_GROUPS: True}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_casambi.importOfflineBackup.assert_not_awaited()


async def _start_restore(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> ConfigFlowResult:
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["reconfigure_password", "restore_backup"]
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "restore_backup"}
    )


@pytest.mark.usefixtures("mock_bluetooth")
async def test_restore_backup(
    hass: HomeAssistant, mock_casambi: MockCasambi, config_entry: MockConfigEntry
) -> None:
    """Restoring a backup refills the cache and reloads the entry."""
    result = await _start_restore(hass, config_entry)
    assert result["step_id"] == "restore_backup"

    # A backup of another network is refused.
    other = {**BACKUP, "address": "11:22:33:44:55:66"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"backup": json.dumps(other)}
    )
    assert result["errors"] == {"backup": "wrong_network"}
    mock_casambi.importOfflineBackup.assert_not_awaited()

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"backup": "garbage"}
    )
    assert result["errors"] == {"backup": "invalid_backup"}

    # A failing restore keeps the entry running.
    mock_casambi.importOfflineBackup = AsyncMock(
        side_effect=[OfflineBackupError("bad"), TEST_ADDRESS]
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"backup": json.dumps(BACKUP)}
    )
    assert result["errors"] == {"backup": "invalid_backup"}
    assert config_entry.state is ConfigEntryState.LOADED

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"backup": json.dumps(BACKUP)}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.data[CONF_PASSWORD] == "secret-password"


@pytest.mark.usefixtures("mock_bluetooth", "mock_setup_entry")
async def test_backup_flow_online_needs_password(
    hass: HomeAssistant, mock_casambi: MockCasambi
) -> None:
    """Without the offline mode the cloud must accept the password."""
    result = await _start_backup_flow(hass)
    backup_input = {
        "backup": json.dumps(BACKUP),
        CONF_OFFLINE_MODE: False,
        CONF_IMPORT_GROUPS: True,
    }

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], backup_input
    )
    assert result["errors"] == {CONF_PASSWORD: "password_required"}
    mock_casambi.importOfflineBackup.assert_not_awaited()

    # The backup restores, but the cloud rejects the password.
    mock_casambi.connect = AsyncMock(
        side_effect=[
            None,
            AuthenticationError("Login failed: 401", status_code=401),
            None,
            None,
        ]
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**backup_input, CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**backup_input, CONF_PASSWORD: "secret-password"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PASSWORD] == "secret-password"
    assert result["options"] == {CONF_IMPORT_GROUPS: True, CONF_OFFLINE_MODE: False}
    # The password was checked with a fresh cloud login.
    assert mock_casambi.connect.await_count == 4


@pytest.mark.usefixtures("mock_bluetooth")
async def test_options_offline_mode_needs_password_to_go_online(
    hass: HomeAssistant, mock_casambi: MockCasambi
) -> None:
    """An entry without a password can't leave the offline mode."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Network",
        data={CONF_ADDRESS: TEST_ADDRESS, CONF_PASSWORD: ""},
        options={CONF_IMPORT_GROUPS: True, CONF_OFFLINE_MODE: True},
        unique_id=TEST_ADDRESS,
        version=2,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_IMPORT_GROUPS: True, CONF_OFFLINE_MODE: False}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "password_required"}

    # Other options can still be changed while offline.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_IMPORT_GROUPS: False, CONF_OFFLINE_MODE: True}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_IMPORT_GROUPS: False, CONF_OFFLINE_MODE: True}

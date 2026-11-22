"""Tests for the config flow."""

from unittest.mock import AsyncMock, MagicMock, patch

from CasambiBt.errors import AuthenticationError, BluetoothError
from habluetooth import BluetoothServiceInfoBleak
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_bt.config_flow import _validate_input
from custom_components.casambi_bt.const import CONF_IMPORT_GROUPS, DOMAIN
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER, ConfigEntryState
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
        side_effect=[AuthenticationError("Login failed: 401"), None]
    )

    title = await _validate_input(hass, TEST_ADDRESS, "secret-password")

    assert title == "Test Network"
    assert mock_casambi.connect.await_count == 2
    mock_casambi.invalidateCache.assert_awaited_once_with(TEST_UUID)


@pytest.mark.usefixtures("mock_bluetooth", "mock_casambi", "mock_setup_entry")
async def test_user_flow(hass: HomeAssistant) -> None:
    """A valid user input must create an entry with a normalized address."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
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
    assert result["options"] == {CONF_IMPORT_GROUPS: True}
    assert result["result"].unique_id == TEST_ADDRESS


@pytest.mark.usefixtures("mock_bluetooth", "mock_casambi")
async def test_user_flow_invalid_address(hass: HomeAssistant) -> None:
    """A malformed address must be reported on the address field."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_ADDRESS: "not-a-mac"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ADDRESS: "invalid_address"}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthenticationError("Login failed: 401"), "invalid_auth"),
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
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

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
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
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

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
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
    assert result["options"] == {CONF_IMPORT_GROUPS: False}
    assert result["result"].unique_id == TEST_ADDRESS


@pytest.mark.usefixtures("mock_bluetooth")
async def test_bluetooth_discovery_wrong_password(
    hass: HomeAssistant, mock_casambi: MockCasambi
) -> None:
    """A wrong password in the discovery flow must be reported."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_discovery_info()
    )
    mock_casambi.connect = AsyncMock(side_effect=AuthenticationError())
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

    # A wrong password keeps the entry running: both login attempts of the
    # validation fail, then the entry is set up again with its old data.
    mock_casambi.connect = AsyncMock(
        side_effect=[AuthenticationError(), AuthenticationError(), None]
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
    assert config_entry.options == {CONF_IMPORT_GROUPS: True}
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
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

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

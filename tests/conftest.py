"""Common fixtures for Casambi Bluetooth tests."""

import asyncio
from collections.abc import AsyncGenerator, Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from CasambiBt import Unit, UnitControl, UnitControlType, UnitState, UnitType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_bt.const import DOMAIN
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant

TEST_ADDRESS = "AA:BB:CC:DD:EE:FF"
TEST_UUID = "aabbccddeeff"

# Captured early so tests keep access to the real sleep even while the
# integration's asyncio.sleep is patched.
real_sleep = asyncio.sleep


@pytest.fixture
async def enable_bluetooth(
    hass: HomeAssistant,
    mock_bleak_scanner_start: MagicMock,
    mock_bluetooth_adapters: None,
) -> AsyncGenerator[None]:
    """Set up the bluetooth integration with mocked adapters.

    Mirrors the fixture of pytest-homeassistant-custom-component but
    cancels the scanner's periodic device-expiry timer on teardown. It is
    scheduled directly on the event loop by the compiled habluetooth
    scanner and would otherwise trip HA's lingering-timer check.
    """
    entry = MockConfigEntry(domain="bluetooth", unique_id="00:00:00:00:00:01")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    loop = asyncio.get_running_loop()
    for handle in list(loop._scheduled):  # noqa: SLF001
        callback = getattr(handle, "_callback", None)
        qualname = getattr(callback, "__qualname__", "")
        if qualname.startswith("BaseHaScanner."):
            handle.cancel()


async def drain(hass: HomeAssistant, rounds: int = 25, tick: float = 0.005) -> None:
    """Let background tasks run to completion.

    Tasks created via async_create_background_task with eager_start=False
    only start on the next loop iteration and the reconnect loop suspends
    repeatedly, so a single async_block_till_done is not sufficient.
    """
    for _ in range(rounds):
        await hass.async_block_till_done()
        await real_sleep(tick)


@pytest.fixture(scope="session")
def mock_bluetooth_adapters() -> Generator[None]:
    """Mock bluetooth adapters, including the BlueZ history.

    Mirrors the fixture of pytest-homeassistant-custom-component but also
    patches LinuxAdapters.history which otherwise reaches out to D-Bus.
    """
    with (
        patch("bluetooth_auto_recovery.recover_adapter"),
        patch("bluetooth_adapters.systems.platform.system", return_value="Linux"),
        patch("bluetooth_adapters.systems.linux.LinuxAdapters.refresh"),
        patch("bluetooth_adapters.systems.linux.LinuxAdapters.history", {}),
        patch(
            "bluetooth_adapters.systems.linux.LinuxAdapters.adapters",
            {
                "hci0": {
                    "address": "00:00:00:00:00:01",
                    "hw_version": "usb:v1D6Bp0246d053F",
                    "passive_scan": False,
                    "sw_version": "homeassistant",
                    "manufacturer": "ACME",
                    "product": "Bluetooth Adapter 5.0",
                    "product_id": "aa01",
                    "vendor_id": "cc01",
                },
            },
        ),
    ):
        yield


class MockCasambi:
    """Stand-in for the CasambiBt.Casambi class."""

    def __init__(self) -> None:
        """Initialize with a disconnected, empty network."""
        self.connected = False
        self.units: list = []
        self.groups: list = []
        self.scenes: list = []
        self.networkId = TEST_UUID
        self.networkName = "Test Network"

        self.registerDisconnectCallback = MagicMock()
        self.unregisterDisconnectCallback = MagicMock()
        self.registerUnitChangedHandler = MagicMock()
        self.unregisterUnitChangedHandler = MagicMock()

        self.connect = AsyncMock(side_effect=self._connect)
        self.disconnect = AsyncMock(side_effect=self._disconnect)
        self.reconnect = AsyncMock(side_effect=self._reconnect)
        self.invalidateCache = AsyncMock()

    async def _connect(self, _device, _password, _forceOffline=False) -> None:
        self.connected = True

    async def _disconnect(self) -> None:
        self.connected = False

    async def _reconnect(self, _device) -> None:
        self.connected = True


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading of custom integrations in all tests."""


@pytest.fixture
def mock_casambi() -> Generator[MockCasambi]:
    """Patch the Casambi class used by the integration."""
    instance = MockCasambi()
    with (
        patch("custom_components.casambi_bt.Casambi", return_value=instance),
        patch(
            "custom_components.casambi_bt.config_flow.Casambi",
            return_value=instance,
        ),
    ):
        yield instance


@pytest.fixture
def mock_bluetooth() -> Generator[MagicMock]:
    """Patch the bluetooth helpers used to find and watch the device."""
    device = MagicMock()
    device.address = TEST_ADDRESS
    with (
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address",
            return_value=device,
        ),
        patch(
            "custom_components.casambi_bt.config_flow.async_ble_device_from_address",
            return_value=device,
        ),
        patch(
            "homeassistant.components.bluetooth.async_register_callback",
            return_value=MagicMock(),
        ) as register_callback,
    ):
        register_callback.device = device
        yield register_callback


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Create a mock config entry for the test network."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Network",
        data={
            CONF_ADDRESS: TEST_ADDRESS,
            CONF_PASSWORD: "secret-password",
            "import_groups": False,
        },
        unique_id=TEST_ADDRESS,
    )


def make_unit(
    device_id: int,
    controls: list[UnitControlType],
    *,
    state: UnitState | None = None,
    on: bool = True,
    online: bool = True,
    name: str | None = None,
) -> Unit:
    """Create a library unit with the given control types."""
    unit_type = UnitType(
        id=device_id,
        model="Test Model",
        manufacturer="Test Manufacturer",
        mode="Test Mode",
        stateLength=0,
        controls=[
            UnitControl(
                type=control,
                offset=0,
                length=8,
                default=0,
                readonly=False,
                min=2700 if control is UnitControlType.TEMPERATURE else None,
                max=6500 if control is UnitControlType.TEMPERATURE else None,
            )
            for control in controls
        ],
    )
    return Unit(
        _typeId=device_id,
        deviceId=device_id,
        uuid=f"unit{device_id:08x}",
        address=f"00:00:00:00:00:{device_id:02x}",
        name=name or f"Unit {device_id}",
        firmwareVersion="1.0",
        unitType=unit_type,
        _state=state,
        _on=on,
        _online=online,
    )


def make_state(**values: Any) -> UnitState:
    """Create a library unit state with the given values."""
    state = UnitState()
    for key, value in values.items():
        setattr(state, key, value)
    return state


@pytest.fixture
def group_config_entry(config_entry: MockConfigEntry) -> MockConfigEntry:
    """Create a mock config entry that imports groups."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=config_entry.title,
        data={**config_entry.data, "import_groups": True},
        unique_id=config_entry.unique_id,
    )

"""Contract tests between the integration and the real casambi-bt library.

The integration runs against the real library. Only the Casambi cloud
(http transport) and the bluetooth connection are replaced, so the cache,
the offline handling, the error types and the backup format are exercised
as in production.
"""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
import json
from typing import Any
from unittest.mock import AsyncMock, patch

from bleak.backends.device import BLEDevice
from CasambiBt import Casambi
import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_bt.const import (
    CONF_IMPORT_GROUPS,
    CONF_OFFLINE_MODE,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import TEST_ADDRESS, TEST_UUID, drain

NETWORK_ID = "net-1"
PASSWORD = "right-password"
FIXTURE = {
    "id": 42,
    "model": "Spot",
    "vendor": "Acme",
    "mode": "Dim",
    "stateLength": 1,
    "controls": [
        {"type": "dimmer", "offset": 0, "length": 8, "default": 0, "readonly": False}
    ],
}


def _network(revision: int, units: int) -> dict[str, Any]:
    return {
        "status": "OK",
        "network": {
            "name": "Home",
            "revision": revision,
            "protocolVersion": 10,
            "keyStore": {
                "keys": [{"id": 1, "type": 1, "role": 3, "name": "k", "key": "00" * 16}]
            },
            "units": [
                {
                    "type": 42,
                    "deviceID": i,
                    "uuid": f"unit-{i}",
                    "address": "a",
                    "name": f"Lamp {i}",
                    "firmware": 1,
                }
                for i in range(1, units + 1)
            ],
            "grid": {"cells": []},
            "scenes": [],
        },
    }


class FakeCloud:
    """The Casambi cloud as far as casambi-bt uses it."""

    def __init__(self) -> None:
        """Start online with a network of one unit."""
        self.offline = False
        self.session_status = 200
        self.network = _network(revision=1, units=1)
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Answer a request of the library."""
        self.requests.append(request.url.path)
        if self.offline:
            raise httpx.ConnectError("offline", request=request)
        path = request.url.path
        if path == f"/network/uuid/{TEST_UUID}":
            return httpx.Response(200, json={"id": NETWORK_ID})
        if path == f"/network/{NETWORK_ID}/session":
            if self.session_status != 200:
                return httpx.Response(self.session_status, text="error")
            if json.loads(request.content)["password"] != PASSWORD:
                return httpx.Response(401, text="wrong password")
            expires = (datetime.now(UTC) + timedelta(days=7)).timestamp() * 1000
            return httpx.Response(
                200,
                json={
                    "session": "session",
                    "network": NETWORK_ID,
                    "manager": True,
                    "keyID": 1,
                    "expires": expires,
                },
            )
        if path == f"/network/{NETWORK_ID}/":
            revision = json.loads(request.content)["revision"]
            if revision == self.network["network"]["revision"]:
                return httpx.Response(200, json={"status": "UPTODATE"})
            return httpx.Response(200, json=self.network)
        if path == "/fixture/42":
            return httpx.Response(200, json=FIXTURE)
        return httpx.Response(404)


@pytest.fixture
def cloud() -> Generator[FakeCloud]:
    """Route the library to a fake cloud and replace the bluetooth link."""
    fake = FakeCloud()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    device = BLEDevice(TEST_ADDRESS, "Casambi", None)
    with (
        patch("custom_components.casambi_bt.get_async_client", return_value=client),
        patch(
            "custom_components.casambi_bt.config_flow.get_async_client",
            return_value=client,
        ),
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
            return_value=lambda: None,
        ),
        patch.object(Casambi, "_connectClient", AsyncMock()),
    ):
        yield fake


@pytest.fixture
def entry(hass: HomeAssistant, tmp_path) -> MockConfigEntry:
    """A config entry with the cache in a temporary config dir."""
    hass.config.config_dir = str(tmp_path)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        data={CONF_ADDRESS: TEST_ADDRESS, CONF_PASSWORD: PASSWORD},
        options={CONF_IMPORT_GROUPS: True, CONF_OFFLINE_MODE: False},
        unique_id=TEST_ADDRESS,
        version=2,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await drain(hass)


def _unit_devices(hass: HomeAssistant, entry: MockConfigEntry) -> list[str]:
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

    return sorted(
        d.name or ""
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if d.model != "Network"
    )


pytestmark = pytest.mark.usefixtures("enable_bluetooth")


async def test_restart_without_cloud(
    hass: HomeAssistant, cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    """After one online start the network starts without the cloud."""
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert _unit_devices(hass, entry) == ["Lamp 1"]

    await hass.config_entries.async_unload(entry.entry_id)
    cloud.offline = True
    cloud.requests.clear()

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert _unit_devices(hass, entry) == ["Lamp 1"]
    # Only the background refresh tried the cloud.
    assert not hass.config_entries.flow.async_progress()


async def test_cloud_outage_does_not_ask_for_the_password(
    hass: HomeAssistant, cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    """A cloud answering 503 must not start reauth (it used to)."""
    cloud.session_status = 503

    await _setup(hass, entry)

    # Without a cache there is nothing to start from yet.
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress()


async def test_rejected_password_asks_for_the_password(
    hass: HomeAssistant, cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    """A wrong password is reported as such and starts reauth."""
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_PASSWORD: "wrong"}
    )

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_new_units_are_added_by_the_refresh(
    hass: HomeAssistant, cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    """A unit added in the app appears after the next refresh."""
    await _setup(hass, entry)
    assert _unit_devices(hass, entry) == ["Lamp 1"]

    cloud.network = _network(revision=2, units=2)
    await entry.runtime_data.async_refresh_network()
    await drain(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert _unit_devices(hass, entry) == ["Lamp 1", "Lamp 2"]


async def test_offline_backup_roundtrip(
    hass: HomeAssistant, cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    """A network is set up again from a backup without any cloud access."""
    await _setup(hass, entry)
    backup = await hass.services.async_call(
        DOMAIN,
        "export_offline_backup",
        {"config_entry_id": entry.entry_id},
        blocking=True,
        return_response=True,
    )
    assert backup["network"]["network"]["keyStore"]["keys"]

    # Removing the entry deletes the cache; the cloud is gone for good.
    await hass.config_entries.async_remove(entry.entry_id)
    await drain(hass)
    cloud.offline = True
    cloud.requests.clear()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "backup"}
    )
    # The backup survives the trip through a password manager as text.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"backup": json.dumps(backup, indent=2), CONF_IMPORT_GROUPS: True},
    )
    await drain(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home"
    restored = result["result"]
    assert restored.state is ConfigEntryState.LOADED
    assert _unit_devices(hass, restored) == ["Lamp 1"]
    # Restoring a backup defaults to the offline mode: no cloud access at all.
    assert cloud.requests == []


async def test_offline_mode_never_contacts_the_cloud(
    hass: HomeAssistant, cloud: FakeCloud, entry: MockConfigEntry
) -> None:
    """In offline mode the cloud isn't used even when it is reachable."""
    await _setup(hass, entry)
    await hass.config_entries.async_unload(entry.entry_id)

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_OFFLINE_MODE: True}
    )
    cloud.requests.clear()
    cloud.network = _network(revision=2, units=2)

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert cloud.requests == []
    assert _unit_devices(hass, entry) == ["Lamp 1"]

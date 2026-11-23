"""Test password verification against the real casambi-bt login code.

Only the http transport and the bluetooth connection are replaced, so the
session handling of the library is exercised as in production.
"""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
import json
import pickle
from unittest.mock import AsyncMock, patch

from bleak.backends.device import BLEDevice
from CasambiBt._cache import CACHE_VERSION
from CasambiBt._casambi import Casambi
from CasambiBt._network import SESSION_CACHE_FILE, Network, _NetworkSession
from CasambiBt.errors import AuthenticationError, NetworkOnlineUpdateNeededError
import httpx
import pytest

from custom_components.casambi_bt import get_cache_dir
from custom_components.casambi_bt.config_flow import _validate_input
from homeassistant.core import HomeAssistant

from .conftest import TEST_ADDRESS, TEST_UUID

NETWORK_ID = "test-network-id"
PASSWORD = "right-password"


def _cloud(request: httpx.Request) -> httpx.Response:
    """Answer like the Casambi cloud for the requests of a login."""
    if request.url.path == f"/network/uuid/{TEST_UUID}":
        return httpx.Response(200, json={"id": NETWORK_ID})
    if request.url.path == f"/network/{NETWORK_ID}/session":
        if json.loads(request.content)["password"] != PASSWORD:
            return httpx.Response(401, text="Wrong password")
        expires = datetime.now(UTC) + timedelta(days=7)
        return httpx.Response(
            200,
            json={
                "session": "new-session",
                "network": NETWORK_ID,
                "manager": True,
                "keyID": 1,
                "expires": expires.timestamp() * 1000,
            },
        )
    return httpx.Response(404)


@pytest.fixture
def cloud() -> Generator[list[httpx.Request]]:
    """Route the library's http requests to a fake Casambi cloud."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _cloud(request)

    async def _update(self: Network, forceOffline: bool = False) -> None:
        self._networkName = "Test Network"
        self._networkRevision = 1

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with (
        patch(
            "custom_components.casambi_bt.config_flow.get_async_client",
            return_value=client,
        ),
        patch("custom_components.casambi_bt.config_flow.Casambi", Casambi),
        patch.object(Network, "update", _update),
        patch.object(Casambi, "_connectClient", AsyncMock()),
        patch(
            "custom_components.casambi_bt.config_flow.async_ble_device_from_address",
            return_value=BLEDevice(TEST_ADDRESS, "Casambi", None),
        ),
    ):
        yield requests


def _write_valid_session(hass: HomeAssistant) -> None:
    """Store a valid session like a previous successful login did."""
    cache_root = get_cache_dir(hass)
    entry_dir = cache_root / TEST_UUID
    entry_dir.mkdir(parents=True)
    (cache_root / ".cachever").write_text(str(CACHE_VERSION))
    (entry_dir / "networkid").write_text(NETWORK_ID)
    session = _NetworkSession(
        session="cached-session",
        network=NETWORK_ID,
        manager=True,
        keyID=1,
        expires=datetime.now(UTC) + timedelta(days=7),
    )
    (entry_dir / SESSION_CACHE_FILE).write_bytes(pickle.dumps(session))


async def test_cached_session_skips_password_check(
    hass: HomeAssistant, cloud: list[httpx.Request], tmp_path
) -> None:
    """Without verification a valid cached session accepts any password.

    This is the library behaviour that makes the verification necessary.
    """
    hass.config.config_dir = str(tmp_path)
    _write_valid_session(hass)

    title = await _validate_input(hass, TEST_ADDRESS, "wrong-password")

    assert title == "Test Network"
    assert not any(r.url.path.endswith("/session") for r in cloud)


async def test_verification_rejects_wrong_password(
    hass: HomeAssistant, cloud: list[httpx.Request], tmp_path
) -> None:
    """A wrong password must be rejected despite a valid cached session."""
    hass.config.config_dir = str(tmp_path)
    _write_valid_session(hass)
    session_file = get_cache_dir(hass) / TEST_UUID / SESSION_CACHE_FILE
    cached = session_file.read_bytes()

    with pytest.raises(AuthenticationError):
        await _validate_input(
            hass, TEST_ADDRESS, "wrong-password", verify_password=True
        )

    title = await _validate_input(hass, TEST_ADDRESS, PASSWORD, verify_password=True)

    assert title == "Test Network"
    # The cache of the entry is left untouched.
    assert session_file.read_bytes() == cached


async def test_verification_without_cloud(
    hass: HomeAssistant, cloud: list[httpx.Request], tmp_path
) -> None:
    """Without the cloud the verification must fail instead of going offline."""
    hass.config.config_dir = str(tmp_path)
    _write_valid_session(hass)

    def _offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_offline))
    with (
        patch(
            "custom_components.casambi_bt.config_flow.get_async_client",
            return_value=client,
        ),
        pytest.raises(NetworkOnlineUpdateNeededError),
    ):
        await _validate_input(hass, TEST_ADDRESS, PASSWORD, verify_password=True)


async def test_verification_removes_temporary_cache(
    hass: HomeAssistant, cloud: list[httpx.Request], tmp_path
) -> None:
    """The temporary cache with the new session must be deleted."""
    hass.config.config_dir = str(tmp_path / "config")
    temp_root = tmp_path / "verify"
    temp_root.mkdir()

    with patch(
        "custom_components.casambi_bt.config_flow.tempfile.mkdtemp",
        return_value=str(temp_root),
    ):
        await _validate_input(hass, TEST_ADDRESS, PASSWORD, verify_password=True)

    assert not temp_root.exists()

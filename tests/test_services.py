"""Test the actions of the integration."""

from CasambiBt._constants import ConnectionState
from CasambiBt.errors import ConnectionStateError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casambi_bt.const import DOMAIN
from homeassistant.auth.models import User
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
)

from .conftest import MockCasambi

pytestmark = pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")


async def _setup(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_export_offline_backup(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """The action returns the backup of the library."""
    backup = {"format": "casambi-bt-offline-backup", "version": 1}
    mock_casambi.exportOfflineBackup.return_value = backup
    await _setup(hass, config_entry)

    response = await hass.services.async_call(
        DOMAIN,
        "export_offline_backup",
        {"config_entry_id": config_entry.entry_id},
        blocking=True,
        return_response=True,
    )

    assert response == backup


async def test_export_requires_admin(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
    hass_read_only_user: User,
) -> None:
    """The backup contains the keys, so only admins may export it."""
    await _setup(hass, config_entry)

    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            "export_offline_backup",
            {"config_entry_id": config_entry.entry_id},
            blocking=True,
            return_response=True,
            context=Context(user_id=hass_read_only_user.id),
        )
    mock_casambi.exportOfflineBackup.assert_not_awaited()


async def test_export_unloaded_entry(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """An entry that isn't loaded has nothing to export."""
    await _setup(hass, config_entry)
    await hass.config_entries.async_unload(config_entry.entry_id)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "export_offline_backup",
            {"config_entry_id": config_entry.entry_id},
            blocking=True,
            return_response=True,
        )


async def test_export_failure(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """A library error fails the action with a translated error."""
    mock_casambi.exportOfflineBackup.side_effect = ConnectionStateError(
        ConnectionState.AUTHENTICATED, ConnectionState.NONE
    )
    await _setup(hass, config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "export_offline_backup",
            {"config_entry_id": config_entry.entry_id},
            blocking=True,
            return_response=True,
        )
    assert err.value.translation_key == "backup_failed"

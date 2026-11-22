"""Test the scene platform."""

from unittest.mock import AsyncMock

from CasambiBt import Scene
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import MockCasambi

pytestmark = pytest.mark.usefixtures("mock_bluetooth", "enable_bluetooth")


async def test_activate_scene(
    hass: HomeAssistant,
    mock_casambi: MockCasambi,
    config_entry: MockConfigEntry,
) -> None:
    """Activating a scene must switch to it at full level."""
    scene = Scene(sceneId=4, name="Evening")
    mock_casambi.scenes = [scene]
    mock_casambi.switchToScene = AsyncMock()

    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = er.async_get(hass).async_get_entity_id(
        "scene", "casambi_bt", f"{mock_casambi.networkId}-scene-4"
    )
    assert entity_id == "scene.test_network_evening"

    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": entity_id}, blocking=True
    )

    mock_casambi.switchToScene.assert_awaited_once_with(scene, 0xFF)

"""Constants for the Casambi Bluetooth integration."""

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "casambi_bt"

PLATFORMS = [Platform.BINARY_SENSOR, Platform.LIGHT, Platform.SCENE, Platform.NUMBER]

CONF_IMPORT_GROUPS: Final = "import_groups"

RECONNECT_BACKOFF_START: Final = 2
RECONNECT_BACKOFF_STEP: Final = 2
RECONNECT_BACKOFF_MAX: Final = 300

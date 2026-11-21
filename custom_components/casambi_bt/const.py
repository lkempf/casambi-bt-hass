"""Constants for the Casambi Bluetooth integration."""

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "casambi_bt"

PLATFORMS = [Platform.BINARY_SENSOR, Platform.LIGHT, Platform.SCENE, Platform.NUMBER]

CONF_IMPORT_GROUPS: Final = "import_groups"

RECONNECT_BACKOFF_START: Final = 2
RECONNECT_BACKOFF_STEP: Final = 2
RECONNECT_BACKOFF_MAX: Final = 300

# Maximum duration of a single connect/reconnect attempt before it is
# aborted and retried. Generous because establishing a connection through
# bluetooth proxies can legitimately take a while.
CONNECT_TIMEOUT: Final = 90

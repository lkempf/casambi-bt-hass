"""Constants for the Casambi Bluetooth integration."""

from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "casambi_bt"

PLATFORMS = [Platform.BINARY_SENSOR, Platform.LIGHT, Platform.SCENE, Platform.NUMBER]

CONF_IMPORT_GROUPS: Final = "import_groups"
CONF_BACKUP: Final = "backup"

RECONNECT_BACKOFF_START: Final = 2
RECONNECT_BACKOFF_STEP: Final = 2
RECONNECT_BACKOFF_MAX: Final = 300

# Maximum duration of a single connect/reconnect attempt before it is
# aborted and retried. Generous because establishing a connection through
# bluetooth proxies can legitimately take a while.
CONNECT_TIMEOUT: Final = 90

# Upper bound for a command, including the retries and the single reconnect
# casambi-bt attempts on a broken connection.
COMMAND_TIMEOUT: Final = 45

# How often the Casambi cloud is checked for a changed network configuration.
# The network runs from the cached configuration in between.
NETWORK_REFRESH_INTERVAL: Final = timedelta(hours=24)

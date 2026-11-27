"""Constants for the Ergon Energy Outages integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ergon_outages"

# Esri-hosted view that backs Ergon's public Outage Finder map. It is not a
# documented API, so the integration treats every field defensively.
QUERY_URL: Final = (
    "https://services.arcgis.com/33eHbTVqo7gtiCE8/arcgis/rest/services/"
    "VwErgonOutages/FeatureServer/0/query"
)
OUT_FIELDS: Final = (
    "EVENT_ID,TYPE,STATUS,CUSTOMERS_AFFECTED,SUBURBS,STREETS,"
    "START,FINISH,EST_FIX_TIME,REASON"
)
REQUEST_TIMEOUT: Final = 30  # seconds

CONF_LOCATION: Final = "location"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_SHOW_FEEDERS: Final = "show_feeders"

DEFAULT_NAME: Final = "Home"
DEFAULT_RADIUS_M: Final = 250
DEFAULT_SCAN_INTERVAL_MIN: Final = 5
MIN_SCAN_INTERVAL_MIN: Final = 2
MAX_SCAN_INTERVAL_MIN: Final = 60
MAX_RADIUS_M: Final = 50_000

STORAGE_VERSION: Final = 1

# Unique-ID prefixes of the per-outage entities ("<entry_id>_<prefix><EVENT_ID>").
OUTAGE_SENSOR_KEY_PREFIX: Final = "outage_"
OUTAGE_MAP_KEY_PREFIX: Final = "outage_map_"

# Bus events fired by the coordinator. Automations trigger on these.
EVENT_NEW_OUTAGE: Final = f"{DOMAIN}_new_outage"
EVENT_OUTAGE_STARTED: Final = f"{DOMAIN}_outage_started"
EVENT_OUTAGE_UPDATED: Final = f"{DOMAIN}_outage_updated"
EVENT_OUTAGE_CLEARED: Final = f"{DOMAIN}_outage_cleared"

ATTRIBUTION: Final = "Data provided by Ergon Energy"

# The OpenStreetMap tile policy requires an identifying User-Agent.
TILE_USER_AGENT: Final = (
    "HomeAssistant-ErgonOutages/0.2 (+https://github.com/LONGBOW7757/ha-ergon-outages)"
)


def storage_key(entry_id: str) -> str:
    """Return the storage key used to remember already-announced outages."""
    return f"{DOMAIN}.{entry_id}"

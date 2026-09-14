"""Constants for the Zyxel Switch integration."""

from __future__ import annotations

DOMAIN = "zyxel_switch"

CONF_COMMUNITY = "community"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_PORT = 161
DEFAULT_COMMUNITY = "public"
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 600

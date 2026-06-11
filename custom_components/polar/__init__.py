"""The Polar integration.

History-only design: Polar devices (Loop, watches, ...) sync to Polar Flow in
batches and the data is essentially daily-granular, so there is no meaningful
"current" value to poll. Instead of live sensors, this integration imports the
available Polar history as long-term statistics and keeps it up to date on a
slow cadence (default every 6 hours).
"""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_USER_ID, DEFAULT_SCAN_INTERVAL, DOMAIN
from .polaraccesslink.accesslink import AccessLink
from .statistics import async_import_history

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Polar from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # migrate unique_id to str to fix invalid unique_id
    if entry.unique_id != str(entry.data[CONF_USER_ID]):
        hass.config_entries.async_update_entry(
            entry, unique_id=str(entry.data[CONF_USER_ID])
        )

    accesslink = AccessLink(
        client_id=entry.data[CONF_CLIENT_ID],
        client_secret=entry.data[CONF_CLIENT_SECRET],
    )
    hass.data[DOMAIN][entry.entry_id] = accesslink

    async def _sync(now=None) -> None:
        """Import any new Polar history (best effort, never fails setup)."""
        try:
            await async_import_history(hass, accesslink, entry)
        except Exception:  # noqa: BLE001 - history sync must never break setup
            _LOGGER.exception("Polar: history sync failed")

    interval = timedelta(
        minutes=entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
    )

    # Run once now (in the background) and then on the configured cadence.
    entry.async_create_background_task(hass, _sync(), "polar_history_sync")
    entry.async_on_unload(async_track_time_interval(hass, _sync, interval))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True

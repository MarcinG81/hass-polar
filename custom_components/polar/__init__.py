"""The Polar integration.

History-first design: Polar devices (Loop, watches, ...) sync to Polar Flow in
batches and the data is essentially daily-granular, so there is no meaningful
"current" value to poll. The integration publishes the available Polar history
as long-term statistics (see statistics.py) and keeps it up to date on a slow
cadence (default every 6 hours).

The only live entity is a diagnostic ``Max heart rate`` sensor (from the user's
physical info), which also exposes the derived training zones so charts can draw
zone bands.
"""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_USER_ID, DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import PolarProfileCoordinator
from .statistics import async_import_history

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Polar from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # migrate unique_id to str to fix invalid unique_id
    if entry.unique_id != str(entry.data[CONF_USER_ID]):
        hass.config_entries.async_update_entry(
            entry, unique_id=str(entry.data[CONF_USER_ID])
        )

    coordinator = PolarProfileCoordinator(hass, entry)
    await coordinator.async_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _sync(now=None) -> None:
        """Import any new Polar history (best effort, never fails setup)."""
        try:
            await async_import_history(hass, coordinator.accesslink, entry)
        except Exception:  # noqa: BLE001 - history sync must never break setup
            _LOGGER.exception("Polar: history sync failed")

    interval = timedelta(
        minutes=entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
    )

    # Run the history sync once now (in the background) and then on the cadence.
    entry.async_create_background_task(hass, _sync(), "polar_history_sync")
    entry.async_on_unload(async_track_time_interval(hass, _sync, interval))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok

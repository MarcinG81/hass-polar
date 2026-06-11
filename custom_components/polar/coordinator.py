"""Polar physical-info coordinator (heart rate max / zones).

The history itself is published as long-term statistics (see statistics.py).
This lightweight coordinator only fetches the user's static physical info so we
can expose the maximum heart rate and the derived training zones — used to draw
zone bands on the heart rate chart.
"""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .polaraccesslink.accesslink import AccessLink

_LOGGER = logging.getLogger(__name__)

# Polar's 5 heart rate zones as fractions of the upper bound (50-60-70-80-90-100%).
ZONE_FRACTIONS = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


def _zone_bounds(maximum: int | None, resting: int | None) -> tuple[list[int] | None, list[int] | None]:
    """Return (percent-of-max bounds, Karvonen/HRR bounds) as 6 values each."""
    if not maximum:
        return None, None
    percent_max = [round(maximum * f) for f in ZONE_FRACTIONS]
    karvonen = None
    if resting:
        reserve = maximum - resting
        karvonen = [round(resting + f * reserve) for f in ZONE_FRACTIONS]
    return percent_max, karvonen


class PolarProfileCoordinator(DataUpdateCoordinator):
    """Fetch the user's physical info and derive heart rate zones."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                minutes=entry.options.get(
                    CONF_SCAN_INTERVAL,
                    entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                )
            ),
        )
        self._entry = entry
        self.accesslink = AccessLink(
            client_id=entry.data[CONF_CLIENT_ID],
            client_secret=entry.data[CONF_CLIENT_SECRET],
        )

    @property
    def user_name(self) -> str:
        """Return name of the user."""
        return self._entry.data[CONF_NAME]

    @property
    def entry_id(self) -> str:
        """Return entry ID."""
        return self._entry.entry_id

    async def _async_update_data(self) -> dict:
        """Fetch physical info + latest heart rate (tolerant of missing data)."""
        token = self._entry.data[CONF_ACCESS_TOKEN]
        info = await self.hass.async_add_executor_job(
            self.accesslink.get_physical_info, token
        )
        heart_rate = await self.hass.async_add_executor_job(
            self.accesslink.get_continuous_heart_rate, token
        )
        maximum = info.get("maximum_heart_rate")
        resting = info.get("resting_heart_rate")
        percent_max, karvonen = _zone_bounds(maximum, resting)
        return {
            "max_heart_rate": maximum,
            "resting_heart_rate": resting,
            "aerobic_threshold": info.get("aerobic_threshold"),
            "anaerobic_threshold": info.get("anaerobic_threshold"),
            "vo2_max": info.get("vo2_max"),
            "zones_percent_max": percent_max,
            "zones_karvonen": karvonen,
            "heart_rate": heart_rate,
        }

"""Polar max heart rate / training zones sensor.

A single diagnostic sensor exposing the user's maximum heart rate (from Polar's
physical info) plus the derived training zones. The zone bounds are exposed as
attributes so a chart (e.g. ApexCharts) can draw zone bands without hard-coding
any numbers — it works for any user, pulled from the API.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import PolarProfileCoordinator

# Labels for the five Polar heart rate zones, low to high.
ZONE_NAMES = ("Z1", "Z2", "Z3", "Z4", "Z5")


def _zone_ranges(bounds: list[int] | None) -> dict[str, str] | None:
    """Turn 6 boundary values into {"Z1": "95-114", ...}."""
    if not bounds or len(bounds) != 6:
        return None
    return {ZONE_NAMES[i]: f"{bounds[i]}-{bounds[i + 1]}" for i in range(5)}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Polar sensors."""
    coordinator: PolarProfileCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            PolarMaxHeartRateSensor(coordinator),
            PolarHeartRateSensor(coordinator),
        ]
    )


class PolarMaxHeartRateSensor(CoordinatorEntity[PolarProfileCoordinator], SensorEntity):
    """Maximum heart rate, with training zones exposed as attributes."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_name = "Max heart rate"
    _attr_native_unit_of_measurement = "bpm"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:heart-flash"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PolarProfileCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry_id}_max_heart_rate"
        self._attr_device_info = DeviceInfo(
            configuration_url="https://flow.polar.com/",
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, coordinator.entry_id)},
            manufacturer="Polar",
            name=coordinator.user_name,
        )

    @property
    def available(self) -> bool:
        """Return True if a maximum heart rate is known."""
        return super().available and self.coordinator.data.get("max_heart_rate") is not None

    @property
    def native_value(self) -> int | None:
        """Return the maximum heart rate."""
        return self.coordinator.data.get("max_heart_rate")

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return resting HR, thresholds and zone bounds for charts."""
        data = self.coordinator.data
        return {
            "resting_heart_rate": data.get("resting_heart_rate"),
            "aerobic_threshold": data.get("aerobic_threshold"),
            "anaerobic_threshold": data.get("anaerobic_threshold"),
            "vo2_max": data.get("vo2_max"),
            # Raw boundary arrays (6 values each) for chart zone bands.
            "zones_percent_max": data.get("zones_percent_max"),
            "zones_karvonen": data.get("zones_karvonen"),
            # Human-readable ranges.
            "zone_ranges_percent_max": _zone_ranges(data.get("zones_percent_max")),
            "zone_ranges_karvonen": _zone_ranges(data.get("zones_karvonen")),
        }


class PolarHeartRateSensor(CoordinatorEntity[PolarProfileCoordinator], SensorEntity):
    """Latest continuous heart rate.

    This entity exists mainly to carry the heart rate long-term statistics
    (the history is imported onto it), so charts that only accept real entities
    — e.g. ApexCharts — can plot it. Its state is the latest known 5-minute
    sample, refreshed on the slow sync cadence.
    """

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_name = "Heart rate"
    _attr_native_unit_of_measurement = "bpm"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:heart-pulse"

    def __init__(self, coordinator: PolarProfileCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry_id}_heart_rate"
        self._attr_device_info = DeviceInfo(
            configuration_url="https://flow.polar.com/",
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, coordinator.entry_id)},
            manufacturer="Polar",
            name=coordinator.user_name,
        )

    @property
    def available(self) -> bool:
        """Return True if a recent heart rate value is known."""
        return super().available and bool(self.coordinator.data.get("heart_rate"))

    @property
    def native_value(self) -> int | None:
        """Return the latest heart rate sample."""
        return (self.coordinator.data.get("heart_rate") or {}).get("latest")

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return min/max/average of the most recent day."""
        heart_rate = self.coordinator.data.get("heart_rate") or {}
        return {
            "min": heart_rate.get("min"),
            "max": heart_rate.get("max"),
            "average": heart_rate.get("average"),
            "samples_count": heart_rate.get("samples_count"),
            "date": heart_rate.get("date"),
        }

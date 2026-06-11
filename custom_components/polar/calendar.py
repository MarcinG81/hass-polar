"""Polar exercises as a calendar.

Each training session from Polar is exposed as a calendar event, with the full
session details (sport, distance, duration, heart rate, training load, calories)
preserved in the event — so multiple workouts per day are all kept with their
attributes, and the recent history is available without depending on a tight
poll interval.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import PolarProfileCoordinator

_LOGGER = logging.getLogger(__name__)


def _parse_hms(value: Any) -> timedelta:
    """Parse a ``H:MM:SS`` duration string into a timedelta."""
    try:
        parts = [float(part) for part in str(value).split(":")]
    except (ValueError, AttributeError):
        return timedelta()
    while len(parts) < 3:
        parts.insert(0, 0.0)
    hours, minutes, seconds = parts[-3], parts[-2], parts[-1]
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def _event(exercise: dict) -> CalendarEvent | None:
    """Build a CalendarEvent from a Polar exercise, or None if unparseable."""
    start_raw = exercise.get("start_time")
    if not start_raw:
        return None
    try:
        start = datetime.fromisoformat(start_raw)
    except ValueError:
        return None
    offset = exercise.get("start_time_utc_offset") or 0
    start = start.replace(tzinfo=timezone(timedelta(minutes=offset)))

    end = start + _parse_hms(exercise.get("duration"))
    if end <= start:
        end = start + timedelta(minutes=1)

    sport = (exercise.get("sport") or "Workout").replace("_", " ").title()

    details: list[str] = []
    if (distance := exercise.get("distance")) is not None:
        details.append(f"Distance: {round(distance / 1000, 2)} km")
    details.append(f"Duration: {exercise.get('duration')}")
    if (avg := exercise.get("heart_rate_average")) is not None:
        details.append(f"HR avg: {avg} bpm")
    if (maximum := exercise.get("heart_rate_maximum")) is not None:
        details.append(f"HR max: {maximum} bpm")
    if (load := exercise.get("training_load")) is not None:
        details.append(f"Training load: {load}")
    if (calories := exercise.get("calories")) is not None:
        details.append(f"Calories: {calories} kcal")
    if (device := exercise.get("device")) is not None:
        details.append(f"Device: {device}")

    return CalendarEvent(
        start=start,
        end=end,
        summary=sport,
        description="\n".join(details),
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Polar exercises calendar."""
    coordinator: PolarProfileCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PolarExerciseCalendar(coordinator)])


class PolarExerciseCalendar(CoordinatorEntity[PolarProfileCoordinator], CalendarEntity):
    """Calendar of Polar training sessions."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_name = "Exercises"
    _attr_icon = "mdi:run"

    def __init__(self, coordinator: PolarProfileCoordinator) -> None:
        """Initialize the calendar."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry_id}_exercises"
        self._attr_device_info = DeviceInfo(
            configuration_url="https://flow.polar.com/",
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, coordinator.entry_id)},
            manufacturer="Polar",
            name=coordinator.user_name,
        )

    def _events(self) -> list[CalendarEvent]:
        """Build all known exercise events, sorted by start time."""
        exercises = self.coordinator.data.get("exercises") or []
        events = [event for ex in exercises if (event := _event(ex)) is not None]
        return sorted(events, key=lambda event: event.start)

    @property
    def event(self) -> CalendarEvent | None:
        """Return the most recent training session."""
        events = self._events()
        return events[-1] if events else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return all training sessions within the given range."""
        return [
            event
            for event in self._events()
            if event.start < end_date and event.end > start_date
        ]

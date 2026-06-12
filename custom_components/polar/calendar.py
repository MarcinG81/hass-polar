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

    sport = (
        exercise.get("detailed_sport_info")
        or exercise.get("detailed-sport-info")
        or exercise.get("sport")
        or "Workout"
    ).replace("_", " ").title()

    details: list[str] = []
    if (distance := exercise.get("distance")) is not None:
        details.append(f"Distance: {round(distance / 1000, 2)} km")
    details.append(f"Duration: {exercise.get('duration')}")
    if (avg := exercise.get("heart_rate_average")) is not None:
        details.append(f"HR avg: {avg} bpm")
    if (maximum := exercise.get("heart_rate_maximum")) is not None:
        details.append(f"HR max: {maximum} bpm")
    load_pro = exercise.get("training_load_pro") or {}
    cardio_load = exercise.get("training_load") or load_pro.get(
        "cardio-load", load_pro.get("cardio_load")
    )
    if cardio_load is not None:
        details.append(f"Cardio load: {cardio_load}")
    muscle_load = load_pro.get("muscle-load", load_pro.get("muscle_load"))
    if muscle_load is not None:
        details.append(f"Muscle load: {muscle_load}")
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


def _hm(seconds: Any) -> str | None:
    """Format a duration in seconds as ``Xh Ym``."""
    if seconds is None:
        return None
    minutes = round(seconds / 60)
    return f"{minutes // 60}h {minutes % 60:02d}m"


def _sleep_event(night: dict) -> CalendarEvent | None:
    """Build a CalendarEvent from a Polar sleep night, or None if unparseable."""
    start_raw = night.get("sleep_start_time")
    end_raw = night.get("sleep_end_time")
    if not start_raw or not end_raw:
        return None
    try:
        start = datetime.fromisoformat(start_raw)
        end = datetime.fromisoformat(end_raw)
    except ValueError:
        return None
    if end <= start:
        return None

    score = night.get("sleep_score")
    summary = f"Sleep {score}/100" if score is not None else "Sleep"

    details: list[str] = []
    if score is not None:
        details.append(f"Score: {score}/100")
    stages = [
        ("Deep", _hm(night.get("deep_sleep"))),
        ("Light", _hm(night.get("light_sleep"))),
        ("REM", _hm(night.get("rem_sleep"))),
    ]
    details.append(
        " | ".join(f"{name}: {value}" for name, value in stages if value is not None)
    )
    if (cycles := night.get("sleep_cycles")) is not None:
        details.append(f"Cycles: {cycles}")
    if (continuity := night.get("continuity")) is not None:
        details.append(f"Continuity: {continuity}")
    if (interruptions := night.get("total_interruption_duration")) is not None:
        details.append(f"Awake: {_hm(interruptions)}")

    return CalendarEvent(
        start=start,
        end=end,
        summary=summary,
        description="\n".join(part for part in details if part),
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Polar calendars (exercises and sleep)."""
    coordinator: PolarProfileCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            PolarExerciseCalendar(coordinator),
            PolarSleepCalendar(coordinator),
        ]
    )


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


class PolarSleepCalendar(CoordinatorEntity[PolarProfileCoordinator], CalendarEntity):
    """Calendar of Polar sleep nights."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_name = "Sleep"
    _attr_icon = "mdi:sleep"

    def __init__(self, coordinator: PolarProfileCoordinator) -> None:
        """Initialize the calendar."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry_id}_sleep"
        self._attr_device_info = DeviceInfo(
            configuration_url="https://flow.polar.com/",
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, coordinator.entry_id)},
            manufacturer="Polar",
            name=coordinator.user_name,
        )

    def _events(self) -> list[CalendarEvent]:
        """Build all known sleep events, sorted by start time."""
        nights = self.coordinator.data.get("sleep") or []
        events = [event for night in nights if (event := _sleep_event(night)) is not None]
        return sorted(events, key=lambda event: event.start)

    @property
    def event(self) -> CalendarEvent | None:
        """Return the most recent sleep night."""
        events = self._events()
        return events[-1] if events else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return all sleep nights within the given range."""
        return [
            event
            for event in self._events()
            if event.start < end_date and event.end > start_date
        ]

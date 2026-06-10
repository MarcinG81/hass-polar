"""Import historical Polar data as Home Assistant long-term statistics.

Home Assistant sensors only build history forward from the moment they start
polling and cannot backfill past states. Polar however keeps the last ~28 days
of nightly/daily data and per-day continuous heart rate samples. This module
imports that history as external long-term statistics (``polar:*``) so it shows
up in the statistics graphs / Developer Tools, including data from before the
integration was installed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta
import logging
from typing import Any

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .coordinator import PolarCoordinator

_LOGGER = logging.getLogger(__name__)

# How to declare a "mean" statistic. Newer Home Assistant wants ``mean_type``;
# fall back to the legacy ``has_mean`` boolean on older cores.
try:
    from homeassistant.components.recorder.models import StatisticMeanType

    _MEAN_META: dict = {"mean_type": StatisticMeanType.ARITHMETIC}
except ImportError:  # pragma: no cover - older Home Assistant
    _MEAN_META = {"has_mean": True}

# Continuous heart rate is fetched one API call per day, so keep the dense
# backfill window modest to stay friendly with Polar rate limits.
CHR_BACKFILL_DAYS = 7


def _hour_start(value: datetime) -> datetime:
    """Return the UTC hour-aligned start for a datetime (required by stats)."""
    return dt_util.as_utc(value).replace(minute=0, second=0, microsecond=0)


def _date_to_hour(date_str: Any, hour: int = 12) -> datetime | None:
    """Map a local ``YYYY-MM-DD`` string to an hour-aligned UTC datetime."""
    day = dt_util.parse_date(date_str) if isinstance(date_str, str) else None
    if day is None:
        return None
    local = datetime.combine(
        day, time(hour=hour), tzinfo=dt_util.DEFAULT_TIME_ZONE
    )
    return _hour_start(local)


def _seconds_to_minutes(seconds: Any) -> float | None:
    """Convert a duration in seconds to whole minutes."""
    if seconds is None:
        return None
    return round(seconds / 60)


def _metadata(suffix: str, name: str, unit: str | None) -> StatisticMetaData:
    """Build external statistic metadata for a Polar metric."""
    return StatisticMetaData(
        **_MEAN_META,
        has_sum=False,
        name=f"Polar {name}",
        source=DOMAIN,
        statistic_id=f"{DOMAIN}:{suffix}",
        unit_of_measurement=unit,
        unit_class=None,
    )


def _nightly_stats(
    records: list[dict], value_fn: Callable[[dict], Any]
) -> list[StatisticData]:
    """Build one statistic point per dated record (sleep/recharge/cardio)."""
    by_hour: dict[datetime, float] = {}
    for record in records:
        start = _date_to_hour(record.get("date"))
        value = value_fn(record)
        if start is None or value is None:
            continue
        by_hour[start] = value  # one value per day; last wins
    return [
        StatisticData(start=start, mean=value, min=value, max=value)
        for start, value in sorted(by_hour.items())
    ]


def _heart_rate_stats(
    samples_by_day: list[tuple[str, list[dict]]],
) -> list[StatisticData]:
    """Aggregate per-day 5-minute HR samples into hourly statistics."""
    buckets: dict[datetime, list[int]] = {}
    for date_str, samples in samples_by_day:
        day = dt_util.parse_date(date_str)
        if day is None:
            continue
        for sample in samples:
            heart_rate = sample.get("heart_rate")
            sample_time = sample.get("sample_time")
            if heart_rate is None or not sample_time:
                continue
            try:
                hour = int(str(sample_time).split(":", 1)[0])
            except ValueError:
                continue
            start = _hour_start(
                datetime.combine(
                    day, time(hour=hour), tzinfo=dt_util.DEFAULT_TIME_ZONE
                )
            )
            buckets.setdefault(start, []).append(heart_rate)
    return [
        StatisticData(
            start=start,
            mean=round(sum(values) / len(values), 1),
            min=min(values),
            max=max(values),
        )
        for start, values in sorted(buckets.items())
    ]


async def async_import_history(
    hass: HomeAssistant, coordinator: PolarCoordinator, entry: ConfigEntry
) -> None:
    """Fetch the available Polar history and import it as statistics."""
    token = entry.data[CONF_ACCESS_TOKEN]
    accesslink = coordinator.accesslink

    # Nightly/daily metrics each come from a single "last 28 days" list call.
    sleep = await hass.async_add_executor_job(accesslink.get_sleep, token)
    recharge = await hass.async_add_executor_job(accesslink.get_recharge, token)
    cardio = await hass.async_add_executor_job(accesslink.get_cardio_load, token)

    metrics: list[tuple[StatisticMetaData, list[StatisticData]]] = [
        (
            _metadata("deep_sleep", "deep sleep", "min"),
            _nightly_stats(sleep, lambda r: _seconds_to_minutes(r.get("deep_sleep"))),
        ),
        (
            _metadata("light_sleep", "light sleep", "min"),
            _nightly_stats(sleep, lambda r: _seconds_to_minutes(r.get("light_sleep"))),
        ),
        (
            _metadata("rem_sleep", "REM sleep", "min"),
            _nightly_stats(sleep, lambda r: _seconds_to_minutes(r.get("rem_sleep"))),
        ),
        (
            _metadata("sleep_score", "sleep score", "score"),
            _nightly_stats(sleep, lambda r: r.get("sleep_score")),
        ),
        (
            _metadata("heart_rate_variability", "heart rate variability", "ms"),
            _nightly_stats(recharge, lambda r: r.get("heart_rate_variability_avg")),
        ),
        (
            _metadata("breathing_rate", "breathing rate", "bpm"),
            _nightly_stats(recharge, lambda r: r.get("breathing_rate_avg")),
        ),
        (
            _metadata("cardio_load", "cardio load", None),
            _nightly_stats(cardio, lambda r: r.get("cardio_load")),
        ),
    ]

    # Dense continuous heart rate: one call per day for the recent window.
    today = dt_util.now().date()
    samples_by_day: list[tuple[str, list[dict]]] = []
    for offset in range(1, CHR_BACKFILL_DAYS + 1):
        day = (today - timedelta(days=offset)).isoformat()
        samples = await hass.async_add_executor_job(
            accesslink.get_continuous_heart_rate_samples, token, day
        )
        if samples:
            samples_by_day.append((day, samples))
    metrics.append(
        (_metadata("heart_rate", "heart rate", "bpm"), _heart_rate_stats(samples_by_day))
    )

    imported_points = 0
    imported_metrics = 0
    for metadata, statistics in metrics:
        if not statistics:
            continue
        async_add_external_statistics(hass, metadata, statistics)
        imported_points += len(statistics)
        imported_metrics += 1

    _LOGGER.info(
        "Polar: imported %s historical statistics points across %s metrics",
        imported_points,
        imported_metrics,
    )

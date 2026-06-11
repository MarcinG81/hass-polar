"""Import Polar history as long-term statistics.

Home Assistant cannot backfill past sensor states, so Polar history is published
as external long-term statistics (``polar:*``). On the first run the last
~28 days are imported; afterwards only the missing/recent days are appended, so
each scheduled sync stays cheap (important for continuous heart rate, which costs
one API call per day).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
import logging
from typing import Any

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    async_import_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .polaraccesslink.accesslink import AccessLink

_LOGGER = logging.getLogger(__name__)

# How far back to import on the first run (Polar keeps ~28 days of history).
HISTORY_DAYS = 28
STORE_VERSION = 1

# How to declare a "mean" statistic. Newer Home Assistant wants ``mean_type``;
# fall back to the legacy ``has_mean`` boolean on older cores.
try:
    from homeassistant.components.recorder.models import StatisticMeanType

    _MEAN_META: dict = {"mean_type": StatisticMeanType.ARITHMETIC}
except ImportError:  # pragma: no cover - older Home Assistant
    _MEAN_META = {"has_mean": True}


def _hour_start(value: datetime) -> datetime:
    """Return the UTC hour-aligned start for a datetime (required by stats)."""
    return dt_util.as_utc(value).replace(minute=0, second=0, microsecond=0)


def _date_to_hour(date_str: Any, hour: int = 12) -> datetime | None:
    """Map a local ``YYYY-MM-DD`` string to an hour-aligned UTC datetime."""
    day = dt_util.parse_date(date_str) if isinstance(date_str, str) else None
    if day is None:
        return None
    return _hour_start(datetime.combine(day, time(hour=hour), tzinfo=dt_util.DEFAULT_TIME_ZONE))


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


def _entity_metadata(statistic_id: str, unit: str | None) -> StatisticMetaData:
    """Build statistic metadata targeting an existing sensor entity."""
    return StatisticMetaData(
        **_MEAN_META,
        has_sum=False,
        name=None,
        source="recorder",
        statistic_id=statistic_id,
        unit_of_measurement=unit,
        unit_class=None,
    )


def _nightly_stats(
    records: list[dict], value_fn: Callable[[dict], Any], since: date
) -> list[StatisticData]:
    """Build one statistic point per dated record on/after ``since``."""
    by_hour: dict[datetime, float] = {}
    for record in records:
        day = dt_util.parse_date(record.get("date")) if isinstance(record.get("date"), str) else None
        if day is None or day < since:
            continue
        start = _date_to_hour(record.get("date"))
        value = value_fn(record)
        if start is None or value is None:
            continue
        by_hour[start] = value
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
                datetime.combine(day, time(hour=hour), tzinfo=dt_util.DEFAULT_TIME_ZONE)
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
    hass: HomeAssistant, accesslink: AccessLink, entry: ConfigEntry
) -> None:
    """Import the missing Polar history as long-term statistics.

    First run imports the last ~28 days; later runs only append days since the
    last successful import (the most recent day is always refreshed to pick up
    late-arriving data from a device sync).
    """
    token = entry.data[CONF_ACCESS_TOKEN]
    store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}_history_{entry.entry_id}")
    saved = await store.async_load() or {}

    today = dt_util.now().date()
    earliest = today - timedelta(days=HISTORY_DAYS)
    last_day = (
        date.fromisoformat(saved["last_day"])
        if isinstance(saved.get("last_day"), str)
        else None
    )
    # Start from the last imported day (re-fetched to catch updates), but never
    # earlier than what Polar keeps (~28 days).
    since = max(last_day or earliest, earliest)

    # Nightly/daily metrics: one "last 28 days" list call each, filtered.
    sleep = await hass.async_add_executor_job(accesslink.get_sleep, token)
    recharge = await hass.async_add_executor_job(accesslink.get_recharge, token)
    cardio = await hass.async_add_executor_job(accesslink.get_cardio_load, token)

    metrics: list[tuple[StatisticMetaData, list[StatisticData]]] = [
        (
            _metadata("deep_sleep", "deep sleep", "min"),
            _nightly_stats(sleep, lambda r: _seconds_to_minutes(r.get("deep_sleep")), since),
        ),
        (
            _metadata("light_sleep", "light sleep", "min"),
            _nightly_stats(sleep, lambda r: _seconds_to_minutes(r.get("light_sleep")), since),
        ),
        (
            _metadata("rem_sleep", "REM sleep", "min"),
            _nightly_stats(sleep, lambda r: _seconds_to_minutes(r.get("rem_sleep")), since),
        ),
        (
            _metadata("sleep_score", "sleep score", "score"),
            _nightly_stats(sleep, lambda r: r.get("sleep_score"), since),
        ),
        (
            _metadata("heart_rate_variability", "heart rate variability", "ms"),
            _nightly_stats(recharge, lambda r: r.get("heart_rate_variability_avg"), since),
        ),
        (
            _metadata("breathing_rate", "breathing rate", "bpm"),
            _nightly_stats(recharge, lambda r: r.get("breathing_rate_avg"), since),
        ),
        (
            _metadata("cardio_load", "cardio load", None),
            _nightly_stats(cardio, lambda r: r.get("cardio_load"), since),
        ),
    ]

    # Continuous heart rate: one call per day, only for the days we still need.
    samples_by_day: list[tuple[str, list[dict]]] = []
    day = since
    while day <= today:
        iso = day.isoformat()
        samples = await hass.async_add_executor_job(
            accesslink.get_continuous_heart_rate_samples, token, iso
        )
        if samples:
            samples_by_day.append((iso, samples))
        day += timedelta(days=1)
    heart_rate_stats = _heart_rate_stats(samples_by_day)

    imported_points = 0
    imported_metrics = 0
    for metadata, statistics in metrics:
        if not statistics:
            continue
        async_add_external_statistics(hass, metadata, statistics)
        imported_points += len(statistics)
        imported_metrics += 1

    # Heart rate is imported onto the Heart rate sensor entity (so charts that
    # only accept real entities, e.g. ApexCharts, can plot it); fall back to an
    # external polar:heart_rate statistic if the entity isn't registered yet.
    if heart_rate_stats:
        hr_entity_id = er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_heart_rate"
        )
        if hr_entity_id:
            async_import_statistics(
                hass, _entity_metadata(hr_entity_id, "bpm"), heart_rate_stats
            )
        else:
            async_add_external_statistics(
                hass, _metadata("heart_rate", "heart rate", "bpm"), heart_rate_stats
            )
        imported_points += len(heart_rate_stats)
        imported_metrics += 1

    await store.async_save({"last_day": today.isoformat()})

    _LOGGER.info(
        "Polar: synced %s statistics points across %s metrics (since %s)",
        imported_points,
        imported_metrics,
        since.isoformat(),
    )

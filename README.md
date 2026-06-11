# Polar integration for Home Assistant

This is a _custom component_ for [Home Assistant](https://www.home-assistant.io/).
The `polar` integration pulls your data from [Polar](https://flow.polar.com) via
the [Polar AccessLink API](https://www.polar.com/accesslink-api/).

## History-only by design

Polar devices (Loop, watches, ...) don't stream in real time — they sync to
Polar Flow in batches, only every so often, and the data is essentially
daily-granular (sleep, Nightly Recharge and cardio load are one value per day;
continuous heart rate is historical samples). Polling for a "current" value
would be misleading, so this integration does **not** create live sensors.

Instead it publishes your Polar history as **long-term statistics** (`polar:*`)
and keeps them up to date on a slow cadence (default every 6 hours):

- on the first run it imports the last ~28 days;
- afterwards it only appends the missing/new days (and refreshes the most recent
  day to pick up late-arriving data), so each sync stays cheap — which matters
  for continuous heart rate, where each day is a separate API call.

You then chart the statistics with the built-in `statistics-graph` card (or any
statistics-aware card). They show up under **Developer Tools → Statistics**
(search `polar`).

### Statistics published

| Statistic id | Description | Resolution |
| --- | --- | --- |
| `polar:heart_rate` | Continuous heart rate (min / mean / max) | hourly |
| `polar:deep_sleep` / `polar:light_sleep` / `polar:rem_sleep` | Sleep-stage durations (minutes) | daily |
| `polar:sleep_score` | Sleep score 1–100 | daily |
| `polar:heart_rate_variability` | HRV (RMSSD, ms) from Nightly Recharge | daily |
| `polar:breathing_rate` | Breathing rate from Nightly Recharge | daily |
| `polar:cardio_load` | Cardio (training) load | daily |

The import is best-effort: a metric the device doesn't provide (or a day with no
data) is logged and skipped, never failing the rest of the sync. It requires the
`recorder` integration (declared as a dependency).

> Note: only long-term statistics are produced. Home Assistant does not allow
> inserting past raw states, so there is no instantaneous "current value" entity.

## Heart rate zones

The integration also exposes a single diagnostic sensor, **`Max heart rate`**,
fetched from your Polar physical info (`maximum_heart_rate`). Its attributes
include the resting heart rate, the aerobic/anaerobic thresholds, VO2max and the
five training-zone boundaries computed two ways:

- `zones_percent_max` — % of maximum heart rate (as Polar Flow shows them);
- `zones_karvonen` — % of heart rate reserve (Karvonen, uses resting HR).

Because the zone bounds are pulled from the API and exposed as attributes, charts
can draw zone bands without hard-coding any numbers — it works for any user (see
the heart rate views in the dashboard).

## Installation

### HACS

HACS > Integrations > Explore & Add Repositories > Polar > Install this repository

### Manually

Copy the `custom_components/polar` folder into the config folder.

## Configuration

You need to create a Client in [Polar Access Link](https://admin.polaraccesslink.com)
and set in `Authorization redirect URLs`:

* `https://your_external_access_to_ha`
* `https://your_external_access_to_ha/api/polar_auth` (selected)

To add the Polar integration to your installation, go to Settings >> Devices &
Services in the UI, click the **+ Add Integration** button and select Polar.

### Fields

* `Client ID` and `Client secret`: get credentials from [Polar Access Link](https://admin.polaraccesslink.com).
* `Scan Interval`: how often (in minutes) to sync history from the Polar API (default: `360`, i.e. every 6 hours).
* `URL`: URL used to access your Home Assistant (default: your external or internal URL if configured in HA settings).

When you submit your credentials the integration first runs a connection test
(reachability to Polar + that the `Client ID` is accepted) and logs the result,
so a wrong URL, missing connectivity or an invalid client is reported up front
with a clear error instead of failing later during the OAuth exchange.

## Dashboard

[`polar-dashboard.yaml`](./polar-dashboard.yaml) is a ready-to-use Lovelace
dashboard with *Sleep*, *Training* and *Heart rate* history views. Most cards use
the built-in `statistics-graph` card; the heart rate "zone" cards additionally
use the [ApexCharts Card](https://github.com/RomRider/apexcharts-card) (HACS) and
draw the training-zone bands from the `Max heart rate` sensor attributes. Paste
it into a new dashboard via the raw configuration editor.

## Credits

Thanks to https://github.com/burnnat/ha-polar

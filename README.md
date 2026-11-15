# Ergon Energy Outages for Home Assistant

Know when the power is out, or about to be, at your place. This custom integration checks Ergon Energy's outage map (regional Queensland) for **current and planned outages that cover a location you choose**. It also:

- draws the outage areas on a map
- creates one entity per outage, which appears and disappears with the outage
- fires one event per change, so notifications never repeat every poll or after a restart

> Unofficial. It reads the ArcGIS layer behind Ergon's public Outage Finder map, which isn't a documented API and could change without notice. Energex (south-east Queensland) is not covered.

## Install

1. In Home Assistant, open **HACS → ⋮ → Custom repositories**.
2. Add `https://github.com/LONGBOW7757/ha-ergon-outages` with type **Integration**.
3. Search HACS for **Ergon Energy Outages**, download it, then restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration → Ergon Energy Outages**.
5. Give the location a name (e.g. `Home`), drag the pin and circle to your property, and pick an update interval.

To change the location, radius or interval later: **Settings → Devices & services → Ergon Energy Outages → Configure**. Add the integration again to watch more places (a holiday house, a parent's home).

### Choosing a radius

Ergon's server works out which outage areas touch your circle. Some outage areas cover only a few houses, so a radius of **250–500 m** is a good default. It catches outages that affect you even if your pin is on the road or a corner of the block. Use 0 to match only areas covering the exact pin, or a few km to see what's happening around town.

## What you get

Entity IDs below assume the location is named `Home`.

| Entity | What it shows |
|---|---|
| `binary_sensor.ergon_outages_home_outage_now` | **On** while an outage covers the location. The `geojson` attribute holds the outage areas. |
| `binary_sensor.ergon_outages_home_upcoming_outage` | **On** while a planned outage is scheduled. The `geojson` attribute holds the outage areas. |
| `image.ergon_outages_home_outage_map` | Map picture of the area: your location and search circle, current outages in red, upcoming ones in orange. Used for notification images and dashboards. |
| `sensor.ergon_outages_home_current_outages` | Number of outages happening now |
| `sensor.ergon_outages_home_upcoming_outages` | Number of scheduled outages |
| `sensor.ergon_outages_home_next_outage_start` | When the next planned outage starts |
| `sensor.ergon_outages_home_estimated_restoration` | Ergon's estimated restoration time for current outages (the latest, if several) |
| `sensor.ergon_outages_home_outage_<event id>` | One per outage: **Current** or **Upcoming**, with reason, status, customers affected, suburbs, streets, start, estimated restoration, map position and polygon. It is created when the outage appears and removed when it clears. |

"Current" means the outage's start time has passed or isn't set; "Upcoming" means it starts in the future. Ergon rarely gives a restoration time for new faults, so expect "unknown" early on.

## Notifications

The integration fires these events. Each happens **once per outage change**, and what has been announced is saved, so restarts and network hiccups don't cause repeats.

| Event | When |
|---|---|
| `ergon_outages_new_outage` | A new outage affects the location (`phase` is `current` or `future`) |
| `ergon_outages_outage_started` | A scheduled outage has begun |
| `ergon_outages_outage_updated` | Status, start, restoration time or customer count changed |
| `ergon_outages_outage_cleared` | The outage is no longer listed |

Event data: `event_id`, `phase`, `outage_type` (`planned`/`unplanned`), `status`, `reason`, `customers_affected`, `suburbs`, `start`, `estimated_restoration` (ISO times or `null`), `location`, `config_entry_id`, `map_entity_id`, and `map_url` (a picture of the map for this alert; the link works without a login for 7 days, so it's safe to attach to phone notifications; not included for cleared events).

Outages that already exist when you first add the integration are recorded quietly (no burst of alerts on day one). They still show up in the entities.

A ready-made automation is in [examples/notifications.yaml](examples/notifications.yaml). Paste it in **Settings → Automations & scenes → Create automation → ⋮ → Edit in YAML**, then change `notify.notify` to your phone (e.g. `notify.mobile_app_<your_phone>`).

## Map

**Notifications with a map:** the example automation attaches the `Outage map` picture to new and started outage alerts. Your phone downloads it from Home Assistant when the alert arrives, so this only works if the phone can reach Home Assistant at that moment (on home Wi‑Fi, or anywhere with remote access such as Home Assistant Cloud). Otherwise the alert arrives without the picture.

The map zooms to fit the outage areas found within your radius (or shows the whole search circle when there are none). Map tiles come from OpenStreetMap and are only downloaded when the outages change.

To show the map on a dashboard, add a **Picture entity** card or an **Image** card using `image.ergon_outages_home_outage_map`.

For an interactive map, install **Map Card** (`nathan-gs/ha-map-card`) from HACS, then add a manual card with [examples/map-card.yaml](examples/map-card.yaml). Current outages are drawn in red and upcoming ones in orange; hover over an area for details.

Without HACS cards, the built-in **Map** card can show a pin for each `sensor.ergon_outages_home_outage_*` entity (at the centre of the outage area), but it can't draw the areas themselves.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install pytest-homeassistant-custom-component
.venv/bin/pytest
```

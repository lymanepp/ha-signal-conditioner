# Signal Conditioner

Signal Conditioner replaces established Home Assistant Template, Compensation, Filter, and
throttle chains with one canonical sensor.

## Explicit pipeline configuration

The first form selects the source and only the behaviors to include:

- Apply custom value limits
- Calibrate values
- Process readings in a time window
- Round output values

Only configuration pages for enabled behaviors are shown. Disabled behaviors
are not stored or executed.

The pipeline runs in this order:

```text
Source state or attribute
  → unknown/unavailable: mirrored by default (see Availability)
  → reject missing, nonnumeric, NaN, and infinity
  → optional minimum/maximum: reject or clamp
  → optional calibration
  → optional fixed time window: time-weighted mean or latest
  → optional rounding
  → canonical Home Assistant sensor
```

## Configuration choices

### Source

- Source sensor
- Optional source attribute
- Optional output-name override

Signal Conditioner inherits the unit, device class, state class, and icon whenever a
state-based source provides them. The config flow exposes validated overrides
for all four values. Explicit overrides win over inherited metadata;
attribute-based sources inherit none of the parent entity metadata unless an
override is configured. The same fields are available when reconfiguring an
existing Signal Conditioner sensor, and clearing a field removes that override.

### Custom value limits

- Minimum valid value
- Maximum valid value
- `mode`: `reject` (default) drops an out-of-range raw reading and keeps the
  value already in force; `clamp` pins it to the limit, like Home Assistant's
  `range` filter. Limits apply to the raw reading, before calibration.

```yaml
value_limits:
  minimum: 0
  maximum: 100
  mode: clamp
```

### Calibration

- Raw and corrected value pairs
- Polynomial degree; omitted degree defaults to `1`

### Time window

```yaml
window:
  duration: 60
  output: mean
```

A configured instance owns a repeating interval timer and publishes once at each
interval boundary. The source is treated as a sample-and-hold signal: each accepted
calibrated reading stays in force until the next one, including across interval
boundaries, so a quiet interval publishes the value still in force.

- `mean` publishes the time-weighted mean over the interval: each reading counts
  for as long as it was in force, like Home Assistant's
  `time_simple_moving_average`. A burst of quick readings is not over-counted.
  This is the default when `output` is omitted.
- `latest` publishes the reading in force when the interval ends.

The first value is never delayed by the window: at setup, and after any gap
(unavailable, unknown, or stale source), the first valid reading is published at
once.

Omit `window` to publish every accepted reading immediately.

### Availability

```yaml
availability:
  follow_source: true   # default
  stale_after: 900      # seconds; optional
```

- `follow_source` (default `true`): the sensor is unavailable while its source is
  unavailable or missing, and unknown while the source is unknown, like Home
  Assistant's filter sensor. A window does not average across the gap. With
  `false`, the last value is kept, as the original Calibration integration did.
- `stale_after`: the sensor becomes unavailable when the source has not reported
  for this long. Unchanged re-reports count, so a steady value does not go stale;
  rejected readings do not count. Use it for sources that never go unavailable
  on their own, such as MQTT sensors without an availability topic.
- After a restart, while the source has no state yet, the sensor shows its last
  value from before the restart (unless `follow_source` is on and the source is
  unavailable). `stale_after` applies to that restored value too.

### Missing-source diagnostics and Repairs

Signal Conditioner monitors the configured source entity and raises an actionable
**Settings → System → Repairs** issue only for a persistent configuration problem:

- **Missing source:** the entity ID is absent from both Home Assistant's current
  states and its entity registry.
- **Disabled source:** the source is disabled in the entity registry.

The check waits until Home Assistant has finished starting, then allows a
**30-second grace period** before raising a new issue. The same grace period
applies after a source is removed or its registry entry changes. An entity that
is registered but temporarily unloaded, or whose state is `unknown` or
`unavailable`, **does not** trigger a Repairs issue. Those are runtime conditions
handled by the existing `availability` options.

Repair issues identify the conditioner and source, describe the action needed,
and clear automatically when the reference is corrected or the conditioner is
unloaded, reconfigured, or deleted. No additional YAML settings are needed.

### Rounding

- Decimal places; an empty `rounding` block defaults to `precision: 2`

Rounding is applied only to the published result.

## YAML example

```yaml
signal_conditioner:
  garage_humidity:
    source: sensor.garage_humidity_raw

    value_limits:
      minimum: 0
      maximum: 100

    calibration:
      data_points:
        - [50.76, 53.00]
        - [60.76, 63.00]
      degree: 1

    window:
      duration: 60
      output: mean

    rounding:
      precision: 2
```

YAML and the config flow expose the same behavior set.

YAML is the source of truth for the entries it creates. Each key is imported as
a config entry on startup. To apply YAML edits without restarting Home Assistant,
use **Developer Tools → Actions → Signal Conditioner: Reload**
(`signal_conditioner.reload`). New YAML keys create sensors; changed keys replace
their entire configuration (including removing omitted pipeline stages); deleted or
renamed keys remove the old YAML-owned entry and entity.

Only YAML-imported entries are reconciled. UI-created entries are never modified,
even if a YAML key collides with a UI entry's unique ID (the collision is logged).
The YAML is validated before a reload changes any entries. Failed validation
preserves the existing configuration; an event named
`event_signal_conditioner_reloaded` is fired after a successful reload.

## Installation

Install as a custom HACS repository or copy `custom_components/signal_conditioner` into the
Home Assistant configuration directory, restart Home Assistant, and add
**Signal Conditioner** from **Settings → Devices & services → Add integration**.

## Development

Start the development Home Assistant instance with:

```bash
scripts/develop
```

Run lint and type checks with:

```bash
scripts/lint
```

Run the complete repository check, including tests, with:

```bash
scripts/check
```

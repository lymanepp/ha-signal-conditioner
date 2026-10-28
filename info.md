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
  → reject unknown, unavailable, missing, nonnumeric, NaN, and infinity
  → optional minimum/maximum rejection
  → optional calibration
  → optional fixed time window selecting mean or latest
  → optional rounding
  → canonical Home Assistant sensor
```

## Configuration choices

### Source

- Source sensor
- Optional source attribute
- Optional output-name override

Signal Conditioner inherits the unit, device class, state class, and icon whenever the
source provides them.

### Custom value limits

- Minimum valid value
- Maximum valid value

### Calibration

- Raw and corrected value pairs
- Polynomial degree; omitted degree defaults to `1`

### Time window

```yaml
window:
  duration: 60
  output: mean
```

A configured instance owns a repeating interval timer. It collects every accepted
calibrated reading received during each interval and publishes exactly once at the
interval boundary when at least one reading was collected. Empty intervals publish nothing.

- `mean` publishes the arithmetic mean of all readings in the period and is the default when `output` is omitted.
- `latest` publishes the final accepted reading in the period.

Omit `window` to publish every accepted reading immediately.

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

## Installation

Install as a custom HACS repository or copy `custom_components/signal_conditioner` into the
Home Assistant configuration directory, restart Home Assistant, and add
**Signal Conditioner** from **Settings → Devices & services → Add integration**.

## Development

```bash
scripts/lint
scripts/check
```

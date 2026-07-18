# Normify

Normify replaces established Home Assistant Template, Compensation, Filter, and
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
  → optional configured value rejection
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
- Hide source entity

Normify inherits the unit, device class, state class, and icon whenever a
state-based source provides them. The config flow exposes validated overrides
for all four values. Explicit overrides win over inherited metadata;
attribute-based sources inherit none of the parent entity metadata unless an
override is configured. The same fields are available when reconfiguring an
existing Normify sensor, and clearing a field removes that override.

### Custom value limits

- Minimum valid value
- Maximum valid value
- Specific numeric values to reject

### Calibration

- Raw and corrected value pairs
- Polynomial degree; omitted degree defaults to `1`

### Time window

```yaml
window:
  duration: 60
  output: mean
```

A configured window starts with the first accepted reading, collects every
accepted calibrated reading during the period, and publishes exactly once when
the period ends.

- `mean` publishes the arithmetic mean of all readings in the period and is the default when `output` is omitted.
- `latest` publishes the final accepted reading in the period.

Omit `window` to publish every accepted reading immediately.

### Rounding

- Decimal places; an empty `rounding` block defaults to `precision: 2`

Rounding is applied only to the published result.

## YAML example

```yaml
normify:
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

Install as a custom HACS repository or copy `custom_components/normify` into the
Home Assistant configuration directory, restart Home Assistant, and add
**Normify** from **Settings → Devices & services → Add integration**.

## Development

```bash
scripts/develop
```

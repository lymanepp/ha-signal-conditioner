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

- Source entity, of any domain: a sensor, or for example a climate or weather
  entity when conditioning one of its attributes
- Optional source attribute: turn on **Condition an attribute** and choose it
  from the source's attributes on the next step
- Optional output-name override; without one, the name is the source's name
  plus "(conditioned)", so the two are easy to tell apart

Signal Conditioner inherits the unit, device class, state class, and icon whenever a
state-based source provides them, and keeps following the source: if the source
changes its unit (for example °F to °C), the conditioner switches too, restarts
any time window so readings in different units are never averaged, and
publishes the new reading at once. The device class is inherited only together
with the unit, so an explicit unit (say `%` for a calibrated voltage source) is
never paired with the source's device class. A counter's `total` or
`total_increasing` state class is not inherited when calibration or a `mean`
window changes its readings: a calibrated counter can decrease, which long-term
statistics read as a meter reset, and a mean blends readings from before and
after a reset. Set `state_class` explicitly to keep it anyway. A `total` keeps
the source's `last_reset`. The config flow exposes validated
overrides for all four values. Explicit overrides win over inherited metadata;
attribute-based sources inherit none of the parent entity metadata unless an
override is configured. The same fields are available when reconfiguring an
existing Signal Conditioner sensor, and clearing a field removes that override.

A source can have any number of conditioners (for example a 1-minute mean and
a 1-hour mean of the same sensor). A UI-created conditioner is shown on its
source's device page and follows the source when its entity ID is renamed or it
moves to another device. YAML conditioners keep the entity ID written in YAML;
after renaming their source, update the YAML (a Repairs issue reports the
missing source).

`hide_source` hides the source entity in the entity registry (not available with
a source attribute). The source stays hidden across restarts and reloads, and is
unhidden when the conditioner is deleted or disabled, when `hide_source` is
turned off, or when the conditioner switches to a different source. A source
shared by several hiding conditioners stays hidden until none of them hides it,
and a source you hid yourself is never unhidden.

A UI conditioner hides its source once, like Home Assistant's own helpers: if
you unhide the source afterwards, it stays visible. Turning `hide_source` off
and on, changing the source, or disabling and re-enabling the conditioner hides
it again. A YAML conditioner keeps its source hidden, as its YAML says.

Reconfiguring a UI conditioner can also change its source, for example to
replace a failed sensor. The conditioner keeps its entity, entity ID and
history.

### Units of limits and calibration

Limits and calibration points are numbers in one unit: the source's unit when
they were configured (the *input unit*). The config flow records it and shows it
on those steps. YAML can set it with `input_unit: °F`; without it, the unit of
the source's first reading is learned and kept.

If the source later reports another unit (say °C after °F), readings are
converted to the input unit before the limits and calibration and converted back
afterwards, so the output still follows the source's unit and stays correct.
Conversion uses the source's device class, as Home Assistant does: a source
without one can't be converted, because °C and °F are also temperature-difference
units. Readings in a unit that can't be converted are rejected and a Repairs
issue explains the mismatch. Reconfiguring the conditioner, or setting
`input_unit` in YAML, after re-entering the values in the new unit resolves it.

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

- Raw and corrected value pairs, one pair per line: `38.68, 32` or `38.68 32`.
  With decimal commas, use a semicolon or a space between the pair:
  `38,68; 32`.
- Polynomial degree; omitted degree defaults to `1`

### Time window

```yaml
window:
  duration: 60
  output: mean
```

A configured instance publishes once at each interval boundary. Boundaries
follow the local clock and restart at local midnight: a 60-second window ends on
the minute, a 5-minute window at :00, :05, :10, and so on, a 1-hour window on the
hour, and a 1-day window at local midnight. Reloads and restarts don't shift
them; the first interval after one is just shorter. A duration that doesn't
divide the day (say 7 hours) has a shorter last window before midnight. On a
daylight saving day, a window longer than an hour that spans the change is an
hour shorter or longer, so the next ones still end at the usual clock times.
The source is treated as a sample-and-hold signal: each accepted
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
  unavailable). `stale_after` applies to that restored value too. The first live
  reading replaces it at once, and the restored value is not averaged into a
  window. With `follow_source`, a source that is confirmed missing (when its
  Repairs issue is raised, see below) makes the sensor unavailable, so an old
  value never passes for a current one.
- With a source attribute and `follow_source`, the sensor is unknown while the
  attribute is missing.

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

### Rejected readings

A rejected reading keeps the value already in force, which suits an occasional
glitch. When every reading has been rejected for 10 minutes (and at least three
readings), a Repairs issue names the last reading and why it was rejected: for
example a limit set in the wrong unit, or a source that reports text. It clears
at the next accepted reading. The integration's diagnostics download (on the
conditioner's page) shows the accepted and rejected counts and the last
rejection.

Repair issues identify the conditioner and source, describe the action needed,
and clear automatically when the reference is corrected or the conditioner is
unloaded, reconfigured, or deleted. No additional YAML settings are needed.

### Rounding

- Decimal places; an empty `rounding` block defaults to `precision: 2`

Rounding is applied only to the published result. It also sets the sensor's
suggested display precision, so `21.5` with `precision: 2` shows as `21.50`.

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

Each YAML key is validated on its own. At startup, an invalid key is skipped
and logged, and a Repairs issue lists it; its existing sensor, if any, keeps its
previous configuration, and every other conditioner (YAML or UI) loads
normally. A reload with any invalid key changes nothing and reports the error.
The Repairs issue clears once the YAML is valid.

YAML conditioners can't be reconfigured in the UI; edit the YAML instead.
Deleting one in the UI only lasts until the next reload or restart re-imports
it, so remove its YAML key to delete it for good.

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

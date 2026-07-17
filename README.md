# Normify

Normify converts raw Home Assistant sensor values into normalized values suitable
for downstream automations. This first conversion pass preserves the existing
`ha-calibration` polynomial-calibration behavior while replacing the legacy
platform/discovery architecture with config entries, a UI config flow,
reconfiguration support, diagnostics, and independently testable calibration
logic.

Normify remains directly descended from Home Assistant Core's
[Compensation](https://www.home-assistant.io/integrations/compensation/)
integration. Additional conditioning stages are intentionally **not** included
in this pass.

## Changes from ha-calibration

- Repository renamed from `ha-calibration` to `ha-normify`.
- Integration domain changed from `calibration` to `normify`.
- YAML definitions are imported into reloadable config entries.
- New UI setup and reconfigure flows.
- Calibration fitting and evaluation moved to a pure, testable module.
- Explicit rejection of `NaN`, positive infinity, and negative infinity.
- Source metadata inheritance no longer disappears when later updates omit it.
- Unit, config-flow, YAML-import, and entity tests added.
- Modern HACS metadata, translations, diagnostics, Ruff, mypy, pytest, coverage,
  and GitHub Actions configuration.

## Installation

Install as a custom HACS repository or copy `custom_components/normify` into the
Home Assistant configuration directory, then restart Home Assistant.

## YAML migration

Change the top-level domain from `calibration` to `normify`:

```yaml
normify:
  garage_humidity:
    source: sensor.garage_humidity_uncalibrated
    degree: 1
    hide_source: true
    data_points:
      - [38.68, 32.0]
      - [79.89, 75.0]
```

On startup, each YAML object is imported as a Normify config entry. The object ID
becomes the stable config-entry unique ID and the default display name. YAML
remains the source of truth for imported entries: changing it and restarting
updates the existing entry rather than creating a duplicate.

Do not keep the old `calibration:` section active after installing Normify.

## UI configuration

Add **Normify** from **Settings → Devices & services → Add integration**. Enter
calibration points as one `raw, normalized` pair per line:

```text
38.68, 32
79.89, 75
```

Existing UI entries can be edited through **Reconfigure**.

## Behavior retained in this pass

- Polynomial degree 1 through 7.
- Source entity state or one source attribute.
- Configurable precision and metadata.
- Optional source hiding.
- Last valid output is retained when the source becomes `unknown`,
  `unavailable`, or omits the configured attribute.
- A nonnumeric or non-finite source value changes the Normify entity to
  `unknown` and logs a warning.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
ruff check .
ruff format --check .
mypy custom_components/normify
pytest --cov
```

The pure calibration tests can be run without Home Assistant integration setup:

```bash
pytest tests/components/normify/test_calibration.py
```

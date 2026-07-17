#!/usr/bin/env python3
"""Validate the repository metadata required by HACS.

This local validator is intentionally small and deterministic. The official HACS
Action is still executed by CI, but is temporarily non-blocking because of the
upstream manifest-validation regression tracked in hacs/integration#5252.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HACS_JSON = ROOT / "hacs.json"
INTEGRATION_DIR = ROOT / "custom_components" / "normify"
MANIFEST_JSON = INTEGRATION_DIR / "manifest.json"
BRAND_ICON = ROOT / "brand" / "icon.png"


def load_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"Missing required file: {path.relative_to(ROOT)}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise SystemExit(f"Invalid JSON in {path.relative_to(ROOT)}: {err}") from err
    if not isinstance(value, dict):
        raise SystemExit(f"Expected a JSON object in {path.relative_to(ROOT)}")
    return value


def require_keys(data: dict[str, Any], path: Path, keys: set[str]) -> None:
    missing = sorted(keys - data.keys())
    if missing:
        raise SystemExit(
            f"Missing required key(s) in {path.relative_to(ROOT)}: {', '.join(missing)}"
        )


def main() -> None:
    hacs = load_object(HACS_JSON)
    require_keys(hacs, HACS_JSON, {"name"})
    supported_hacs_keys = {
        "name",
        "content_in_root",
        "zip_release",
        "filename",
        "hide_default_branch",
        "country",
        "homeassistant",
        "hacs",
        "persistent_directory",
    }
    unsupported = sorted(hacs.keys() - supported_hacs_keys)
    if unsupported:
        raise SystemExit(f"Unsupported hacs.json key(s): {', '.join(unsupported)}")

    manifest = load_object(MANIFEST_JSON)
    require_keys(
        manifest,
        MANIFEST_JSON,
        {"domain", "documentation", "issue_tracker", "codeowners", "name", "version"},
    )
    if manifest["domain"] != INTEGRATION_DIR.name:
        raise SystemExit(
            "manifest domain must match custom_components directory: "
            f"{manifest['domain']!r} != {INTEGRATION_DIR.name!r}"
        )
    if not isinstance(manifest["codeowners"], list) or not manifest["codeowners"]:
        raise SystemExit("manifest codeowners must be a non-empty list")
    if not BRAND_ICON.is_file():
        raise SystemExit("Missing required brand/icon.png")
    if BRAND_ICON.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit("brand/icon.png is not a valid PNG file")

    print("HACS repository metadata is valid.")


if __name__ == "__main__":
    main()

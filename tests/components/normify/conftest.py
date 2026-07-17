"""Fixtures for Normify tests."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: bool) -> None:
    """Enable loading custom integrations in tests."""

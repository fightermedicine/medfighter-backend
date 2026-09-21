"""Shared test configuration."""

from __future__ import annotations

import os

os.environ.setdefault("FIGHTERS_ENVIRONMENT", "test")
os.environ.setdefault("FIGHTERS_DEBUG", "false")

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

from __future__ import annotations

import json
import os
import stat

import pytest

from mouseion.credentials import CredentialStore


def test_set_and_resolve(tmp_path):
    store = CredentialStore(tmp_path / "credentials.json")
    store.set("FRED_API_KEY", "  abc123  ")

    assert store.resolve("FRED_API_KEY") == "abc123"
    assert store.source("FRED_API_KEY") == "store"
    assert store.configured("FRED_API_KEY") is True


def test_environment_wins_over_the_store(tmp_path, monkeypatch):
    store = CredentialStore(tmp_path / "credentials.json")
    store.set("FRED_API_KEY", "stored")
    monkeypatch.setenv("FRED_API_KEY", "from-env")

    assert store.resolve("FRED_API_KEY") == "from-env"
    assert store.source("FRED_API_KEY") == "env"


def test_delete_removes_only_the_stored_value(tmp_path, monkeypatch):
    store = CredentialStore(tmp_path / "credentials.json")
    store.set("FRED_API_KEY", "stored")
    assert store.delete("FRED_API_KEY") is True
    assert store.source("FRED_API_KEY") is None
    assert store.delete("FRED_API_KEY") is False

    monkeypatch.setenv("FRED_API_KEY", "from-env")
    assert store.source("FRED_API_KEY") == "env"


def test_file_is_private(tmp_path):
    path = tmp_path / "credentials.json"
    CredentialStore(path).set("FRED_API_KEY", "secret")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_values_are_not_exposed_by_the_store_api(tmp_path):
    path = tmp_path / "credentials.json"
    store = CredentialStore(path)
    store.set("FRED_API_KEY", "super-secret")
    # The file holds it; the metadata calls do not.
    assert "super-secret" not in json.dumps({"source": store.source("FRED_API_KEY")})
    assert store.source("FRED_API_KEY") == "store"


@pytest.mark.parametrize("name", ["lowercase", "WITH-DASH", "WITH SPACE", "", "1LEADING"])
def test_invalid_names_are_rejected(tmp_path, name):
    store = CredentialStore(tmp_path / "credentials.json")
    with pytest.raises(ValueError):
        store.set(name, "value")


@pytest.mark.parametrize("value", ["", "   ", "x" * 5000])
def test_invalid_values_are_rejected(tmp_path, value):
    store = CredentialStore(tmp_path / "credentials.json")
    with pytest.raises(ValueError):
        store.set("FRED_API_KEY", value)


def test_a_corrupt_store_reads_as_empty(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text("{not json", encoding="utf-8")
    store = CredentialStore(path)
    assert store.resolve("FRED_API_KEY") is None
    store.set("FRED_API_KEY", "fresh")
    assert store.resolve("FRED_API_KEY") == "fresh"

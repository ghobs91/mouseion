from __future__ import annotations

import pytest

from mouseion.favicons import (
    FaviconStore,
    icon_href,
    is_public_host,
    monogram_svg,
    normalize_host,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"payload"


def test_normalize_host_accepts_urls_and_hosts():
    assert normalize_host("https://data.ny.gov/d/e9ss-239a") == "data.ny.gov"
    assert normalize_host("DATA.NY.GOV") == "data.ny.gov"
    assert normalize_host("data.ny.gov:443") == "data.ny.gov"
    assert normalize_host("localhost") is None
    assert normalize_host("") is None
    assert normalize_host(None) is None


def test_private_addresses_are_rejected():
    assert is_public_host("127.0.0.1") is False
    assert is_public_host("localhost") is False
    assert is_public_host("10.0.0.1") is False
    assert is_public_host("169.254.169.254") is False


def test_icon_href_resolves_the_first_icon_link():
    page = (
        '<link rel="stylesheet" href="/site.css">'
        '<link rel="icon" href="/assets/icon.png">'
        '<link rel="apple-touch-icon" href="/touch.png">'
    )
    assert icon_href(page, "https://example.gov/") == "https://example.gov/assets/icon.png"


def test_icon_href_handles_absolute_and_escaped_urls():
    page = '<link rel="shortcut icon" href="https://cdn.example.gov/i.ico?a=1&amp;b=2">'
    assert icon_href(page, "https://example.gov/") == "https://cdn.example.gov/i.ico?a=1&b=2"


def test_icon_href_returns_none_without_an_icon():
    assert icon_href("<link rel='stylesheet' href='/x.css'>", "https://example.gov/") is None


def test_monogram_is_a_deterministic_svg():
    first = monogram_svg("Data NY")
    second = monogram_svg("Data NY")
    assert first.startswith(b"<svg")
    assert first == second
    assert b">DN<" in first


def test_store_fetches_once_and_caches(tmp_path):
    calls: list[str] = []

    def fetcher(host: str):
        calls.append(host)
        return PNG, "image/png"

    store = FaviconStore({"example.gov"}, tmp_path, fetcher=fetcher)
    data, content_type = store.get("example.gov", "Example")

    assert data == PNG
    assert content_type == "image/png"
    store.get("example.gov", "Example")
    assert calls == ["example.gov"]


def test_store_refuses_hosts_outside_the_allowlist(tmp_path):
    store = FaviconStore({"example.gov"}, tmp_path, fetcher=lambda host: None)
    with pytest.raises(KeyError):
        store.get("evil.test", "Evil")


def test_store_falls_back_to_a_monogram(tmp_path):
    store = FaviconStore({"example.gov"}, tmp_path, fetcher=lambda host: None)
    data, content_type = store.get("example.gov", "Example")

    assert content_type == "image/svg+xml"
    assert data.startswith(b"<svg")


def test_store_does_not_cache_a_miss_as_a_real_icon(tmp_path):
    store = FaviconStore({"example.gov"}, tmp_path, fetcher=lambda host: None)
    store.get("example.gov", "Example")
    assert list(tmp_path.glob("*.bin")) == []

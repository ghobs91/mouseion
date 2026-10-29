from __future__ import annotations

from mouseion.cache import Cache, cache_key


def test_roundtrip(tmp_path):
    cache = Cache(tmp_path / "c.db")
    key = cache_key("demo", "search", {"q": "x"}, None)
    cache.put(
        key, provider="demo", capability="search", params={"q": "x"}, status=200, body={"a": 1}, ttl=60
    )
    hit = cache.get(key)
    assert hit is not None
    assert hit.body == {"a": 1}
    assert hit.status == 200
    cache.close()


def test_expiry(tmp_path):
    cache = Cache(tmp_path / "c.db")
    key = cache_key("demo", "search", {}, None)
    cache.put(key, provider="demo", capability="search", params={}, status=200, body={}, ttl=-1)
    assert cache.get(key) is None
    cache.close()


def test_key_varies_with_cursor_and_params():
    a = cache_key("demo", "s", {"q": "x"}, None)
    b = cache_key("demo", "s", {"q": "x"}, "2")
    c = cache_key("demo", "s", {"q": "y"}, None)
    assert len({a, b, c}) == 3


def test_stats_group_by_provider(tmp_path):
    cache = Cache(tmp_path / "c.db")
    for i in range(2):
        cache.put(
            cache_key("demo", "s", {"i": i}, None),
            provider="demo",
            capability="s",
            params={"i": i},
            status=200,
            body={},
            ttl=60,
        )
    assert cache.stats() == {"demo": 2}
    cache.close()

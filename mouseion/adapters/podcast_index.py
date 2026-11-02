"""Podcast Index adapter.

The API authenticates with an Amazon-style signed request rather than a bearer
token: ``Authorization`` is the SHA-1 of the API key, the secret and a Unix
timestamp, sent alongside ``X-Auth-Key`` and ``X-Auth-Date``. Both values must
travel in headers, so the signing happens here rather than in a manifest.
"""

from __future__ import annotations

import hashlib
import time

from ..config import Settings
from ..credentials import resolve
from ..envelope import ProviderError
from ..manifest import Manifest
from .rest_json import RestJsonAdapter


class PodcastIndexAdapter(RestJsonAdapter):
    name = "podcast_index"

    def headers(
        self, manifest: Manifest, config: Settings | None = None
    ) -> dict[str, str]:
        headers = super().headers(manifest, config)
        key = resolve(manifest.auth.env)
        secret = resolve("PODCAST_INDEX_SECRET")
        if not key or not secret:
            raise ProviderError(
                "provider 'podcast_index' requires API credentials",
                hint=(
                    "set PODCAST_INDEX_KEY and PODCAST_INDEX_SECRET in the environment "
                    "or save them in the dashboard's Credentials tab"
                ),
            )
        stamp = str(int(time.time()))
        headers["X-Auth-Key"] = key
        headers["X-Auth-Date"] = stamp
        headers["Authorization"] = hashlib.sha1(
            f"{key}{secret}{stamp}".encode()
        ).hexdigest()
        return headers

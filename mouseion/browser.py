"""Browser transport: drive a real Chrome window and intercept the page's API call.

Bot-protected sites such as Costco reject a separate in-page ``fetch`` even when
the page itself succeeds: the site's own request carries a freshly minted
per-request token. The reliable path is therefore to open the human-facing page,
let its JavaScript make the search request, and keep the 200 response.

Patchright patches the Playwright driver's CDP leaks. It still needs a real,
headed Chrome: headless Chromium is detected before the page's request is sent.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from .config import Settings
from .envelope import ProviderError
from .manifest import Capability, Manifest

log = logging.getLogger(__name__)


class BrowserClient:
    """One serialized headful browser session per provider call."""

    def __init__(self, config: Settings):
        self.config = config
        self._lock = asyncio.Lock()

    async def fetch(
        self, manifest: Manifest, prepared: Any, capability: Capability
    ) -> Any:
        if not prepared.browser_url:
            raise ProviderError(
                f"provider '{manifest.id}' has no browser_url",
                hint="declare capability.browser_url for transport=browser",
            )
        try:
            from patchright.async_api import async_playwright
        except ImportError:
            raise ProviderError(
                "the browser transport needs Patchright",
                hint=(
                    "install the optional extra with `uv sync --extra browser`, "
                    "then `patchright install chromium` (or install Google Chrome)"
                ),
            ) from None

        async with self._lock, async_playwright() as pw:
            context = await self._launch(pw, manifest)
            page = context.pages[0] if context.pages else await context.new_page()
            timeout_ms = int(self.config.browser_timeout_s * 1000)
            match = capability.browser_response_match or "/api/"

            def wanted(response: Any) -> bool:
                return (
                    match in response.url
                    and response.request.method.upper() == "POST"
                    and response.status == 200
                )

            try:
                async with page.expect_response(wanted, timeout=timeout_ms) as info:
                    await page.goto(
                        prepared.browser_url,
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                response = await info.value
                prepared.browser_meta["page_url"] = page.url
                with contextlib.suppress(Exception):
                    body = response.request.post_data_json
                    if isinstance(body, dict):
                        prepared.browser_meta.update(body)
                return await response.json()
            except Exception as exc:  # noqa: BLE001 - surfaced with a hint
                raise ProviderError(
                    f"{manifest.id} browser request failed: {type(exc).__name__}",
                    hint=(
                        "the site may be showing a challenge; retry once, or run "
                        "with a visible window (MOUSEION_BROWSER_OFFSCREEN=0) and "
                        "complete it manually"
                    ),
                ) from None
            finally:
                await context.close()

    async def _launch(self, pw: Any, manifest: Manifest) -> Any:
        profile = Path(self.config.browser_profile_dir) / manifest.id
        profile.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {
            "user_data_dir": str(profile),
            "headless": self.config.browser_headless,
            "viewport": None,
            "locale": "en-US",
        }
        if not self.config.browser_headless and self.config.browser_offscreen:
            kwargs["args"] = ["--window-position=-2000,-2000", "--window-size=1280,900"]

        channel = self.config.browser_channel.strip()
        if channel:
            try:
                return await pw.chromium.launch_persistent_context(
                    channel=channel, **kwargs
                )
            except Exception as exc:  # noqa: BLE001 - fall back to Patchright's Chromium
                log.debug("browser channel %r failed, trying bundled Chromium: %s", channel, exc)
        try:
            return await pw.chromium.launch_persistent_context(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                f"could not launch a browser for '{manifest.id}': {exc}",
                hint=(
                    "install Google Chrome, or run `patchright install chromium` "
                    "for the bundled fallback"
                ),
            ) from None

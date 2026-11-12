"""Request signing for providers that authenticate each call individually.

Walmart's affiliate API signs a small string with an RSA private key and sends the
signature plus a timestamp in headers. It is close to, but not the same as, an API
key: there is no token to cache, and the timestamp makes every request unique.
"""

from __future__ import annotations

import base64
import time
import uuid

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .envelope import ProviderError


def _private_key_bytes(value: str) -> bytes:
    """Accept a PEM body or the base64-wrapped PEM operators tend to paste."""
    text = value.strip()
    if "BEGIN" in text:
        return text.encode("utf-8")
    try:
        decoded = base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        raise ProviderError(
            "signed-provider private key is neither PEM nor base64-encoded PEM"
        ) from None
    if b"BEGIN" not in decoded:
        raise ProviderError("decoded private key does not contain a PEM header")
    return decoded


def walmart_headers(
    *,
    consumer_id: str,
    private_key: str,
    key_version: str,
    timestamp: str | None = None,
) -> dict[str, str]:
    """Build the WM_SEC headers for one Walmart affiliate API request."""
    stamp = timestamp or str(int(time.time() * 1000))
    string_to_sign = f"{consumer_id}\n{stamp}\n{key_version}\n"
    try:
        key = serialization.load_pem_private_key(_private_key_bytes(private_key), password=None)
        signature = key.sign(
            string_to_sign.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256()
        )
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced with the env name by the caller
        raise ProviderError(f"could not sign the Walmart request: {exc}") from None

    return {
        "WM_CONSUMER.ID": consumer_id,
        "WM_CONSUMER.INTIMESTAMP": stamp,
        "WM_SEC.KEY_VERSION": key_version,
        "WM_SEC.AUTH_SIGNATURE": base64.b64encode(signature).decode("ascii"),
        "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
    }

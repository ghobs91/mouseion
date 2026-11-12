from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from mouseion.envelope import ProviderError
from mouseion.signing import walmart_headers


def _verify(private_pem: str, message: str, signature_b64: str) -> None:
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    key.public_key().verify(
        base64.b64decode(signature_b64),
        message.encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_walmart_headers_sign_the_documented_string(walmart_private_key):
    headers = walmart_headers(
        consumer_id="consumer-123",
        private_key=walmart_private_key,
        key_version="1",
        timestamp="1762100000000",
    )

    assert headers["WM_CONSUMER.ID"] == "consumer-123"
    assert headers["WM_CONSUMER.INTIMESTAMP"] == "1762100000000"
    assert headers["WM_SEC.KEY_VERSION"] == "1"
    assert headers["WM_QOS.CORRELATION_ID"]
    _verify(
        walmart_private_key,
        "consumer-123\n1762100000000\n1\n",
        headers["WM_SEC.AUTH_SIGNATURE"],
    )


def test_walmart_headers_accept_a_base64_wrapped_key(walmart_private_key):
    wrapped = base64.b64encode(walmart_private_key.encode()).decode()
    headers = walmart_headers(
        consumer_id="consumer-123",
        private_key=wrapped,
        key_version="1",
        timestamp="1762100000000",
    )
    _verify(
        walmart_private_key,
        "consumer-123\n1762100000000\n1\n",
        headers["WM_SEC.AUTH_SIGNATURE"],
    )


def test_walmart_headers_reject_a_non_key():
    with pytest.raises(ProviderError):
        walmart_headers(
            consumer_id="consumer-123",
            private_key="not-a-key",
            key_version="1",
        )

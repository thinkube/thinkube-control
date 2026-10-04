#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for verify_token, which checks Keycloak access tokens.

Keycloak signs access tokens with RS256. verify_token accepts a token only when
its signature matches the realm's public key, its issuer is the realm URL and
it has not expired; anything else is a 401.
"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app.core import security
from app.core.config import settings

ISSUER = f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}"


def _key_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return private_key, public_pem


REALM_KEY, REALM_PUBLIC_PEM = _key_pair()
OTHER_KEY, _ = _key_pair()


def _token(key=REALM_KEY, issuer=ISSUER, expires_in=300, algorithm="RS256"):
    now = int(time.time())
    claims = {
        "sub": "user-1",
        "preferred_username": "tester",
        "aud": "account",
        "iss": issuer,
        "iat": now,
        "exp": now + expires_in,
    }
    return jwt.encode(claims, key, algorithm=algorithm)


@pytest.fixture(autouse=True)
def realm_public_key(monkeypatch):
    async def fake_public_key():
        return REALM_PUBLIC_PEM

    monkeypatch.setattr(security, "get_keycloak_public_key", fake_public_key)


@pytest.mark.asyncio
async def test_valid_token_returns_claims():
    payload = await security.verify_token(_token())
    assert payload["preferred_username"] == "tester"
    assert payload["iss"] == ISSUER


@pytest.mark.asyncio
async def test_audience_is_not_checked():
    payload = await security.verify_token(_token())
    assert payload["aud"] == "account"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    [
        pytest.param(lambda: _token(key=OTHER_KEY), id="wrong-signing-key"),
        pytest.param(lambda: _token(issuer="https://evil.example/realms/x"), id="wrong-issuer"),
        pytest.param(lambda: _token(expires_in=-60), id="expired"),
        pytest.param(lambda: _token(key="a-shared-secret-of-at-least-32-bytes", algorithm="HS256"), id="hs256"),
        pytest.param(lambda: "not-a-jwt", id="malformed"),
    ],
)
async def test_rejected_tokens_are_401(token):
    with pytest.raises(HTTPException) as excinfo:
        await security.verify_token(token())
    assert excinfo.value.status_code == 401

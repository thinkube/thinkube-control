#!/usr/bin/env python3
"""The Secrets store has one key, and without it the backend does not start.

The module is loaded from its file so that importing it runs the singleton
exactly as the backend does, without importing the rest of the application.
"""

import importlib.util
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

MODULE = Path(__file__).resolve().parents[1] / "app/services/secrets_service.py"


def load(monkeypatch, key=None, **extra_env):
    monkeypatch.delenv("THINKUBE_ENCRYPTION_KEY", raising=False)
    if key is not None:
        monkeypatch.setenv("THINKUBE_ENCRYPTION_KEY", key)
    for name, value in extra_env.items():
        monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location("secrets_service_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_without_a_key_the_service_does_not_start(monkeypatch):
    with pytest.raises(RuntimeError, match="thinkube-encryption-key"):
        load(monkeypatch)


def test_the_old_password_and_salt_do_not_stand_in_for_a_key(monkeypatch):
    with pytest.raises(RuntimeError, match="THINKUBE_ENCRYPTION_KEY is not set"):
        load(monkeypatch, ENCRYPTION_PASSWORD="anything", ENCRYPTION_SALT="anything")


def test_a_key_that_is_not_a_fernet_key_is_refused(monkeypatch):
    with pytest.raises(RuntimeError, match="not a valid Fernet key"):
        load(monkeypatch, key="not-a-fernet-key")


def test_values_round_trip_with_the_configured_key(monkeypatch):
    module = load(monkeypatch, key=Fernet.generate_key().decode())
    token = module.secrets_service.encrypt("hf_abc123")
    assert token != "hf_abc123"
    assert module.secrets_service.decrypt(token) == "hf_abc123"


def test_a_value_encrypted_under_another_key_is_refused(monkeypatch):
    other = Fernet(Fernet.generate_key()).encrypt(b"hf_abc123").decode()
    module = load(monkeypatch, key=Fernet.generate_key().decode())
    with pytest.raises(ValueError):
        module.secrets_service.decrypt(other)


def test_no_key_is_derived_from_constants_in_the_source():
    source = MODULE.read_text()
    for leftover in ("thinkube-secret-key", "thinkube-salt", "PBKDF2", "ENCRYPTION_PASSWORD"):
        assert leftover not in source, leftover

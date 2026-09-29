"""Rate-limit key policy: stable peers, hashed accounts, user+IP short windows (§37)."""

from uuid import uuid4

import pytest

from doculens.application.ratelimit import (
    auth_account_key,
    auth_address_key,
    enforce_keys,
    normalize_client_address,
    user_and_ip_keys,
    user_only_keys,
)
from doculens.domain.errors import RateLimitedError
from doculens.infrastructure.ratelimit import InMemoryRateLimiter

pytestmark = pytest.mark.unit


def test_normalize_client_address_rejects_spoof_shaped_values() -> None:
    assert normalize_client_address(" 10.0.0.1 ") == "10.0.0.1"
    assert normalize_client_address("2001:DB8::1") == "2001:db8::1"
    assert normalize_client_address("10.0.0.1, 1.2.3.4") == "unknown"
    assert normalize_client_address("evil\nhost") == "unknown"
    assert normalize_client_address(None) == "unknown"
    assert normalize_client_address("") == "unknown"


def test_auth_account_key_is_stable_across_trivial_email_variations() -> None:
    assert auth_account_key("Victim@Example.com") == auth_account_key("  victim@example.com  ")
    assert auth_account_key("a@example.com") != auth_account_key("b@example.com")
    assert "@" not in auth_account_key("victim@example.com")


def test_auth_address_key_scopes_by_action_and_normalised_peer() -> None:
    assert auth_address_key("login", "10.0.0.1") == "auth:login:10.0.0.1"
    assert auth_address_key("login", " 10.0.0.1 ") == auth_address_key("login", "10.0.0.1")
    assert auth_address_key("register", "10.0.0.1") != auth_address_key("login", "10.0.0.1")


def test_user_and_ip_keys_cover_both_identities() -> None:
    user_id = uuid4()
    keys = user_and_ip_keys("ask", user_id=user_id, client="10.1.2.3")
    assert keys == (f"ask:user:{user_id}", "ask:ip:10.1.2.3")
    assert user_only_keys("ask:daily", user_id=user_id) == (f"ask:daily:user:{user_id}",)


async def test_enforce_keys_stops_on_the_first_exhausted_budget() -> None:
    limiter = InMemoryRateLimiter()
    await enforce_keys(limiter, ["a"], limit=1, window_seconds=60)
    with pytest.raises(RateLimitedError):
        await enforce_keys(limiter, ["a", "b"], limit=1, window_seconds=60)
    # Sibling key still has budget until it is hit on its own.
    await enforce_keys(limiter, ["b"], limit=1, window_seconds=60)

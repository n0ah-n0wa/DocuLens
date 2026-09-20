import pytest

from doculens.infrastructure.security.passwords import Argon2PasswordHasher

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def hasher() -> Argon2PasswordHasher:
    return Argon2PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)


def test_hashes_are_argon2id_salted_and_never_the_password(hasher: Argon2PasswordHasher) -> None:
    first = hasher.hash("correct horse battery")
    second = hasher.hash("correct horse battery")

    assert first.startswith("$argon2id$")
    assert "correct horse battery" not in first
    assert first != second


def test_verify_accepts_the_right_password_only(hasher: Argon2PasswordHasher) -> None:
    password_hash = hasher.hash("correct horse battery")

    assert hasher.verify(password_hash, "correct horse battery") is True
    assert hasher.verify(password_hash, "correct horse batter") is False
    assert hasher.verify("not-a-hash", "correct horse battery") is False


def test_rehash_is_requested_when_parameters_change(hasher: Argon2PasswordHasher) -> None:
    password_hash = hasher.hash("correct horse battery")

    assert hasher.needs_rehash(password_hash) is False
    assert Argon2PasswordHasher(time_cost=2, memory_cost=8192, parallelism=1).needs_rehash(
        password_hash
    )
    assert hasher.needs_rehash("garbage") is True

"""Argon2id password hashing (SPECIFICATIONS.md §8) via argon2-cffi."""

from argon2 import PasswordHasher as Argon2
from argon2 import Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


class Argon2PasswordHasher:
    """Argon2id with the argon2-cffi defaults (t=3, m=64 MiB, p=4) unless overridden.

    Lower parameters exist for test speed only; production keeps the defaults.
    """

    def __init__(
        self, *, time_cost: int = 3, memory_cost: int = 65536, parallelism: int = 4
    ) -> None:
        self._impl = Argon2(
            time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism, type=Type.ID
        )

    def hash(self, password: str) -> str:
        return self._impl.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._impl.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._impl.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True

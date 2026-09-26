"""Redis-backed local job queue (OQ-13, SPECIFICATIONS.md §47, §48).

Keys (all prefixed by the queue name):

- ``ready`` — ZSET scored by unix time when the job becomes claimable (includes delayed retries)
- ``lease:{receipt}`` — HASH holding the leased payload until ack, retry, dead-letter or expiry
- ``leases`` — ZSET of receipt → lease expiry for reclaim sweeps
- ``dlq`` — LIST of dead-lettered JSON payloads (newest at the head)

Claim and reclaim use Lua so two workers cannot split a lease or lose a member between pop and
lease write.
"""

from __future__ import annotations

import json
from collections.abc import Mapping  # noqa: TC003
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from redis.asyncio import Redis

from doculens.application.jobs import with_next_attempt
from doculens.domain.jobs import (
    ClaimedJob,
    DeadLetterRecord,
    ProcessingJob,
    QueueUnavailableError,
)
from doculens.domain.time import utc_now

# Atomically: if the earliest ready member is due, pop it and create a lease.
_CLAIM_LUA = """
local ready = KEYS[1]
local leases = KEYS[2]
local lease_key = KEYS[3]
local now = tonumber(ARGV[1])
local expires = tonumber(ARGV[2])
local receipt = ARGV[3]
local items = redis.call('ZRANGE', ready, 0, 0, 'WITHSCORES')
if (#items == 0) then
  return nil
end
local member = items[1]
local score = tonumber(items[2])
if (score > now) then
  return nil
end
redis.call('ZREM', ready, member)
redis.call('HSET', lease_key, 'payload', member, 'available_at', tostring(score))
redis.call('ZADD', leases, expires, receipt)
return {member, tostring(score)}
"""

# Atomically take ownership of an expired lease and re-queue with attempt+1.
_RECLAIM_LUA = """
local leases = KEYS[1]
local ready = KEYS[2]
local lease_key = KEYS[3]
local now = tonumber(ARGV[1])
local receipt = ARGV[2]
local removed = redis.call('ZREM', leases, receipt)
if (removed == 0) then
  return nil
end
local payload = redis.call('HGET', lease_key, 'payload')
redis.call('DEL', lease_key)
if (not payload) then
  return nil
end
return payload
"""


def _to_unix(moment: datetime) -> float:
    return moment.astimezone(UTC).timestamp()


def _from_unix(value: float) -> datetime:
    return datetime.fromtimestamp(value, tz=UTC)


def _job_payload(job: ProcessingJob) -> dict[str, Any]:
    return {
        "document_id": str(job.document_id),
        "job_id": job.job_id,
        "attempt": job.attempt,
        "enqueued_at": job.enqueued_at.astimezone(UTC).isoformat(),
        "request_id": job.request_id,
    }


def _job_from_payload(payload: Mapping[str, Any]) -> ProcessingJob:
    return ProcessingJob(
        document_id=UUID(str(payload["document_id"])),
        job_id=str(payload["job_id"]),
        attempt=int(payload["attempt"]),
        enqueued_at=datetime.fromisoformat(str(payload["enqueued_at"])),
        request_id=str(payload["request_id"]) if payload.get("request_id") else None,
    )


class RedisJobQueue:
    def __init__(self, client: Redis, *, name: str = "doculens-documents") -> None:
        self._redis: Any = client
        self._ready = f"{name}:ready"
        self._leases = f"{name}:leases"
        self._lease_prefix = f"{name}:lease:"
        self._dlq = f"{name}:dlq"
        self._name = name
        self._claim_script = self._redis.register_script(_CLAIM_LUA)
        self._reclaim_script = self._redis.register_script(_RECLAIM_LUA)

    @classmethod
    def from_url(cls, url: str, *, name: str = "doculens-documents") -> RedisJobQueue:
        return cls(Redis.from_url(url, decode_responses=True), name=name)

    async def close(self) -> None:
        await self._redis.aclose()

    async def enqueue(self, job: ProcessingJob) -> None:
        try:
            await self._redis.zadd(self._ready, {_encode_member(job): _to_unix(job.enqueued_at)})
        except Exception as exc:
            raise QueueUnavailableError from exc

    async def claim(self, *, visibility_timeout_seconds: float) -> ClaimedJob | None:
        try:
            await self._reclaim_expired()
            now = utc_now()
            while True:
                receipt = str(uuid4())
                result = await self._claim_script(
                    keys=[self._ready, self._leases, self._lease_key(receipt)],
                    args=[
                        _to_unix(now),
                        _to_unix(now) + visibility_timeout_seconds,
                        receipt,
                    ],
                )
                if result is None:
                    return None
                member, score = str(result[0]), float(result[1])
                try:
                    job = _decode_member(member)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    await self._dead_letter_raw(member, error=f"POISON_JOB: {exc}")
                    continue
                return ClaimedJob(receipt=receipt, job=job, available_at=_from_unix(float(score)))
        except QueueUnavailableError:
            raise
        except Exception as exc:
            raise QueueUnavailableError from exc

    async def acknowledge(self, claim: ClaimedJob) -> None:
        try:
            pipe = self._redis.pipeline()
            pipe.delete(self._lease_key(claim.receipt))
            pipe.zrem(self._leases, claim.receipt)
            await pipe.execute()
        except Exception as exc:
            raise QueueUnavailableError from exc

    async def retry(
        self, claim: ClaimedJob, *, delay_seconds: float, error: str
    ) -> ClaimedJob | None:
        del error
        try:
            if not await self._redis.exists(self._lease_key(claim.receipt)):
                return None
            now = utc_now()
            nxt = with_next_attempt(claim.job, now=now)
            available = _to_unix(now) + max(0.0, delay_seconds)
            pipe = self._redis.pipeline()
            pipe.delete(self._lease_key(claim.receipt))
            pipe.zrem(self._leases, claim.receipt)
            pipe.zadd(self._ready, {_encode_member(nxt): available})
            await pipe.execute()
            return ClaimedJob(receipt=claim.receipt, job=nxt, available_at=_from_unix(available))
        except Exception as exc:
            raise QueueUnavailableError from exc

    async def dead_letter(self, claim: ClaimedJob, *, error: str) -> None:
        try:
            record = {
                "job": _job_payload(claim.job),
                "error": error,
                "dead_lettered_at": utc_now().astimezone(UTC).isoformat(),
            }
            pipe = self._redis.pipeline()
            pipe.lpush(self._dlq, json.dumps(record))
            pipe.delete(self._lease_key(claim.receipt))
            pipe.zrem(self._leases, claim.receipt)
            await pipe.execute()
        except Exception as exc:
            raise QueueUnavailableError from exc

    async def list_dead_letters(self, *, limit: int = 100) -> list[DeadLetterRecord]:
        try:
            raw = cast("list[str]", await self._redis.lrange(self._dlq, 0, max(0, limit - 1)))
        except Exception as exc:
            raise QueueUnavailableError from exc
        records: list[DeadLetterRecord] = []
        for item in raw:
            try:
                payload = json.loads(item)
                job_payload = payload["job"]
                if "document_id" not in job_payload:
                    continue  # poison / raw entries stay in Redis for operators
                records.append(
                    DeadLetterRecord(
                        job=_job_from_payload(job_payload),
                        error=str(payload["error"]),
                        dead_lettered_at=datetime.fromisoformat(str(payload["dead_lettered_at"])),
                    )
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return records

    async def check(self) -> None:
        await self._redis.ping()

    def _lease_key(self, receipt: str) -> str:
        return f"{self._lease_prefix}{receipt}"

    async def _dead_letter_raw(self, raw_member: str, *, error: str) -> None:
        record = {
            "job": {"raw": raw_member},
            "error": error,
            "dead_lettered_at": utc_now().astimezone(UTC).isoformat(),
        }
        await self._redis.lpush(self._dlq, json.dumps(record))

    async def _reclaim_expired(self) -> None:
        now = _to_unix(utc_now())
        expired = await self._redis.zrangebyscore(self._leases, min="-inf", max=now)
        for receipt in expired:
            payload = await self._reclaim_script(
                keys=[self._leases, self._ready, self._lease_key(str(receipt))],
                args=[now, str(receipt)],
            )
            if payload is None:
                continue
            try:
                job = _decode_member(str(payload))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                await self._dead_letter_raw(str(payload), error=f"POISON_JOB: {exc}")
                continue
            bumped = with_next_attempt(job, now=utc_now())
            await self._redis.zadd(self._ready, {_encode_member(bumped): now})


def _encode_member(job: ProcessingJob) -> str:
    return json.dumps(_job_payload(job), separators=(",", ":"), sort_keys=True)


def _decode_member(member: str) -> ProcessingJob:
    return _job_from_payload(json.loads(member))


__all__ = ["RedisJobQueue"]

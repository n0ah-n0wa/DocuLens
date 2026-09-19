"""Readiness reporting: the application-level view of whether dependencies are usable.

Infrastructure adapters implement :class:`HealthProbe` for the dependency they wrap (PostgreSQL,
Redis, the vector store, ...). :class:`ReadinessService` runs every probe concurrently, bounds each
one with a timeout, and never raises: a broken dependency must degrade readiness, not crash the
process (SPECIFICATIONS.md §67).
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class HealthProbe(Protocol):
    """Port implemented by adapters that can verify their dependency is reachable and usable."""

    @property
    def name(self) -> str:
        """Stable identifier of the dependency, e.g. ``postgres``."""

    async def check(self) -> None:
        """Return normally when the dependency is usable; raise any exception otherwise."""


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Outcome of one probe. ``detail`` is a short, non-sensitive summary of a failure."""

    name: str
    healthy: bool
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    results: tuple[ProbeResult, ...]

    @property
    def ready(self) -> bool:
        return all(result.healthy for result in self.results)


class ReadinessService:
    """Runs the registered probes and summarises the outcome."""

    def __init__(self, probes: Sequence[HealthProbe], *, timeout_seconds: float = 2.0) -> None:
        self._probes = tuple(probes)
        self._timeout_seconds = timeout_seconds

    async def check(self) -> ReadinessReport:
        results = await asyncio.gather(*(self._run(probe) for probe in self._probes))
        return ReadinessReport(results=tuple(results))

    async def _run(self, probe: HealthProbe) -> ProbeResult:
        try:
            await asyncio.wait_for(probe.check(), timeout=self._timeout_seconds)
        except TimeoutError:
            return ProbeResult(name=probe.name, healthy=False, detail="timed out")
        except Exception:  # noqa: BLE001 - a failing probe must degrade readiness, never raise
            return ProbeResult(name=probe.name, healthy=False, detail="failed")
        return ProbeResult(name=probe.name, healthy=True)

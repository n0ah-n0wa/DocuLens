import asyncio

import pytest

from doculens.application.health import ProbeResult, ReadinessService

pytestmark = pytest.mark.unit


class HealthyProbe:
    name = "healthy"

    async def check(self) -> None:
        return None


class FailingProbe:
    name = "failing"

    async def check(self) -> None:
        message = "connection refused: secret-host:5432"
        raise ConnectionError(message)


class SlowProbe:
    name = "slow"

    async def check(self) -> None:
        await asyncio.sleep(10)


async def test_no_probes_means_ready() -> None:
    report = await ReadinessService([]).check()

    assert report.ready is True
    assert report.results == ()


async def test_all_healthy_probes_report_ready() -> None:
    report = await ReadinessService([HealthyProbe(), HealthyProbe()]).check()

    assert report.ready is True
    assert all(result.healthy for result in report.results)


async def test_a_raising_probe_degrades_readiness_without_leaking_details() -> None:
    report = await ReadinessService([HealthyProbe(), FailingProbe()]).check()

    assert report.ready is False
    assert report.results[1] == ProbeResult(name="failing", healthy=False, detail="failed")
    assert "secret-host" not in repr(report)


async def test_a_slow_probe_is_bounded_by_the_timeout() -> None:
    report = await ReadinessService([SlowProbe()], timeout_seconds=0.01).check()

    assert report.ready is False
    assert report.results == (ProbeResult(name="slow", healthy=False, detail="timed out"),)

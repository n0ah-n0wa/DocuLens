"""Per-user quotas: available, exceeded, concurrent, retries, deletion release (§38-§39)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from doculens.application.quotas import QuotaService
from doculens.domain.documents import ProcessingStatus
from doculens.domain.ingestion import DocumentLimitReachedError
from doculens.domain.quotas import (
    AiCostQuotaExceededError,
    AiPricing,
    ModelPrices,
    PagesQuotaExceededError,
    QuestionsQuotaExceededError,
    QuotaLimits,
    StorageQuotaExceededError,
    embedding_cost_micros,
    llm_cost_micros,
    parse_ai_pricing,
)
from doculens.testing.factories import Factories
from doculens.testing.fakes import InMemoryStore, InMemoryUnitOfWork

if TYPE_CHECKING:
    from uuid import UUID

pytestmark = pytest.mark.unit

_NO_COST = Decimal(0)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def store() -> InMemoryStore:
    store = InMemoryStore()
    owner = Factories.user()
    store.users[owner.id] = owner
    return store


@pytest.fixture
def owner_id(store: InMemoryStore) -> UUID:
    return next(iter(store.users))


def _quotas(
    store: InMemoryStore,
    *,
    limits: QuotaLimits | None = None,
    clock: Clock | None = None,
    pricing: AiPricing | None = None,
) -> QuotaService:
    return QuotaService(
        unit_of_work=lambda: InMemoryUnitOfWork(store),
        limits=limits
        or QuotaLimits(
            max_documents=2,
            max_storage_bytes=10_000,
            max_pages_total=20,
            max_questions_per_day=3,
            max_ai_cost_usd_per_day=Decimal("1.00"),
        ),
        pricing=pricing,
        clock=clock or Clock(),
    )


def _daily(*, questions: int) -> QuotaLimits:
    return QuotaLimits(max_questions_per_day=questions, max_ai_cost_usd_per_day=_NO_COST)


def _document(
    store: InMemoryStore,
    owner_id: UUID,
    *,
    size: int = 100,
    pages: int | None = 5,
    status: ProcessingStatus = ProcessingStatus.READY,
) -> UUID:
    document = Factories.document(owner_id)
    store.documents[document.id] = replace(
        document,
        file_size=size,
        page_count=pages,
        processing_status=status,
    )
    return document.id


async def test_upload_is_allowed_under_document_and_storage_quotas(
    store: InMemoryStore, owner_id: UUID
) -> None:
    quotas = _quotas(store)
    _document(store, owner_id, size=1_000, pages=2)

    await quotas.ensure_upload_allowed(owner_id, file_size=500)
    usage = await quotas.document_usage(owner_id)
    assert usage.documents == 1
    assert usage.storage_bytes == 1_000


async def test_document_quota_exceeded(store: InMemoryStore, owner_id: UUID) -> None:
    quotas = _quotas(store)
    _document(store, owner_id, size=10)
    _document(store, owner_id, size=10)

    with pytest.raises(DocumentLimitReachedError):
        await quotas.ensure_upload_allowed(owner_id, file_size=1)


async def test_storage_quota_exceeded(store: InMemoryStore, owner_id: UUID) -> None:
    quotas = _quotas(store, limits=QuotaLimits(max_documents=10, max_storage_bytes=1_000))
    _document(store, owner_id, size=800)

    with pytest.raises(StorageQuotaExceededError):
        await quotas.ensure_upload_allowed(owner_id, file_size=300)


async def test_pages_quota_exceeded(store: InMemoryStore, owner_id: UUID) -> None:
    quotas = _quotas(store, limits=QuotaLimits(max_pages_total=10))
    _document(store, owner_id, pages=8)

    with pytest.raises(PagesQuotaExceededError):
        await quotas.ensure_pages_allowed(owner_id, additional_pages=5)


async def test_question_quota_available_then_exceeded(store: InMemoryStore, owner_id: UUID) -> None:
    clock = Clock()
    quotas = _quotas(store, limits=_daily(questions=2), clock=clock)

    assert await quotas.reserve_question(owner_id, idempotency_key="q-1") is True
    assert await quotas.reserve_question(owner_id, idempotency_key="q-2") is True
    with pytest.raises(QuestionsQuotaExceededError):
        await quotas.reserve_question(owner_id, idempotency_key="q-3")


async def test_retried_question_with_same_key_does_not_double_count(
    store: InMemoryStore, owner_id: UUID
) -> None:
    quotas = _quotas(store, limits=_daily(questions=1))

    assert await quotas.reserve_question(owner_id, idempotency_key="retry-me") is True
    assert await quotas.reserve_question(owner_id, idempotency_key="retry-me") is False
    daily = await quotas.daily_usage(owner_id)
    assert daily.questions == 1


async def test_failed_question_releases_reservation(store: InMemoryStore, owner_id: UUID) -> None:
    quotas = _quotas(store, limits=_daily(questions=1))

    assert await quotas.reserve_question(owner_id, idempotency_key="will-fail") is True
    assert await quotas.release_question(owner_id, idempotency_key="will-fail") is True
    assert await quotas.reserve_question(owner_id, idempotency_key="next") is True


async def test_ai_cost_quota_and_idempotent_recording(store: InMemoryStore, owner_id: UUID) -> None:
    quotas = _quotas(
        store,
        limits=QuotaLimits(max_questions_per_day=0, max_ai_cost_usd_per_day=Decimal("0.50")),
    )

    first = await quotas.record_ai_cost(owner_id, idempotency_key="c1", cost_usd_micros=400_000)
    again = await quotas.record_ai_cost(owner_id, idempotency_key="c1", cost_usd_micros=400_000)
    assert first is True
    assert again is False
    with pytest.raises(AiCostQuotaExceededError):
        await quotas.record_ai_cost(owner_id, idempotency_key="c2", cost_usd_micros=200_000)


async def test_concurrent_question_reservations_respect_the_daily_cap(
    store: InMemoryStore, owner_id: UUID
) -> None:
    quotas = _quotas(store, limits=_daily(questions=5))

    async def reserve(index: int) -> bool | Exception:
        try:
            return await quotas.reserve_question(owner_id, idempotency_key=f"c-{index}")
        except Exception as exc:  # noqa: BLE001 - collect outcomes for the assertion
            return exc

    outcomes = await asyncio.gather(*(reserve(i) for i in range(20)))
    successes = [item for item in outcomes if item is True]
    duplicates = [item for item in outcomes if item is False]
    exceeded = [item for item in outcomes if isinstance(item, QuestionsQuotaExceededError)]

    assert len(successes) == 5
    assert not duplicates
    assert len(exceeded) == 15
    daily = await quotas.daily_usage(owner_id)
    assert daily.questions == 5


async def test_concurrent_identical_keys_charge_once(store: InMemoryStore, owner_id: UUID) -> None:
    quotas = _quotas(store, limits=_daily(questions=10))

    outcomes = await asyncio.gather(
        *(quotas.reserve_question(owner_id, idempotency_key="same") for _ in range(12))
    )
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 11
    assert (await quotas.daily_usage(owner_id)).questions == 1


async def test_document_deletion_releases_document_storage_and_pages(
    store: InMemoryStore, owner_id: UUID
) -> None:
    quotas = _quotas(
        store,
        limits=QuotaLimits(max_documents=1, max_storage_bytes=500, max_pages_total=10),
    )
    doc_id = _document(store, owner_id, size=400, pages=8)

    with pytest.raises(DocumentLimitReachedError):
        await quotas.ensure_upload_allowed(owner_id, file_size=50)
    with pytest.raises(PagesQuotaExceededError):
        await quotas.ensure_pages_allowed(owner_id, additional_pages=5)

    store.documents[doc_id] = replace(
        store.documents[doc_id], processing_status=ProcessingStatus.DELETED
    )

    await quotas.ensure_upload_allowed(owner_id, file_size=400)
    await quotas.ensure_pages_allowed(owner_id, additional_pages=10)
    usage = await quotas.document_usage(owner_id)
    assert usage.documents == 0
    assert usage.storage_bytes == 0
    assert usage.pages == 0


async def test_pricing_helpers_and_parse_ai_pricing() -> None:
    pricing = parse_ai_pricing(
        '{"gpt-4o-mini":{"input_per_1m_usd":0.15,"output_per_1m_usd":0.6},'
        '"default":{"embed_per_1m_usd":0.02}}'
    )
    assert (
        llm_cost_micros(
            model="gpt-4o-mini", input_tokens=1_000_000, output_tokens=0, pricing=pricing
        )
        == 150_000
    )
    assert embedding_cost_micros(model=None, tokens=1_000_000, pricing=pricing) == 20_000
    assert parse_ai_pricing("") == AiPricing(models={})
    assert parse_ai_pricing(None).default == ModelPrices()


async def test_new_utc_day_resets_question_budget(store: InMemoryStore, owner_id: UUID) -> None:
    clock = Clock()
    quotas = _quotas(store, limits=_daily(questions=1), clock=clock)
    assert await quotas.reserve_question(owner_id, idempotency_key="day1") is True
    with pytest.raises(QuestionsQuotaExceededError):
        await quotas.reserve_question(owner_id, idempotency_key="day1-b")

    clock.advance(days=1)
    assert await quotas.reserve_question(owner_id, idempotency_key="day2") is True

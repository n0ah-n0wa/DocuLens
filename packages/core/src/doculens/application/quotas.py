"""Quota enforcement use case (SPECIFICATIONS.md §38, §39).

Checks run under the per-user row lock before expensive work. Daily question and AI-cost hits are
recorded through an idempotent ledger so a retried request with the same key cannot double-count.
"""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from doculens.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from doculens.domain.ids import new_id
from doculens.domain.ingestion import DocumentLimitReachedError
from doculens.domain.quotas import (
    AiCostQuotaExceededError,
    AiPricing,
    DailyUsage,
    DocumentUsageTotals,
    PagesQuotaExceededError,
    QuestionsQuotaExceededError,
    QuotaLimits,
    StorageQuotaExceededError,
    UsageEvent,
    usage_day,
)
from doculens.domain.time import utc_now


class QuotaService:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWorkFactory,
        limits: QuotaLimits,
        pricing: AiPricing | None = None,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], UUID] = new_id,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._limits = limits
        self._pricing = pricing or AiPricing(models={})
        self._clock = clock
        self._new_id = id_factory

    @property
    def limits(self) -> QuotaLimits:
        return self._limits

    @property
    def pricing(self) -> AiPricing:
        return self._pricing

    async def document_usage(self, owner_id: UUID) -> DocumentUsageTotals:
        async with self._unit_of_work() as uow:
            return await uow.documents.usage_totals_for_owner(owner_id)

    async def daily_usage(self, owner_id: UUID, *, moment: datetime | None = None) -> DailyUsage:
        day = usage_day(moment or self._clock())
        async with self._unit_of_work() as uow:
            return await uow.usage.daily_totals(owner_id, day)

    def assert_upload(self, totals: DocumentUsageTotals, *, file_size: int) -> None:
        """Pure check against live totals; caller must hold the per-user lock."""
        self._check_documents(totals.documents + 1)
        self._check_storage(totals.storage_bytes + max(0, file_size))

    def assert_pages(
        self, totals: DocumentUsageTotals, *, additional_pages: int, replacing_pages: int = 0
    ) -> None:
        projected = totals.pages - max(0, replacing_pages) + max(0, additional_pages)
        self._check_pages(projected)

    async def ensure_upload_allowed(self, owner_id: UUID, *, file_size: int) -> None:
        """Refuse the upload before storage work when documents or storage would overflow."""
        async with self._unit_of_work() as uow:
            await uow.users.lock(owner_id)
            totals = await uow.documents.usage_totals_for_owner(owner_id)
            self.assert_upload(totals, file_size=file_size)

    async def ensure_pages_allowed(
        self, owner_id: UUID, *, additional_pages: int, replacing_pages: int = 0
    ) -> None:
        """Refuse extraction when the owner's live page total would exceed the ceiling."""
        async with self._unit_of_work() as uow:
            await uow.users.lock(owner_id)
            totals = await uow.documents.usage_totals_for_owner(owner_id)
            self.assert_pages(
                totals, additional_pages=additional_pages, replacing_pages=replacing_pages
            )

    async def check_upload(self, uow: UnitOfWork, owner_id: UUID, *, file_size: int) -> None:
        """Upload check under an already-held user lock (intake registration path)."""
        totals = await uow.documents.usage_totals_for_owner(owner_id)
        self.assert_upload(totals, file_size=file_size)

    async def check_pages(
        self,
        uow: UnitOfWork,
        owner_id: UUID,
        *,
        additional_pages: int,
        replacing_pages: int = 0,
    ) -> None:
        totals = await uow.documents.usage_totals_for_owner(owner_id)
        self.assert_pages(
            totals, additional_pages=additional_pages, replacing_pages=replacing_pages
        )

    async def reserve_question(
        self, owner_id: UUID, *, idempotency_key: str, moment: datetime | None = None
    ) -> bool:
        """Charge one question before AI work. Returns False when the key was already reserved.

        Concurrent callers serialise on the user lock. A duplicate key is a no-op (retry-safe).
        """
        now = moment or self._clock()
        day = usage_day(now)
        async with self._unit_of_work() as uow:
            await uow.users.lock(owner_id)
            existing = await uow.usage.get(owner_id, idempotency_key)
            if existing is not None:
                return False
            daily = await uow.usage.daily_totals(owner_id, day)
            self._check_questions(daily.questions + 1)
            if self._limits.max_ai_cost_usd_per_day > 0:
                self._check_ai_cost(daily.cost_usd_micros)
            await uow.usage.add(
                UsageEvent(
                    id=self._new_id(),
                    owner_id=owner_id,
                    idempotency_key=idempotency_key,
                    usage_day=day,
                    kind="question",
                    questions=1,
                    cost_usd_micros=0,
                    created_at=now,
                )
            )
            await uow.commit()
            return True

    async def release_question(self, owner_id: UUID, *, idempotency_key: str) -> bool:
        """Void a reservation when the question never completed (failed before persistence)."""
        async with self._unit_of_work() as uow:
            await uow.users.lock(owner_id)
            removed = await uow.usage.delete(owner_id, idempotency_key)
            if removed:
                await uow.commit()
            return removed

    async def record_ai_cost(
        self,
        owner_id: UUID,
        *,
        idempotency_key: str,
        cost_usd_micros: int,
        kind: str = "ai",
        moment: datetime | None = None,
    ) -> bool:
        """Append AI spend under ``idempotency_key``. Duplicate keys do not double-charge."""
        if cost_usd_micros <= 0:
            return False
        now = moment or self._clock()
        day = usage_day(now)
        async with self._unit_of_work() as uow:
            await uow.users.lock(owner_id)
            existing = await uow.usage.get(owner_id, idempotency_key)
            if existing is not None:
                return False
            daily = await uow.usage.daily_totals(owner_id, day)
            projected = daily.cost_usd_micros + cost_usd_micros
            if (
                self._limits.max_ai_cost_usd_per_day > 0
                and projected > self._limits.max_ai_cost_micros_per_day
            ):
                raise AiCostQuotaExceededError
            await uow.usage.add(
                UsageEvent(
                    id=self._new_id(),
                    owner_id=owner_id,
                    idempotency_key=idempotency_key,
                    usage_day=day,
                    kind=kind,
                    questions=0,
                    cost_usd_micros=cost_usd_micros,
                    created_at=now,
                )
            )
            await uow.commit()
            return True

    def _check_documents(self, projected: int) -> None:
        limit = self._limits.max_documents
        if limit > 0 and projected > limit:
            raise DocumentLimitReachedError

    def _check_storage(self, projected: int) -> None:
        limit = self._limits.max_storage_bytes
        if limit > 0 and projected > limit:
            raise StorageQuotaExceededError

    def _check_pages(self, projected: int) -> None:
        limit = self._limits.max_pages_total
        if limit > 0 and projected > limit:
            raise PagesQuotaExceededError

    def _check_questions(self, projected: int) -> None:
        limit = self._limits.max_questions_per_day
        if limit > 0 and projected > limit:
            raise QuestionsQuotaExceededError

    def _check_ai_cost(self, current_micros: int) -> None:
        limit = self._limits.max_ai_cost_micros_per_day
        if limit > 0 and current_micros >= limit:
            raise AiCostQuotaExceededError


__all__ = ["QuotaService"]

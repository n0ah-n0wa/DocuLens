"""Composition helpers for per-user quotas and AI pricing (§38, §39, OQ-11)."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from doculens.application.quotas import QuotaService
from doculens.domain.quotas import MEBIBYTE, QuotaLimits, parse_ai_pricing

if TYPE_CHECKING:
    from doculens.application.unit_of_work import UnitOfWorkFactory
    from doculens.domain.quotas import AiPricing
    from doculens.infrastructure.config import CoreSettings


def build_quota_limits(settings: CoreSettings) -> QuotaLimits:
    return QuotaLimits(
        max_documents=settings.quota_max_documents,
        max_storage_bytes=settings.quota_max_storage_mb * MEBIBYTE,
        max_pages_total=settings.quota_max_pages_total,
        max_questions_per_day=settings.quota_max_questions_per_day,
        max_ai_cost_usd_per_day=Decimal(settings.quota_max_ai_cost_usd_per_day),
    )


def build_ai_pricing(settings: CoreSettings) -> AiPricing:
    return parse_ai_pricing(settings.ai_pricing_json)


def build_quota_service(settings: CoreSettings, *, unit_of_work: UnitOfWorkFactory) -> QuotaService:
    return QuotaService(
        unit_of_work=unit_of_work,
        limits=build_quota_limits(settings),
        pricing=build_ai_pricing(settings),
    )


__all__ = ["build_ai_pricing", "build_quota_limits", "build_quota_service"]

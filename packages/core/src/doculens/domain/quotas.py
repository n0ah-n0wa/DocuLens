"""Per-user quotas and AI cost accounting (SPECIFICATIONS.md §38, §39; OQ-11).

Limits are configurable. Document / storage / page totals are derived from live documents so
deletion releases capacity. Daily question and AI-cost budgets are tracked in an idempotent usage
ledger so retries with the same key cannot double-charge.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from doculens.domain.errors import InvalidInputError
from doculens.domain.time import utc_now

MEBIBYTE = 1024 * 1024
USD_MICROS = 1_000_000


class QuotaExceededError(InvalidInputError):
    """The caller's account has exhausted a configured usage quota (§39)."""

    code = "QUOTA_EXCEEDED"
    default_message = "A usage quota for this account has been reached."
    dimension: str = "quota"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message)
        self.diagnostics: str | None = self.dimension


class DocumentQuotaExceededError(QuotaExceededError):
    code = "DOCUMENT_LIMIT_REACHED"
    default_message = "The maximum number of documents for this account has been reached."
    dimension = "documents"


class StorageQuotaExceededError(QuotaExceededError):
    code = "STORAGE_QUOTA_EXCEEDED"
    default_message = "The maximum storage for this account has been reached."
    dimension = "storage"


class PagesQuotaExceededError(QuotaExceededError):
    code = "PAGES_QUOTA_EXCEEDED"
    default_message = "The maximum total pages for this account has been reached."
    dimension = "pages"


class QuestionsQuotaExceededError(QuotaExceededError):
    code = "QUESTIONS_QUOTA_EXCEEDED"
    default_message = "The daily question quota for this account has been reached."
    dimension = "questions"


class AiCostQuotaExceededError(QuotaExceededError):
    code = "AI_COST_QUOTA_EXCEEDED"
    default_message = "The daily AI cost quota for this account has been reached."
    dimension = "ai_cost"


@dataclass(frozen=True, slots=True)
class QuotaLimits:
    """Configured §39 ceilings. Zero disables a dimension (unlimited)."""

    max_documents: int = 100
    max_storage_bytes: int = 5 * 1024 * MEBIBYTE  # 5 GiB
    max_pages_total: int = 50_000
    max_questions_per_day: int = 500
    max_ai_cost_usd_per_day: Decimal = Decimal("10.00")

    def __post_init__(self) -> None:
        for name in (
            "max_documents",
            "max_storage_bytes",
            "max_pages_total",
            "max_questions_per_day",
        ):
            if getattr(self, name) < 0:
                message = f"{name} must be >= 0"
                raise ValueError(message)
        if self.max_ai_cost_usd_per_day < 0:
            message = "max_ai_cost_usd_per_day must be >= 0"
            raise ValueError(message)

    @property
    def max_ai_cost_micros_per_day(self) -> int:
        return int(self.max_ai_cost_usd_per_day * USD_MICROS)


@dataclass(frozen=True, slots=True)
class DocumentUsageTotals:
    documents: int = 0
    storage_bytes: int = 0
    pages: int = 0


@dataclass(frozen=True, slots=True)
class DailyUsage:
    day: date
    questions: int = 0
    cost_usd_micros: int = 0


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """One idempotent ledger entry; ``idempotency_key`` is unique per owner."""

    id: UUID
    owner_id: UUID
    idempotency_key: str
    usage_day: date
    kind: str
    questions: int = 0
    cost_usd_micros: int = 0
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ModelPrices:
    input_per_1m_usd: Decimal = Decimal(0)
    output_per_1m_usd: Decimal = Decimal(0)
    embed_per_1m_usd: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class AiPricing:
    """Per-model USD prices per 1M tokens; missing models fall back to ``default``."""

    models: Mapping[str, ModelPrices]
    default: ModelPrices = ModelPrices()

    def for_model(self, model: str | None) -> ModelPrices:
        if model and model in self.models:
            return self.models[model]
        return self.default


def parse_ai_pricing(raw: str | None) -> AiPricing:
    """Parse ``AI_PRICING_JSON``; empty / invalid shapes yield zero prices (cost accounting off)."""
    if not raw or not raw.strip():
        return AiPricing(models={})
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        message = "AI_PRICING_JSON must be valid JSON"
        raise ValueError(message) from exc
    if not isinstance(payload, dict):
        message = "AI_PRICING_JSON must be an object"
        raise TypeError(message)
    models: dict[str, ModelPrices] = {}
    default = ModelPrices()
    for name, entry in payload.items():
        if not isinstance(entry, dict):
            message = f"AI_PRICING_JSON[{name!r}] must be an object"
            raise TypeError(message)
        prices = ModelPrices(
            input_per_1m_usd=_decimal(entry.get("input_per_1m_usd", 0)),
            output_per_1m_usd=_decimal(entry.get("output_per_1m_usd", 0)),
            embed_per_1m_usd=_decimal(entry.get("embed_per_1m_usd", 0)),
        )
        if name == "default":
            default = prices
        else:
            models[str(name)] = prices
    return AiPricing(models=models, default=default)


def llm_cost_micros(
    *,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    pricing: AiPricing,
) -> int:
    prices = pricing.for_model(model)
    micros = 0
    if input_tokens:
        micros += int(Decimal(input_tokens) * prices.input_per_1m_usd)
    if output_tokens:
        micros += int(Decimal(output_tokens) * prices.output_per_1m_usd)
    return max(0, micros)


def embedding_cost_micros(*, model: str | None, tokens: int | None, pricing: AiPricing) -> int:
    if not tokens:
        return 0
    prices = pricing.for_model(model)
    return max(0, int(Decimal(tokens) * prices.embed_per_1m_usd))


def usage_day(moment: datetime | None = None) -> date:
    return (moment or utc_now()).date()


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


# Prefer ``DocumentLimitReachedError`` from ``doculens.domain.ingestion`` at the intake boundary.
__all__ = [
    "MEBIBYTE",
    "USD_MICROS",
    "AiCostQuotaExceededError",
    "AiPricing",
    "DailyUsage",
    "DocumentQuotaExceededError",
    "DocumentUsageTotals",
    "ModelPrices",
    "PagesQuotaExceededError",
    "QuestionsQuotaExceededError",
    "QuotaExceededError",
    "QuotaLimits",
    "StorageQuotaExceededError",
    "UsageEvent",
    "embedding_cost_micros",
    "llm_cost_micros",
    "parse_ai_pricing",
    "usage_day",
]

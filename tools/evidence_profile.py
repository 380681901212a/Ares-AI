"""Universal evidence planning helpers for research tasks.

The goal is to avoid hard-coded category templates such as "smartphones only".
Instead we infer the evidence types a task needs: official specs, fresh local
prices, offers with links, comparisons, reviews, and so on.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
import re
from typing import Any


_CONNECTORS_RE = re.compile(
    r"\s+(?:і|та|vs\.?|versus|проти|порівняно з|and|or)\s+",
    re.IGNORECASE,
)
_CURRENCY_RE = re.compile(r"(?:грн|uah|₴|usd|\$|eur|€)", re.IGNORECASE)
_BUDGET_RE = re.compile(
    r"(?:до|за|around|about|under|budget)\s*([0-9][0-9\s.,]*)\s*(тис\.?|k|грн|uah|₴)?",
    re.IGNORECASE,
)
_MODEL_RE = re.compile(
    r"\b(?:"
    r"(?:Samsung\s+Galaxy\s+[A-Z]?\d{1,3}(?:\s+(?:Ultra|Plus|FE|5G|Pro|Max))?)|"
    r"(?:iPhone\s+\d{1,2}(?:\s+(?:Pro\s+Max|Pro|Plus|Max|Air|e))?)|"
    r"(?:Apple\s+iPhone\s+\d{1,2}(?:\s+(?:Pro\s+Max|Pro|Plus|Max|Air|e))?)|"
    r"(?:Audi\s+[A-Z0-9-]+(?:\s+\d{4})?)|"
    r"(?:BMW\s+[A-Z0-9-]+(?:\s+\d{4})?)|"
    r"(?:Mercedes(?:-Benz)?\s+[A-Z0-9-]+(?:\s+\d{4})?)"
    r")\b",
    re.IGNORECASE,
)

_VARIANT_TERMS = ("ultra", "pro max", "pro", "plus", "max", "fe")
_GENERIC_PRODUCT_TERMS = (
    "смартфон",
    "телефон",
    "ноутбук",
    "планшет",
    "монітор",
    "телевізор",
    "відеокарта",
    "процесор",
    "пилосос",
    "холодильник",
    "пральна машина",
)
_SOURCE_HINTS = {
    "rozetka": ("rozetka", "розетка"),
    "hotline": ("hotline", "хотлайн"),
    "allo": ("allo", "алло"),
    "comfy": ("comfy", "комфі"),
    "ekatalog": ("e-katalog", "ekatalog", "е-каталог"),
    "auto.ria": ("auto.ria", "авторіа", "auto ria"),
}


@dataclass
class EvidenceProfile:
    entities: list[str]
    locale: str
    currency: str
    intents: list[str]
    budget_max: int | None
    freshness_required: bool
    preferred_source_hints: list[str]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_entity(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip(" .,;:-")
    return value


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        key = value.casefold().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def extract_entities(task: str) -> list[str]:
    """Extract likely product/model entities without relying on a category."""
    task = task or ""
    matches = [_clean_entity(match.group(0)) for match in _MODEL_RE.finditer(task)]
    if matches:
        return _dedupe_keep_order(matches)[:5]

    normalized = task.casefold()
    for term in _GENERIC_PRODUCT_TERMS:
        if term in normalized:
            return [term]

    # Fallback for generic "X vs Y" tasks. Keep it conservative.
    pieces = _CONNECTORS_RE.split(task)
    if 2 <= len(pieces) <= 4:
        candidates = []
        for piece in pieces:
            piece = re.sub(
                r"\b(?:зроби|знайди|склади|порівняльний|аналіз|звіт|word|таблиц[яею]|ціна|характеристики)\b",
                " ",
                piece,
                flags=re.IGNORECASE,
            )
            piece = _clean_entity(piece)
            if 2 <= len(piece.split()) <= 6:
                candidates.append(piece)
        return _dedupe_keep_order(candidates)[:5]

    return []


def detect_locale(task: str) -> tuple[str, str]:
    normalized = (task or "").casefold()
    if any(token in normalized for token in ("україн", "украин", "ukraine", "україні")):
        return "Ukraine", "UAH"
    if any(token in normalized for token in ("польщ", "poland")):
        return "Poland", "PLN"
    if any(token in normalized for token in ("usa", "united states", "america")):
        return "United States", "USD"
    if "€" in normalized or "eur" in normalized:
        return "European Union", "EUR"
    return "", ""


def detect_budget_max(task: str) -> int | None:
    match = _BUDGET_RE.search(task or "")
    if not match:
        return None
    raw_number = re.sub(r"[^\d]", "", match.group(1))
    if not raw_number:
        return None
    value = int(raw_number)
    suffix = (match.group(2) or "").casefold()
    if suffix in {"тис", "тис.", "k"} and value < 1000:
        value *= 1000
    return value


def build_evidence_profile(task: str, output_requirements: dict | None = None) -> EvidenceProfile:
    output_requirements = output_requirements or {}
    normalized = (task or "").casefold()
    locale, currency = detect_locale(task)
    budget_max = detect_budget_max(task)

    intents: list[str] = []
    if any(token in normalized for token in ("характерист", "spec", "ттх", "features")):
        intents.append("official_specs")
    if any(token in normalized for token in ("ціна", "цін", "цена", "цен", "price", "купити", "придбати", "за ")) or budget_max:
        intents.extend(["local_price", "top_offers"])
    if any(token in normalized for token in ("порівня", "comparison", "compare", "vs", "відмінност")):
        intents.append("comparison")
    if any(token in normalized for token in ("відгук", "review", "огляд")):
        intents.append("reviews")
    if any(token in normalized for token in ("benchmark", "тест", "performance", "швидкод")):
        intents.append("benchmarks")
    if output_requirements.get("must_have_table"):
        intents.append("comparison_table")
    if output_requirements.get("must_have_conclusion"):
        intents.append("conclusion")
    if not intents:
        intents.extend(["official_specs", "comparison"])

    hints: list[str] = []
    if locale == "Ukraine":
        hints.extend(["ціна", "грн", "UAH", "Україна", "купити"])
    if "local_price" in intents or "top_offers" in intents:
        hints.extend(["price", "availability", "seller", "link"])
    for canonical, aliases in _SOURCE_HINTS.items():
        if any(alias in normalized for alias in aliases):
            hints.append(canonical)

    freshness_required = any(
        token in normalized
        for token in ("актуаль", "свіж", "current", "latest", "today", "зараз", "ціна", "price")
    )

    return EvidenceProfile(
        entities=extract_entities(task),
        locale=locale,
        currency=currency,
        intents=_dedupe_keep_order(intents),
        budget_max=budget_max,
        freshness_required=freshness_required,
        preferred_source_hints=_dedupe_keep_order(hints),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def canonicalize_search_query(query: str) -> str:
    tokens = re.findall(r"[a-zа-яіїєґ0-9]+", (query or "").casefold())
    stopwords = {
        "the", "a", "an", "and", "or", "for", "of", "to", "in", "with",
        "official", "documentation", "guide", "release", "notes", "detailed",
        "current", "latest", "інформація", "огляд", "детальний",
    }
    meaningful = [token for token in tokens if token not in stopwords and len(token) > 1]
    return " ".join(sorted(dict.fromkeys(meaningful)))


def build_seed_queries(profile: EvidenceProfile) -> list[str]:
    """Generate category-agnostic, evidence-driven search queries."""
    queries: list[str] = []
    entities = profile.entities
    locale_suffix = f" {profile.locale}" if profile.locale else ""
    price_suffix = f" {profile.currency}" if profile.currency else ""

    for entity in entities:
        if "official_specs" in profile.intents:
            queries.append(f"{entity} official specifications")
            queries.append(f"{entity} характеристики офіційно")
        if "local_price" in profile.intents:
            queries.append(f"{entity} ціна{locale_suffix}{price_suffix}".strip())
            queries.append(f"{entity} купити{locale_suffix} ціна".strip())
        if "top_offers" in profile.intents and profile.budget_max:
            queries.append(f"{entity} до {profile.budget_max} грн купити{locale_suffix}".strip())
            for hint in profile.preferred_source_hints:
                if hint in {"price", "availability", "seller", "link", "ціна", "грн", "UAH", "Україна", "купити"}:
                    continue
                queries.append(f"{entity} {hint} до {profile.budget_max} грн".strip())
        if "reviews" in profile.intents:
            queries.append(f"{entity} огляд відгуки")
        if "benchmarks" in profile.intents:
            queries.append(f"{entity} benchmark performance test")

    if len(entities) >= 2 and "comparison" in profile.intents:
        pair = " vs ".join(entities[:2])
        queries.append(f"{pair} comparison")
        queries.append(f"{entities[0]} {entities[1]} ключові відмінності")

    return _dedupe_keep_order(queries)


def summarize_profile(profile: EvidenceProfile) -> str:
    return json.dumps(profile.to_dict(), ensure_ascii=False, indent=2)


def validate_output_against_profile(output: dict[str, Any], profile: EvidenceProfile, original_task: str) -> dict[str, Any]:
    """Deterministic quality gate for critical evidence before document creation."""
    evidence_payload = {
        key: value
        for key, value in output.items()
        if key not in {"evidence_profile", "data_quality"}
    }
    text = json.dumps(evidence_payload, ensure_ascii=False).casefold()
    task_text = (original_task or "").casefold()
    missing_fields: list[str] = []
    entity_issues: list[str] = []
    freshness_issues: list[str] = []

    for entity in profile.entities:
        if entity.casefold() not in text:
            entity_issues.append(f"Missing exact entity: {entity}")

    for variant in _VARIANT_TERMS:
        variant_pattern = r"\b" + re.escape(variant).replace(r"\ ", r"\s+") + r"\b"
        if re.search(variant_pattern, text) and not re.search(variant_pattern, task_text):
            entity_issues.append(
                f"Output mentions variant '{variant}' that was not requested. Verify it is not replacing the requested model."
            )

    if "top_offers" in profile.intents:
        offers = output.get("top_offers")
        if not isinstance(offers, list) or not offers:
            missing_fields.append("top_offers list with real products")
        else:
            complete_offers = 0
            for offer in offers:
                if not isinstance(offer, dict):
                    continue
                has_product = bool(str(offer.get("product", "")).strip())
                has_price = bool(str(offer.get("price", "")).strip())
                has_url = bool(str(offer.get("url", "") or offer.get("link", "")).strip())
                if has_product and has_price and has_url:
                    complete_offers += 1
            if complete_offers < min(3, len(offers)):
                missing_fields.append("at least 3 complete offers with product, price, and url")

    if "local_price" in profile.intents or "top_offers" in profile.intents:
        if not (_CURRENCY_RE.search(text) or (profile.currency and profile.currency.casefold() in text)):
            missing_fields.append("Fresh/local price with currency")
        if profile.locale and profile.locale.casefold() not in text and "україн" not in text:
            missing_fields.append(f"Locale-specific price evidence for {profile.locale}")
        if "http" not in text and "source" not in text:
            missing_fields.append("Source links for price/offers")

    if "comparison_table" in profile.intents and "comparison_table" not in output:
        missing_fields.append("comparison_table")

    if profile.freshness_required and not any(token in text for token in ("актуаль", "current", "as of", "станом")):
        freshness_issues.append("Freshness marker is missing")

    critical = bool(missing_fields or entity_issues)
    feedback_parts = []
    if missing_fields:
        feedback_parts.append("Missing fields: " + "; ".join(missing_fields))
    if entity_issues:
        feedback_parts.append("Entity issues: " + "; ".join(entity_issues))
    if freshness_issues:
        feedback_parts.append("Freshness issues: " + "; ".join(freshness_issues))

    return {
        "critical": critical,
        "missing_fields": missing_fields,
        "entity_issues": entity_issues,
        "freshness_issues": freshness_issues,
        "feedback": " | ".join(feedback_parts),
    }

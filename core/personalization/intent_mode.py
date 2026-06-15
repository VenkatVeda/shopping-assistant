"""
Intent Mode Classifier — determines how much weight to give the user's profile
vs their current query, preventing filter bubbles and over-personalisation.

The classifier reads four layers of signal and produces a PersonalisationDecision
that controls per-dimension blending weights in the search layer.

Usage:
    from core.personalization.intent_mode import classify_personalisation_mode, PersonalisationMode

    decision = classify_personalisation_mode(
        query="show me something for a beach holiday",
        query_preferences=prefs,
        user_profile=profile.to_dict(),
        session_signals=session_signals,
    )
    # decision.mode, decision.profile_weight, decision.overridden_dimensions, ...
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Explicit linguistic signals
# ---------------------------------------------------------------------------

_EXPLORE_SIGNALS = [
    "something different", "something new", "try something", "surprise me",
    "not my usual", "never tried", "gift for", "as a present", "for my",
    "for a wedding", "for a job interview", "for a holiday", "for a beach",
    "for the office", "for travel", "special occasion", "treat myself",
    "show me options", "explore", "something fresh", "completely different",
    "out of my comfort zone",
]

_FOLLOW_PROFILE_SIGNALS = [
    "same style", "similar to", "more like", "another one", "my usual",
    "what i normally", "like before", "as always", "same as", "similar style",
    "my kind of", "keep it similar",
]

_DISSATISFACTION_SIGNALS = [
    "none of these", "don't like any", "show me different ones",
    "not what i wanted", "something else entirely", "these aren't right",
    "not helpful", "different results", "try again", "show me other",
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class PersonalisationMode(Enum):
    FOLLOW_PROFILE = "follow_profile"   # stay close to historical preferences
    PARTIAL        = "partial"          # some dimensions override, others follow profile
    FULL_OVERRIDE  = "full_override"    # current query intent dominates
    EXPLORE        = "explore"          # actively diversify away from profile


@dataclass
class SessionSignals:
    """
    Behaviour signals accumulated within the current session.
    Populate this from the LangGraph state before calling the classifier.
    """
    viewed_products: List[Dict[str, Any]] = field(default_factory=list)
    rejected_results: int = 0          # times user said "not these" / reformulated
    same_query_variations: int = 0     # number of reformulations this session
    satisfaction_signals: int = 0      # positive engagements (clicks, follow-ups)


@dataclass
class ProfileConflictResult:
    overridden_dimensions: List[str]   # dimensions the query departs from profile
    aligned_dimensions: List[str]      # dimensions consistent with profile
    new_dimensions: List[str]          # dimensions not yet in the profile
    profile_weight: float              # recommended blending weight 0.0–1.0


@dataclass
class PersonalisationDecision:
    mode: PersonalisationMode
    profile_weight: float              # 0.0 → ignore profile; 1.0 → fully follow profile
    overridden_dimensions: List[str]   # do NOT boost these from the profile
    aligned_dimensions: List[str]      # DO boost these from the profile
    explanation: str                   # human-readable reason (for debug / MLflow logging)


# ---------------------------------------------------------------------------
# Helper: query specificity score
# ---------------------------------------------------------------------------

def query_specificity_score(query_preferences: Dict[str, Any]) -> float:
    """
    Returns 0.0 (fully vague) to 1.0 (fully specific).
    Higher specificity → user knows what they want → lower profile weight.

    query_preferences should be the SearchPreferences dict or equivalent,
    with keys: colors, brands, materials, categories, features,
               price_min, price_max, closure_types.
    """
    checks = [
        bool(query_preferences.get("colors")),
        bool(query_preferences.get("brands")),
        bool(query_preferences.get("materials")),
        bool(query_preferences.get("categories")),
        (
            query_preferences.get("price_min") is not None
            or query_preferences.get("price_max") is not None
        ),
        bool(query_preferences.get("features")),
        bool(query_preferences.get("closure_types")),
    ]
    return sum(checks) / len(checks)


# ---------------------------------------------------------------------------
# Helper: profile conflict detection
# ---------------------------------------------------------------------------

def detect_profile_conflicts(
    query_preferences: Dict[str, Any],
    user_profile: Dict[str, Any],
) -> ProfileConflictResult:
    """
    Compare the current query's extracted preferences against the stored user
    profile.  Returns which dimensions are being overridden, aligned, or newly
    introduced, plus a recommended profile_weight.

    user_profile should be the dict produced by UserProfile.to_dict().
    """
    overridden: List[str] = []
    aligned: List[str]    = []
    new_dims: List[str]   = []

    prefs = user_profile.get("preferences", {})

    def _profile_values(category: str) -> set:
        cat = prefs.get(category, {})
        items = cat.get("items", {}) if isinstance(cat, dict) else {}
        return {k.lower() for k in items}

    # --- Color ---
    q_colors = {c.lower() for c in (query_preferences.get("colors") or [])}
    p_colors  = _profile_values("colors")
    if q_colors:
        if p_colors and not q_colors.intersection(p_colors):
            overridden.append("color")
        elif p_colors:
            aligned.append("color")
        else:
            new_dims.append("color")

    # --- Brand ---
    q_brands = {b.lower() for b in (query_preferences.get("brands") or [])}
    p_brands  = _profile_values("brands")
    if q_brands:
        if p_brands and not q_brands.intersection(p_brands):
            overridden.append("brand")
        elif p_brands:
            aligned.append("brand")
        else:
            new_dims.append("brand")

    # --- Material ---
    q_materials = {m.lower() for m in (query_preferences.get("materials") or [])}
    p_materials  = _profile_values("materials")
    if q_materials:
        if p_materials and not q_materials.intersection(p_materials):
            overridden.append("material")
        elif p_materials:
            aligned.append("material")
        else:
            new_dims.append("material")

    # --- Category ---
    q_cats = {c.lower() for c in (query_preferences.get("categories") or [])}
    p_cats  = _profile_values("categories")
    if q_cats:
        if p_cats and not q_cats.intersection(p_cats):
            overridden.append("category")
        elif p_cats:
            aligned.append("category")
        else:
            new_dims.append("category")

    # --- Price (significant departure = override) ---
    typical_max = user_profile.get("price_range", {}).get("max")
    q_price_max = query_preferences.get("price_max")
    if q_price_max and typical_max:
        try:
            ratio = float(q_price_max) / float(typical_max)
            if ratio < 0.5 or ratio > 2.0:
                overridden.append("price")
            else:
                aligned.append("price")
        except (TypeError, ValueError):
            pass
    elif q_price_max:
        new_dims.append("price")

    # Compute profile_weight from conflict ratio
    total = len(overridden) + len(aligned) + len(new_dims)
    if total == 0:
        profile_weight = 0.70   # vague query — let profile guide
    else:
        override_ratio = len(overridden) / total
        profile_weight = max(0.10, 0.70 - override_ratio * 0.60)

    return ProfileConflictResult(
        overridden_dimensions=overridden,
        aligned_dimensions=aligned,
        new_dimensions=new_dims,
        profile_weight=profile_weight,
    )


# ---------------------------------------------------------------------------
# Helper: session drift score
# ---------------------------------------------------------------------------

def compute_session_drift(
    signals: SessionSignals,
    user_profile: Dict[str, Any],
) -> float:
    """
    Returns a drift score 0.0 (stay in profile) → 1.0 (user is exploring away).
    """
    drift = 0.0

    prefs = user_profile.get("preferences", {})
    p_brands = {
        k.lower()
        for k in prefs.get("brands", {}).get("items", {})
    }

    if signals.viewed_products and p_brands:
        off_profile = sum(
            1 for p in signals.viewed_products
            if (p.get("brand") or p.get("metadata", {}).get("brand") or "").lower()
               not in p_brands
        )
        drift += (off_profile / len(signals.viewed_products)) * 0.4

    if signals.rejected_results >= 2:
        drift += 0.3

    if signals.same_query_variations >= 3:
        drift += 0.2

    return min(1.0, drift)


# ---------------------------------------------------------------------------
# Temporal decay helper
# ---------------------------------------------------------------------------

def decayed_weight(last_seen_days: int) -> float:
    """
    Exponential decay with a 90-day half-life.
    Use this when reading preference weights from the stored profile to
    de-emphasise stale signals.

      30 days → 0.79
      90 days → 0.50
     180 days → 0.25
     365 days → 0.06
    """
    return math.exp(-0.693 * last_seen_days / 90)


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------

def classify_personalisation_mode(
    query: str,
    query_preferences: Dict[str, Any],
    user_profile: Dict[str, Any],
    session_signals: Optional[SessionSignals] = None,
) -> PersonalisationDecision:
    """
    Determine how to blend the user's historical profile with the current query.

    Args:
        query:              Raw user query string.
        query_preferences:  Extracted preferences from the current query
                            (SearchPreferences dict or equivalent).
        user_profile:       Stored user profile as dict (UserProfile.to_dict()).
        session_signals:    Optional in-session behaviour signals.

    Returns:
        PersonalisationDecision with mode, profile_weight, and per-dimension flags.
    """
    if session_signals is None:
        session_signals = SessionSignals()

    query_lower = query.lower()

    # ------------------------------------------------------------------
    # Layer 1 — Explicit linguistic signals (highest confidence, O(1))
    # ------------------------------------------------------------------

    if any(s in query_lower for s in _EXPLORE_SIGNALS):
        logger.debug("[intent_mode] Explicit explore signal detected")
        return PersonalisationDecision(
            mode=PersonalisationMode.EXPLORE,
            profile_weight=0.05,
            overridden_dimensions=["color", "brand", "material", "category"],
            aligned_dimensions=["price"],   # keep price comfort even in explore
            explanation="Explicit exploration or occasion-specific intent in query",
        )

    if any(s in query_lower for s in _DISSATISFACTION_SIGNALS):
        logger.debug("[intent_mode] Dissatisfaction signal detected")
        return PersonalisationDecision(
            mode=PersonalisationMode.EXPLORE,
            profile_weight=0.10,
            overridden_dimensions=["color", "brand", "material", "category"],
            aligned_dimensions=["price"],
            explanation="Dissatisfaction signal — diversifying away from prior results",
        )

    if any(s in query_lower for s in _FOLLOW_PROFILE_SIGNALS):
        logger.debug("[intent_mode] Follow-profile signal detected")
        return PersonalisationDecision(
            mode=PersonalisationMode.FOLLOW_PROFILE,
            profile_weight=0.80,
            overridden_dimensions=[],
            aligned_dimensions=["color", "brand", "material", "category", "price"],
            explanation="User explicitly requested profile-aligned results",
        )

    # ------------------------------------------------------------------
    # Layer 2+3 — Specificity + conflict analysis
    # ------------------------------------------------------------------

    specificity   = query_specificity_score(query_preferences)
    conflict      = detect_profile_conflicts(query_preferences, user_profile)
    session_drift = compute_session_drift(session_signals, user_profile)

    logger.debug(
        f"[intent_mode] specificity={specificity:.2f} "
        f"overridden={conflict.overridden_dimensions} "
        f"drift={session_drift:.2f}"
    )

    # High specificity + multiple conflicts → user has a precise new intent
    if specificity >= 0.6 and len(conflict.overridden_dimensions) >= 2:
        profile_weight = max(0.05, conflict.profile_weight - session_drift * 0.20)
        return PersonalisationDecision(
            mode=PersonalisationMode.FULL_OVERRIDE,
            profile_weight=profile_weight,
            overridden_dimensions=conflict.overridden_dimensions,
            aligned_dimensions=conflict.aligned_dimensions,
            explanation=(
                f"High-specificity query overriding profile on: "
                f"{conflict.overridden_dimensions}"
            ),
        )

    # Session drift pushing away from profile
    if session_drift >= 0.5:
        profile_weight = max(0.10, 0.40 - session_drift * 0.30)
        return PersonalisationDecision(
            mode=PersonalisationMode.EXPLORE,
            profile_weight=profile_weight,
            overridden_dimensions=conflict.overridden_dimensions,
            aligned_dimensions=conflict.aligned_dimensions,
            explanation=(
                f"Session behaviour signals exploration away from profile "
                f"(drift={session_drift:.2f})"
            ),
        )

    # Some dimensions override, others stay with profile
    if conflict.overridden_dimensions:
        return PersonalisationDecision(
            mode=PersonalisationMode.PARTIAL,
            profile_weight=conflict.profile_weight,
            overridden_dimensions=conflict.overridden_dimensions,
            aligned_dimensions=conflict.aligned_dimensions,
            explanation=(
                f"Partial override — departing from profile on: "
                f"{conflict.overridden_dimensions}, "
                f"aligned on: {conflict.aligned_dimensions}"
            ),
        )

    # Default — vague query, let the profile guide
    base_weight = max(0.20, 0.70 - specificity * 0.30)
    return PersonalisationDecision(
        mode=PersonalisationMode.FOLLOW_PROFILE,
        profile_weight=base_weight,
        overridden_dimensions=[],
        aligned_dimensions=conflict.aligned_dimensions or ["color", "brand", "material", "category", "price"],
        explanation=(
            f"Profile guidance — "
            f"specificity={specificity:.2f}, weight={base_weight:.2f}"
        ),
    )


# ---------------------------------------------------------------------------
# Diversity injection
# ---------------------------------------------------------------------------

def inject_diversity(
    results: List[Dict[str, Any]],
    user_profile: Dict[str, Any],
    decision: PersonalisationDecision,
    diversity_slot: int = 4,
) -> List[Dict[str, Any]]:
    """
    In FOLLOW_PROFILE and PARTIAL modes, swap a result at `diversity_slot`
    with the highest-ranked off-profile candidate found further down the list.

    This prevents filter bubbles by ensuring every result set contains at
    least one serendipitous product the user would not normally see.

    In EXPLORE or FULL_OVERRIDE mode, the list is already diversified —
    this function returns it unchanged.

    Args:
        results:        Ranked list of product dicts (each may have a 'metadata' sub-dict).
        user_profile:   UserProfile.to_dict().
        decision:       The PersonalisationDecision from classify_personalisation_mode.
        diversity_slot: Position (0-indexed) where the off-profile result is injected.

    Returns:
        Possibly reordered results list.
    """
    if decision.mode in (PersonalisationMode.EXPLORE, PersonalisationMode.FULL_OVERRIDE):
        return results

    if len(results) < diversity_slot + 2:
        return results

    prefs = user_profile.get("preferences", {})
    p_brands = {k.lower() for k in prefs.get("brands", {}).get("items", {})}
    p_colors = {k.lower() for k in prefs.get("colors", {}).get("items", {})}

    off_profile_candidate = None
    for product in results[diversity_slot:]:
        meta  = product.get("metadata") or product
        brand = (meta.get("brand") or "").lower()
        color = (meta.get("primary_color") or meta.get("color") or "").lower()
        if brand not in p_brands or color not in p_colors:
            off_profile_candidate = product
            break

    if off_profile_candidate:
        results = [r for r in results if r is not off_profile_candidate]
        candidate = dict(off_profile_candidate)
        candidate["_diversity_injection"] = True
        results.insert(diversity_slot, candidate)
        logger.debug(
            f"[intent_mode] Diversity injection at slot {diversity_slot}: "
            f"{off_profile_candidate.get('metadata', off_profile_candidate).get('name', 'unknown')}"
        )

    return results


# ---------------------------------------------------------------------------
# Dimension-aware preference boost
# ---------------------------------------------------------------------------

def dimension_aware_boost(
    product_meta: Dict[str, Any],
    user_profile: Dict[str, Any],
    decision: PersonalisationDecision,
) -> float:
    """
    Compute a personalisation boost score (0.0–1.0) for a single product,
    applying the profile weight ONLY to dimensions that are NOT being overridden.

    Use this score to blend with the raw vector-search relevance score:
        final_score = relevance_score * (1 - decision.profile_weight)
                      + boost * decision.profile_weight

    Args:
        product_meta:   The product's metadata dict (brand, primary_color, etc.).
        user_profile:   UserProfile.to_dict().
        decision:       PersonalisationDecision from classify_personalisation_mode.

    Returns:
        Float 0.0–1.0 representing how well this product matches the active
        profile dimensions.
    """
    if decision.profile_weight < 0.01:
        return 0.0

    prefs = user_profile.get("preferences", {})

    def _profile_set(category: str) -> set:
        cat = prefs.get(category, {})
        return {k.lower() for k in cat.get("items", {})} if isinstance(cat, dict) else set()

    dimension_config = [
        # (dimension_name,  profile_category,  metadata_key,    dim_weight)
        ("color",    "colors",     "primary_color", 0.20),
        ("brand",    "brands",     "brand",         0.25),
        ("material", "materials",  "material_type", 0.15),
        ("category", "categories", "category",      0.15),
        ("price",    None,         "price",         0.25),
    ]

    score      = 0.0
    weight_sum = 0.0

    for dim_name, profile_cat, meta_key, dim_weight in dimension_config:
        if dim_name in decision.overridden_dimensions:
            continue   # user is intentionally departing from this — skip

        weight_sum += dim_weight

        if dim_name == "price":
            typical_max = user_profile.get("price_range", {}).get("max")
            if typical_max:
                try:
                    price_str = str(product_meta.get("price") or product_meta.get("sale_price") or 0)
                    price = float(price_str.replace("$", "").replace(",", "").strip())
                    if price <= float(typical_max):
                        score += dim_weight * 1.0
                    elif price <= float(typical_max) * 1.30:
                        score += dim_weight * 0.5
                    # else: 0 — significantly over budget
                except (ValueError, TypeError):
                    score += dim_weight * 0.5   # unknown price — neutral
        else:
            profile_values = _profile_set(profile_cat)
            product_value  = (product_meta.get(meta_key) or "").lower()
            if profile_values and product_value and product_value in profile_values:
                score += dim_weight * 1.0

    if weight_sum == 0:
        return 0.5   # no applicable dimensions — neutral

    normalised = score / weight_sum
    return round(normalised * decision.profile_weight, 4)

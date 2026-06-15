"""
Main personalization engine.
This is the entry point for all personalization logic.
"""

from typing import Dict, Any, Optional, Tuple, Set
from datetime import datetime
import uuid
import logging

from .models import UserProfile, SessionState, PreferenceCategory
from .extractor import PreferenceExtractor
from .rules import PersonalizationRules
from .counters import PreferenceCounter
from .post_processing import fix_color_modifiers, extract_style_attributes, detect_special_occasion
from .rate_limiter import RateLimiter
from .intent_mode import (
    classify_personalisation_mode,
    inject_diversity,
    dimension_aware_boost,
    PersonalisationDecision,
    PersonalisationMode,
    SessionSignals,
)

# Configure logging
logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when user exceeds rate limit."""
    pass

class PersonalizationEngine:
    """
    Main engine that orchestrates the entire personalization flow.
    
    This is a PURE Python class with no Flask/DB dependencies.
    Can be tested standalone.
    """
    
    def __init__(self, extractor: PreferenceExtractor, enable_rate_limiting: bool = True):
        """
        Args:
            extractor: LLM-based preference extractor
            enable_rate_limiting: Whether to enable rate limiting (default: True)
        """
        self.extractor = extractor
        self.rules = PersonalizationRules()
        self.counter = PreferenceCounter()
        
        # Rate limiting (10 requests per minute per user)
        self.rate_limiter = RateLimiter(max_requests=10, window_seconds=60) if enable_rate_limiting else None

    def process_message(
        self,
        user_id: str,
        user_message: str,
        current_profile: UserProfile,
        current_session: Optional[SessionState] = None
    ) -> Tuple[UserProfile, SessionState, Dict[str, Any]]:
        """
        Main entry point: Process a user message and return updated state.
        
        Args:
            user_id: User identifier
            user_message: User's chat message
            current_profile: Current user profile (from DB)
            current_session: Current session state (from memory, or None for new session)
        
        Returns:
            (
                updated_profile: UserProfile to save to DB (may be unchanged),
                updated_session: SessionState to keep in memory,
                context: Dict with personalization info for the chatbot
            )
        """
        
        # Validate inputs
        if not user_id or not isinstance(user_id, str):
            raise ValueError("user_id must be a non-empty string")
        
        if not user_message or not isinstance(user_message, str):
            raise ValueError("user_message must be a non-empty string")
        
        if len(user_message) > 1000:
            raise ValueError("user_message exceeds maximum length of 1000 characters")
        
        if not isinstance(current_profile, UserProfile):
            raise TypeError("current_profile must be a UserProfile instance")
        
        # Check rate limit
        if self.rate_limiter and not self.rate_limiter.is_allowed(user_id):
            remaining = self.rate_limiter.get_remaining(user_id)
            logger.warning(f"Rate limit exceeded for user {user_id}")
            raise RateLimitExceeded(f"Rate limit exceeded. Please try again later.")
        
        # Initialize session if needed
        if current_session is None:
            current_session = SessionState(
                session_id=str(uuid.uuid4()),
                user_id=user_id
            )

        # Apply calendar-based preference decay on the first turn of each session.
        # Uses last_active from behavior_stats so it only runs once per session,
        # not on every message.
        if current_session.turn_count == 0:
            last_active_str = current_profile.behavior_stats.get("last_active")
            if last_active_str:
                try:
                    days_since = (datetime.now() - datetime.fromisoformat(last_active_str)).days
                    months_since = days_since / 30
                    if months_since >= 1:
                        PreferenceCounter.apply_decay(current_profile, months_inactive=max(1, int(months_since)))
                        logger.info(
                            f"[ENGINE] Applied preference decay for user {user_id} "
                            f"({days_since}d since last active)"
                        )
                except (ValueError, TypeError) as e:
                    logger.warning(f"[ENGINE] Could not parse last_active for decay: {e}")
            # Stamp this session start as the new last_active
            current_profile.behavior_stats["last_active"] = datetime.now().isoformat()
            current_profile.behavior_stats["total_sessions"] = (
                current_profile.behavior_stats.get("total_sessions", 0) + 1
            )

        # Increment turn counter
        current_session.turn_count += 1
        current_session.last_query = user_message

        logger.info(f"Processing message for user {user_id}, turn {current_session.turn_count}")
        
        # Step 1: Extract structured information using LLM
        try:
            extraction = self.extractor.extract(user_message)
        except Exception as e:
            logger.error(f"LLM extraction failed for user {user_id}: {e}")
            extraction = {
                "intent_type": "query",
                "extracted": {
                    "colors": [], "brands": [], "materials": [],
                    "categories": [], "features": [],
                    "price_range": {"min": None, "max": None},
                },
                "negations": {
                    "colors": [], "brands": [], "materials": [],
                    "categories": [], "features": [],
                },
                "signals": {"is_gift": False, "is_special_occasion": False, "confidence": 0.0},
            }

        # Step 2: Resolve session flags BEFORE any profile writes so gift guard is active
        signals = extraction.get("signals", {})
        if not signals.get("is_special_occasion", False):
            signals["is_special_occasion"] = detect_special_occasion(user_message)
        if signals.get("is_gift"):
            current_session.is_gift = True
            logger.info(f"Gift context detected for user {user_id} — profile updates suppressed")
        if signals.get("is_special_occasion", False):
            current_session.is_special_occasion = True
        extraction["signals"] = signals

        # Step 1a: Post-process extracted fields
        raw_extracted = extraction["extracted"]
        logger.debug(f"[ENGINE] Extracted (raw): {raw_extracted}")

        raw_extracted = fix_color_modifiers(raw_extracted, user_message)
        raw_extracted = extract_style_attributes(raw_extracted, user_message)

        negated = extraction.get("negations", {})
        raw_extracted = self._filter_negated(raw_extracted, negated)

        logger.debug(f"[ENGINE] intent={extraction['intent_type']} extracted={raw_extracted}")

        # Normalize keys for profile update
        extracted_entities = self._map_extracted_to_profile(raw_extracted)

        # Reinforce from search behavior (respects is_gift guard now correctly ordered)
        self._reinforce_from_search(current_profile, extracted_entities, current_session)

        # Per-turn inferred decay (very light — 0.2% per turn to avoid over-decay in long sessions)
        self._decay_inferred_preferences(current_profile, touched_keys=raw_extracted)

        # Step 3: Classify how to handle this information
        classification = self.rules.classify_update_type(
            extraction.get("intent_type"),
            extraction,
            current_profile,
            current_session,
            user_message
        )
        
        logger.debug(f"Classification: {classification['profile_action']} — {classification['reasoning']}")

        # Step 3b: Downgrade profile action when LLM confidence is low
        confidence = extraction.get("signals", {}).get("confidence", 1.0)
        action = classification["profile_action"]
        if action == "hard_update" and confidence < 0.5:
            action = "soft_update"
            logger.debug(f"[ENGINE] Downgraded hard_update → soft_update (confidence={confidence:.2f})")
        elif action == "soft_update" and confidence < 0.3:
            action = "no_update"
            logger.debug(f"[ENGINE] Downgraded soft_update → no_update (confidence={confidence:.2f})")
        classification["profile_action"] = action

        # Step 4: Apply updates based on classification
        profile_updated = False

        if classification["profile_action"] == "hard_update":
            profile_updated = self._apply_hard_update(current_profile, extracted_entities)
    
        elif classification["profile_action"] == "soft_update":
            profile_updated = self._apply_soft_update(current_profile, extracted_entities)
        
        elif classification["profile_action"] == "requirement_update":
            profile_updated = self._apply_requirement_update(current_profile, extracted_entities)
        
        elif classification["profile_action"] == "remove_preference":
            global_confidence = extraction.get("confidence", 0.5)
            profile_updated = self._apply_negation(current_profile, extraction.get("negations", {}), global_confidence)
        
        elif classification["profile_action"] == "confirm_needed":
            current_session.detected_contradiction = True
            logger.warning(f"Contradiction detected for user {user_id} - need confirmation")
        
        # Step 5: Update session constraints
        if classification.get("session_action") == "add_constraint":
            self._update_session_constraints(
                current_session,
                raw_extracted,  # Use the flattened dict here as well
                is_temporary=current_session.is_gift
            )
        
        # Step 6: Build context for the chatbot
        context = self._build_context(
            current_profile,
            current_session,
            classification,
            extraction  # Fixed: Added missing extraction parameter
        )
        
        # Step 7: Mark profile as updated
        if profile_updated:
            current_profile.updated_at = datetime.now().isoformat()
            logger.info(f"Profile updated for user {user_id}")
        else:
            logger.debug(f"Profile unchanged for user {user_id}")
        
        return current_profile, current_session, context

    def _map_extracted_to_profile(self, extracted: Dict[str, Any]) -> Dict[str, Any]:
        normalized = extracted.copy()

        # Map old keys to new standardized keys
        if "bag_types" in normalized:
            normalized["categories"] = normalized.pop("bag_types")
        
        if "attributes" in normalized:
            normalized["features"] = normalized.pop("attributes")
        
        # Merge styles and designs into features
        features = normalized.get("features", [])
        if "designs" in normalized:
            features.extend(normalized.pop("designs"))
        if "styles" in normalized:
            features.extend(normalized.pop("styles"))
        normalized["features"] = features

        return normalized
    
    def _reinforce_from_search(
        self,
        profile: UserProfile,
        extracted_entities: Dict[str, Any],
        session: SessionState,
        delta: float = 0.05
    ):
        """
        Reinforce user preferences based on repeated search behavior.
        """
        # Does not consider gift searches
        if session.is_gift:
            return

        for color in extracted_entities.get("colors", []):
            profile.preferences["colors"].add_or_update(
                color,
                weight_delta=delta,
                explicit=False
            )

        for brand in extracted_entities.get("brands", []):
            profile.preferences["brands"].add_or_update(
                brand,
                weight_delta=delta,
                explicit=False
            )

        for material in extracted_entities.get("materials", []):
            profile.preferences["materials"].add_or_update(
                material,
                weight_delta=delta,
                explicit=False
            )

        for bag_type in extracted_entities.get("categories", []):
            profile.preferences["categories"].add_or_update(
                bag_type,
                weight_delta=delta,
                explicit=False
            )

        for attr in extracted_entities.get("features", []):
            profile.preferences["features"].add_or_update(
                attr,
                weight_delta=delta,
                explicit=False
            )

    def _decay_inferred_preferences(
        self,
        profile: UserProfile,
        touched_keys: Dict[str, list],
        decay_rate: float = 0.002,   # 0.2% per turn — ~350 turns to halve (not 70)
        min_weight: float = 0.1,
    ):
        """
        Gradually decay inferred preferences that were NOT reinforced this turn.
        Iterates over a snapshot to avoid RuntimeError from concurrent modification.
        Explicit preferences are never decayed.
        """
        for category, pref_category in profile.preferences.items():
            touched = set(touched_keys.get(category, []))
            for key, pref in list(pref_category.items.items()):  # snapshot via list()
                if pref.explicit or key in touched:
                    continue
                pref.weight = max(min_weight, pref.weight * (1 - decay_rate))

    def _filter_negated(self, extracted, negated):
        cleaned = {}
        for category, values in extracted.items():
            neg = set(negated.get(category, []))
            cleaned[category] = [v for v in values if v not in neg]
        return cleaned

    def _apply_hard_update(
        self,
        profile: UserProfile,
        extracted: Dict[str, Any]
    ) -> bool:
        """
        Hard update: User explicitly stated a preference.
        High confidence, strong weight increase.
        """
        updated = False
        
        # Update colors
        for color in extracted.get("colors", []):
            profile.preferences["colors"].add_or_update(
                color,
                self.counter.EXPLICIT_DELTA,
                explicit=True
            )
            updated = True
        
        # Update brands
        for brand in extracted.get("brands", []):
            profile.preferences["brands"].add_or_update(
                brand,
                self.counter.EXPLICIT_DELTA,
                explicit=True
            )
            updated = True
        
        # Update materials
        for material in extracted.get("materials", []):
            profile.preferences["materials"].add_or_update(
                material,
                self.counter.EXPLICIT_DELTA,
                explicit=True
            )
            updated = True
        
        # Update bag types
        for bag_type in extracted.get("categories", []):
            profile.preferences["categories"].add_or_update(
                bag_type,
                self.counter.EXPLICIT_DELTA,
                explicit=True
            )
            updated = True
        
        # Update features
        for attr in extracted.get("features", []):
            profile.preferences["features"].add_or_update(
                attr,
                self.counter.EXPLICIT_DELTA,
                explicit=True
            )
            updated = True
        
        # Update price range if provided
        price_range = extracted.get("price_range", {})
        if isinstance(price_range, dict):
            min_price = price_range.get("min")
            max_price = price_range.get("max")
            if min_price is not None or max_price is not None:
                profile.price_range = {
                    "min": min_price if min_price is not None else profile.price_range.get("min"),
                    "max": max_price if max_price is not None else profile.price_range.get("max"),
                    "confidence": 0.9,
                }
                updated = True

        return updated
    
    def _apply_soft_update(
        self,
        profile: UserProfile,
        extracted: Dict[str, Any]
    ) -> bool:
        """
        Soft update: Inferred preference from conversation.
        Lower confidence, small weight increase.
        """
        updated = False
        
        # Similar to hard update but with INFERRED_DELTA
        for color in extracted.get("colors", []):
            profile.preferences["colors"].add_or_update(
                color,
                self.counter.INFERRED_DELTA,
                explicit=False
            )
            updated = True
        
        for brand in extracted.get("brands", []):
            profile.preferences["brands"].add_or_update(
                brand,
                self.counter.INFERRED_DELTA,
                explicit=False
            )
            updated = True
        
        for material in extracted.get("materials", []):
            profile.preferences["materials"].add_or_update(
                material,
                self.counter.INFERRED_DELTA,
                explicit=False
            )
            updated = True
        
        for bag_type in extracted.get("categories", []):
            profile.preferences["categories"].add_or_update(
                bag_type,
                self.counter.INFERRED_DELTA,
                explicit=False
            )
            updated = True
        
        for attr in extracted.get("features", []):
            profile.preferences["features"].add_or_update(
                attr,
                self.counter.INFERRED_DELTA,
                explicit=False
            )
            updated = True
        
        return updated
    
    def _apply_requirement_update(
        self,
        profile: UserProfile,
        extracted: Dict[str, Any]
    ) -> bool:
        """
        Requirement update: User stated a need/requirement.
        Medium confidence, moderate weight increase.
        Between explicit and inferred.
        """
        updated = False
        
        # Use REQUIREMENT_DELTA (0.2) instead of EXPLICIT_DELTA (0.5)
        requirement_delta = 0.2
        
        # Update colors
        for color in extracted.get("colors", []):
            profile.preferences["colors"].add_or_update(
                color,
                requirement_delta,
                explicit=False  # Not fully explicit
            )
            updated = True
        
        # Update brands
        for brand in extracted.get("brands", []):
            profile.preferences["brands"].add_or_update(
                brand,
                requirement_delta,
                explicit=False
            )
            updated = True
        
        # Update materials
        for material in extracted.get("materials", []):
            profile.preferences["materials"].add_or_update(
                material,
                requirement_delta,
                explicit=False
            )
            updated = True

        # Update bag types
        for bag_type in extracted.get("bag_types", []):
            profile.preferences["bag_types"].add_or_update(
                bag_type,
                requirement_delta,
                explicit=False
            )
            updated = True

        # Update attributes
        for attr in extracted.get("attributes", []):
            profile.preferences["attributes"].add_or_update(
                attr,
                requirement_delta,
                explicit=False
            )
            updated = True
        
        return updated

    def _apply_negation(
        self,
        profile: UserProfile,
        negations: Dict[str, list],
        global_confidence: float = 0.8
    ) -> bool:
        """
        User explicitly said they DON'T like something.
        Remove or downweight the preference.
        """
        updated = False
        
        # Remove or downweight colors
        for color in negations.get("colors", []):
            profile.preferences["colors"].remove_or_downweight(color, full_remove=True)
            profile.preferences["colors"].add_disliked(color, weight=global_confidence)
            updated = True
        
        # Remove or downweight brands
        for brand in negations.get("brands", []):
            profile.preferences["brands"].remove_or_downweight(brand, full_remove=True)
            profile.preferences["brands"].add_disliked(brand, weight=global_confidence)
            updated = True
        
        # Remove or downweight materials
        for material in negations.get("materials", []):
            profile.preferences["materials"].remove_or_downweight(material, full_remove=True)
            profile.preferences["materials"].add_disliked(material, weight=global_confidence)
            updated = True
        
        # Remove or downweight categories (normalised from bag_types)
        for cat in negations.get("categories", []):
            profile.preferences["categories"].remove_or_downweight(cat, full_remove=True)
            profile.preferences["categories"].add_disliked(cat, weight=global_confidence)
            updated = True

        # Remove or downweight features (normalised from attributes)
        for feat in negations.get("features", []):
            profile.preferences["features"].remove_or_downweight(feat, full_remove=True)
            profile.preferences["features"].add_disliked(feat, weight=global_confidence)
            updated = True

        return updated
    
    def _update_session_constraints(
        self,
        session: SessionState,
        extracted: Dict[str, Any],
        is_temporary: bool = False
    ):
        """
        Update session-level constraints for current search.
        """
        
        if is_temporary:
            # Gift or special occasion - store in temporary interests
            for key, values in extracted.items():
                if values and key != "price_range":
                    session.temporary_interests[key] = values
        else:
            # Regular search - store as explicit constraints
            for key, values in extracted.items():
                if values and key != "price_range":
                    if key not in session.explicit_constraints:
                        session.explicit_constraints[key] = []
                    session.explicit_constraints[key].extend(
                        v for v in values if v not in session.explicit_constraints[key]
                    )

            
            # Handle price range safely
            price_range = extracted.get("price_range", {})

            # Ensure it's a dict
            if isinstance(price_range, dict):
                if price_range.get("min") is not None:
                    session.explicit_constraints["price_min"] = [price_range["min"]]
                if price_range.get("max") is not None:
                    session.explicit_constraints["price_max"] = [price_range["max"]]
            # If it somehow comes as a list
            elif isinstance(price_range, list) and len(price_range) > 0:
                first_range = price_range[0]
                if isinstance(first_range, dict):
                    if first_range.get("min") is not None:
                        session.explicit_constraints["price_min"] = [first_range["min"]]
                    if first_range.get("max") is not None:
                        session.explicit_constraints["price_max"] = [first_range["max"]]
    
    def _build_context(
        self,
        profile: UserProfile,
        session: SessionState,
        classification: Dict[str, str],
        extraction: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Build context dictionary for the chatbot.
        This is what gets injected into the LLM conversation.
        """
        # Determine how much to honour the user's profile vs current query intent
        query_preferences = extraction.get("extracted", {}) if extraction else {}
        last_query        = session.last_query or ""

        # Build session signals from available session state
        session_signals = SessionSignals(
            rejected_results    = session.turn_count if session.detected_contradiction else 0,
            same_query_variations = max(0, session.turn_count - 1),
        )

        personalisation_decision: PersonalisationDecision = classify_personalisation_mode(
            query              = last_query,
            query_preferences  = query_preferences,
            user_profile       = profile.to_dict(),
            session_signals    = session_signals,
        )

        logger.info(
            f"[intent_mode] mode={personalisation_decision.mode.value} "
            f"weight={personalisation_decision.profile_weight:.2f} "
            f"reason={personalisation_decision.explanation}"
        )

        context = {
            "profile_summary": profile.get_summary(),
            "session_constraints": session.explicit_constraints,
            "temporary_interests": session.temporary_interests if session.is_gift else {},
            "is_gift_context": session.is_gift,
            "needs_clarification": classification["profile_action"] == "confirm_needed",
            "clarification_message": "",
            "search_filters": self._build_search_filters(
                profile, session, personalisation_decision
            ),
            "intent_type": extraction.get("intent_type") if extraction else None,
            "profile_action": classification.get("profile_action"),
            "extracted_items": extraction.get("extracted") if extraction else {},
            # Surface the personalisation decision for downstream nodes and observability
            "personalisation_mode": personalisation_decision.mode.value,
            "personalisation_weight": personalisation_decision.profile_weight,
            "personalisation_overrides": personalisation_decision.overridden_dimensions,
            "personalisation_explanation": personalisation_decision.explanation,
        }

        # Add clarification message if needed
        if context["needs_clarification"]:
            should_ask, message = self.rules.should_ask_clarification(classification)
            if should_ask:
                context["clarification_message"] = message

        return context

    def _build_search_filters(
        self,
        profile: UserProfile,
        session: SessionState,
        decision: Optional[PersonalisationDecision] = None,
    ) -> Dict[str, Any]:
        """
        Build search filters for product retrieval.
        Combines profile preferences + session constraints.
        """
        
        filters = {}

        # Determine which profile dimensions to suppress based on intent mode.
        # In EXPLORE / FULL_OVERRIDE, all preference dimensions are suppressed.
        suppressed: set = set()
        if decision is not None:
            if decision.mode in (PersonalisationMode.EXPLORE, PersonalisationMode.FULL_OVERRIDE):
                suppressed = {"color", "brand", "material", "category"}
            else:
                # Suppress only the overridden dimensions
                dim_map = {
                    "color": "colors", "brand": "brands",
                    "material": "materials", "category": "categories",
                }
                suppressed = set(decision.overridden_dimensions)

        # Priority 1: Session explicit constraints (what user wants RIGHT NOW)
        if session.explicit_constraints:
            filters["explicit"] = session.explicit_constraints

        filters["explicit_negations"] = {
            "colors":     list(profile.preferences.get("colors", PreferenceCategory()).disliked_items.keys()),
            "brands":     list(profile.preferences.get("brands", PreferenceCategory()).disliked_items.keys()),
            "materials":  list(profile.preferences.get("materials", PreferenceCategory()).disliked_items.keys()),
            "categories": list(profile.preferences.get("categories", PreferenceCategory()).disliked_items.keys()),
            "features":   list(profile.preferences.get("features", PreferenceCategory()).disliked_items.keys()),
        }

        # Expose profile weight to the search layer
        if decision is not None:
            filters["profile_weight"] = decision.profile_weight
            filters["personalisation_mode"] = decision.mode.value

        if not session.is_gift:  # Don't use profile for gift shopping
            # Only include profile dimensions that the intent mode has NOT suppressed
            if "color" not in suppressed:
                filters["preferred_colors"] = profile.preferences.get("colors", PreferenceCategory()).get_top(3, min_weight=0.4)
            if "brand" not in suppressed:
                filters["preferred_brands"] = profile.preferences.get("brands", PreferenceCategory()).get_top(3, min_weight=0.4)
            if "material" not in suppressed:
                filters["preferred_materials"] = profile.preferences.get("materials", PreferenceCategory()).get_top(2, min_weight=0.4)
            if "category" not in suppressed:
                filters["preferred_types"] = profile.preferences.get("categories", PreferenceCategory()).get_top(2, min_weight=0.4)

            # Features are not a standard conflict dimension — always include
            filters["preferred_features"] = profile.preferences.get("features", PreferenceCategory()).get_top(3, min_weight=0.4)

            filters["disliked_colors"]    = filters["explicit_negations"]["colors"]
            filters["disliked_brands"]    = filters["explicit_negations"]["brands"]
            filters["disliked_materials"] = filters["explicit_negations"]["materials"]
            filters["disliked_types"]     = filters["explicit_negations"]["categories"]
            filters["disliked_features"]  = filters["explicit_negations"]["features"]

        
        # Priority 3: Price range from profile
        if profile.price_range.get("min") or profile.price_range.get("max"):
            if profile.price_range.get("confidence", 0) > 0.5:
                filters["price_range"] = {
                    "min": profile.price_range.get("min"),
                    "max": profile.price_range.get("max")
                }
        
        # Override with session price if specified
        if "price_min" in session.explicit_constraints:
            if "price_range" not in filters:
                filters["price_range"] = {}
            filters["price_range"]["min"] = session.explicit_constraints["price_min"][0]
        
        if "price_max" in session.explicit_constraints:
            if "price_range" not in filters:
                filters["price_range"] = {}
            filters["price_range"]["max"] = session.explicit_constraints["price_max"][0]
        
        return filters

# Utility function for one-off processing
def process_user_message(
    user_id: str,
    message: str,
    profile_dict: Dict[str, Any],
    session_dict: Optional[Dict[str, Any]],
    llm_client
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """
    Convenience function for processing a message.
    Handles dict <-> object conversion.
    
    Args:
        user_id: User identifier
        message: User's message
        profile_dict: User profile as dictionary (from DB)
        session_dict: Session state as dictionary (or None)
        llm_client: Your LLM client
    
    Returns:
        (profile_dict, session_dict, context_dict)
    """
    
    # Convert dicts to objects
    profile = UserProfile.from_dict(profile_dict) if profile_dict else UserProfile(user_id=user_id)
    session = SessionState(**session_dict) if session_dict else None
    
    # Create engine
    extractor = PreferenceExtractor(llm_client)
    engine = PersonalizationEngine(extractor)
    
    # Process
    updated_profile, updated_session, context = engine.process_message(
        user_id,
        message,
        profile,
        session
    )
    
    # Convert back to dicts
    return (
        updated_profile.to_dict(),
        updated_session.to_dict(),
        context
    )
"""
Deterministic rules engine.
Decides WHERE extracted information should go and HOW to update.
"""

from typing import Dict, Any, Tuple
from .models import UserProfile, SessionState


class PersonalizationRules:
    """
    Rules engine that decides how to handle extracted information.
    
    This is PURE LOGIC - no ML, no LLM.
    """
    
    # Thresholds for decision making
    EXPLICIT_WEIGHT_DELTA = 0.5  # Strong signal
    REQUIREMENT_WEIGHT_DELTA = 0.2  # Medium signal (stated needs)
    INFERRED_WEIGHT_DELTA = 0.1  # Weak signal
    NEGATION_WEIGHT_DELTA = -0.3  # Reduce preference
    
    CONTRADICTION_THRESHOLD = 0.6  # If existing pref weight > this, flag contradiction
    
    @staticmethod
    def classify_update_type(
        intent_type: str,
        extracted: Dict[str, Any],
        profile: UserProfile,
        session: SessionState,
        user_message: str
    ) -> Dict[str, str]:
        """
        Classify how to handle the extracted information.
        
        Returns:
            {
                "profile_action": "hard_update" | "soft_update" | "no_update" | "confirm_needed",
                "session_action": "add_constraint" | "add_temporary" | "ignore",
                "reasoning": "explanation"
            }
        """
        
        # Rule 1: Gift detection → Never update profile
        if session.is_gift or extracted.get("signals", {}).get("is_gift"):
            return {
                "profile_action": "no_update",
                "session_action": "add_constraint",
                "reasoning": "Gift shopping - temporary intent only"
            }
        
        # Rule 2: Explicit preference → Hard update
        if intent_type == "explicit_preference":
            # Check for contradictions
            has_contradiction = PersonalizationRules._check_contradiction(extracted, profile)
            
            if has_contradiction:
                return {
                    "profile_action": "confirm_needed",
                    "session_action": "add_constraint",
                    "reasoning": "Contradicts existing strong preference - need confirmation"
                }
            
            return {
                "profile_action": "hard_update",
                "session_action": "add_constraint",
                "reasoning": "Explicit statement - high confidence update"
            }
        
        # Rule 3: Negation → Remove or downweight
        if intent_type == "negation":
            return {
                "profile_action": "remove_preference",
                "session_action": "add_constraint",
                "reasoning": "User explicitly rejecting - remove from profile"
            }
        
        # NEW Rule 4: Requirement → Soft update (medium confidence)
        if intent_type == "requirement":
            return {
                "profile_action": "requirement_update",
                "session_action": "add_constraint",
                "reasoning": "Stated requirement - medium confidence soft update"
            }

        EXPLICIT_PREFERENCE_VERBS = [
            "i like",
            "i prefer",
            "i love",
            "i usually like",
            "my preference is"
        ]

        # Rule 5: Query with explicit preference language → HARD profile update
        if intent_type == "query":
            text = user_message.lower()
            if any(v in text for v in EXPLICIT_PREFERENCE_VERBS):
                return {
                    "profile_action": "hard_update",
                    "session_action": "add_constraint",
                    "reasoning": "Explicit preference stated inside a query"
                }

            return {
                "profile_action": "no_update",
                "session_action": "add_constraint",
                "reasoning": "Pure browsing query"
            }
        
        # Rule 6: Clarification → depends on context
        if intent_type == "clarification":
            # If clarifying a preference change, do hard update
            if session.detected_contradiction:
                return {
                    "profile_action": "hard_update",
                    "session_action": "add_constraint",
                    "reasoning": "Confirmed preference change"
                }
            
            return {
                "profile_action": "soft_update",
                "session_action": "add_constraint",
                "reasoning": "Clarification provided - soft update"
            }
        
        # Default: no update
        return {
            "profile_action": "no_update",
            "session_action": "ignore",
            "reasoning": "Unclear intent - no action taken"
        }
    
    # Semantic opposites per dimension — used by _check_contradiction.
    # Each key maps to values that are meaningfully incompatible with it.
    _OPPOSITE_COLORS = {
        "bright":  ["neutral", "dark", "subtle", "muted"],
        "dark":    ["bright", "light", "pastel", "neon"],
        "neutral": ["bright", "bold", "neon", "vivid"],
        "light":   ["dark", "deep"],
        "pastel":  ["dark", "bold", "neon"],
        "neon":    ["neutral", "muted", "pastel", "dark"],
    }

    _OPPOSITE_MATERIALS = {
        "leather":    ["vegan", "synthetic", "fabric", "canvas"],
        "vegan":      ["leather", "suede", "exotic"],
        "suede":      ["vegan", "synthetic"],
        "canvas":     ["leather", "exotic"],
        "synthetic":  ["leather", "suede"],
        "exotic":     ["vegan", "canvas", "synthetic"],
    }

    _OPPOSITE_CATEGORIES = {
        # Formality axis
        "office bag":     ["gym bag", "beach bag", "backpack", "tote"],
        "work bag":       ["gym bag", "beach bag", "clutch"],
        "gym bag":        ["office bag", "work bag", "clutch", "evening bag"],
        "beach bag":      ["office bag", "work bag", "clutch"],
        "evening bag":    ["gym bag", "backpack", "tote"],
        "clutch":         ["gym bag", "backpack", "tote"],
        # Size axis
        "mini bag":       ["large tote", "travel bag", "weekender"],
        "travel bag":     ["mini bag", "clutch", "evening bag"],
        "weekender":      ["mini bag", "clutch"],
    }

    @staticmethod
    def _check_contradiction(extracted: Dict[str, Any], profile: UserProfile) -> bool:
        """
        Check if extracted preferences contradict existing strongly-held preferences.
        A contradiction occurs when:
          (a) the user asks for X that they have previously disliked, OR
          (b) the user asks for X but has a strong preference for a semantic opposite of X.

        Covers: colors, materials, brands, and bag categories.
        """
        opp_colors    = PersonalizationRules._OPPOSITE_COLORS
        opp_materials = PersonalizationRules._OPPOSITE_MATERIALS
        opp_categories = PersonalizationRules._OPPOSITE_CATEGORIES
        threshold     = PersonalizationRules.CONTRADICTION_THRESHOLD

        def _has_strong_opposite(pref_category, value: str, opposite_map: Dict) -> bool:
            """True if any semantic opposite of `value` is strongly liked in the profile."""
            for opp in opposite_map.get(value.lower(), []):
                opp_pref = pref_category.items.get(opp)
                if opp_pref and opp_pref.weight > threshold:
                    return True
            return False

        # --- Colors ---
        color_prefs = profile.preferences.get("colors")
        if color_prefs:
            for color in extracted.get("colors", []):
                c = color.lower()
                if c in color_prefs.disliked_items:
                    return True
                if _has_strong_opposite(color_prefs, c, opp_colors):
                    return True

        # --- Materials ---
        material_prefs = profile.preferences.get("materials")
        if material_prefs:
            for material in extracted.get("materials", []):
                m = material.lower()
                if m in material_prefs.disliked_items:
                    return True
                if _has_strong_opposite(material_prefs, m, opp_materials):
                    return True

        # --- Brands ---
        brand_prefs = profile.preferences.get("brands")
        if brand_prefs:
            for brand in extracted.get("brands", []):
                if brand.lower() in brand_prefs.disliked_items:
                    return True

        # --- Categories (bag types) ---
        category_prefs = profile.preferences.get("categories")
        if category_prefs:
            for cat in extracted.get("categories", []):
                c = cat.lower()
                if c in category_prefs.disliked_items:
                    return True
                if _has_strong_opposite(category_prefs, c, opp_categories):
                    return True

        return False
    
    @staticmethod
    def should_ask_clarification(classification: Dict[str, str]) -> Tuple[bool, str]:
        """
        Decide if we should ask user for clarification.
        
        Returns:
            (should_ask, clarification_message)
        """
        
        if classification["profile_action"] == "confirm_needed":
            return (
                True,
                "I notice you're looking at something different from your usual preferences. "
                "Are you shopping for yourself or is this for someone else?"
            )
        
        return (False, "")
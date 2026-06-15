"""
Counter and weight management logic.
Handles how preferences strengthen or decay over time.
"""

from datetime import datetime, timedelta
from typing import Dict
from .models import UserProfile, PreferenceCategory


class PreferenceCounter:
    """
    Manages preference weights and counts.
    """
    
    # Weight deltas for different signal types
    EXPLICIT_DELTA = 0.5
    INFERRED_DELTA = 0.1
    NEGATION_DELTA = -0.3
    DECAY_DELTA = -0.05  # Per month of inactivity
    
    # Thresholds
    MIN_WEIGHT = 0.1
    MAX_WEIGHT = 1.0
    ACTIVE_THRESHOLD = 0.3  # Preferences below this are considered weak
    
    @staticmethod
    def update_preference(
        category: PreferenceCategory,
        value: str,
        update_type: str  # "explicit", "inferred", "negation"
    ):
        """Update a single preference based on signal type."""
        
        if update_type == "explicit":
            category.add_or_update(value, PreferenceCounter.EXPLICIT_DELTA, explicit=True)
        
        elif update_type == "inferred":
            category.add_or_update(value, PreferenceCounter.INFERRED_DELTA, explicit=False)
        
        elif update_type == "negation":
            category.remove_or_downweight(value, full_remove=False)
    
    @staticmethod
    def apply_decay(profile: UserProfile, months_inactive: int = 1):
        """
        Apply time-based decay to preferences.
        Call periodically (e.g., on user login after a long absence).
        Iterates over a snapshot so deletions don't raise RuntimeError.
        """
        for _category_name, category in profile.preferences.items():
            to_delete = []
            for value, pref in list(category.items.items()):   # snapshot
                last_seen  = datetime.fromisoformat(pref.last_seen)
                months_old = (datetime.now() - last_seen).days / 30

                if months_old > months_inactive:
                    decay_amount = PreferenceCounter.DECAY_DELTA * (months_old / months_inactive)
                    pref.weight  = max(PreferenceCounter.MIN_WEIGHT, pref.weight + decay_amount)

                    if pref.weight < PreferenceCounter.ACTIVE_THRESHOLD and not pref.explicit:
                        to_delete.append(value)

            for value in to_delete:
                del category.items[value]
    
    @staticmethod
    def reinforce_from_behavior(profile: UserProfile, clicked_products: list):
        """
        Soft update preferences based on user behavior (clicks, views).
        
        Args:
            clicked_products: List of product dicts with attributes like color, brand, etc.
        """
        
        for product in clicked_products:
            # Infer preferences from repeated behavior
            if "color" in product:
                profile.preferences["colors"].add_or_update(
                    product["color"],
                    PreferenceCounter.INFERRED_DELTA,
                    explicit=False
                )
            
            if "brand" in product:
                profile.preferences["brands"].add_or_update(
                    product["brand"],
                    PreferenceCounter.INFERRED_DELTA,
                    explicit=False
                )
            
            # Update average price clicked
            if "price" in product:
                current_avg = profile.behavior_stats.get("avg_price_clicked")
                if current_avg:
                    # Rolling average
                    profile.behavior_stats["avg_price_clicked"] = (current_avg * 0.8 + product["price"] * 0.2)
                else:
                    profile.behavior_stats["avg_price_clicked"] = product["price"]
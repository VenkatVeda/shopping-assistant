"""
Data models for personalization system.
These are simple Python dataclasses - no DB dependencies.
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List
from datetime import datetime
import json


@dataclass
class Preference:
    """
    Single preference item with weight tracking.
    
    Example: User likes "black" color
    - weight: 0.8 (high confidence)
    - count: 12 (seen 12 times)
    - explicit: True (user said "I like black")
    """
    value: str
    weight: float = 0.5  # 0.0 to 1.0
    count: int = 1
    explicit: bool = False  # True if user explicitly stated
    first_seen: str = field(default_factory=lambda: datetime.now().isoformat())
    last_seen: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Preference':
        return cls(**data)


@dataclass
class PreferenceCategory:
    """
    Collection of preferences in one category (e.g., all colors).
    """
    items: Dict[str, Preference] = field(default_factory=dict)
    disliked_items: Dict[str, Preference] = field(default_factory=dict)
    
    def add_or_update(self, value: str, weight_delta: float = 0.0, explicit: bool = False):
        """Add new preference or update existing one."""
        value_lower = value.lower()
        
        # Remove from disliked if adding as liked
        if value_lower in self.disliked_items:
            del self.disliked_items[value_lower]

        # Weight bounds (defined locally to avoid circular import)
        MIN_WEIGHT = 0.1
        MAX_WEIGHT = 1.0

        if value_lower in self.items:
            pref = self.items[value_lower]
            pref.count += 1
            pref.weight = max(MIN_WEIGHT, min(MAX_WEIGHT, pref.weight + weight_delta))
            pref.last_seen = datetime.now().isoformat()
            if explicit:
                pref.explicit = True
        else:
            initial_weight = 1.0 if explicit else 0.3
            self.items[value_lower] = Preference(
                value=value_lower,
                weight=initial_weight,
                count=1,
                explicit=explicit
            )
    def add_disliked(self, value: str, weight: float = 0.8):
        """Add or update a disliked item."""
        value_lower = value.lower()

        # Remove from liked if adding as disliked
        if value_lower in self.items:
            del self.items[value_lower]

        self.disliked_items[value_lower] = Preference(
            value=value_lower,
            weight=weight,
            count=1,
            explicit=True
        )

    def remove_or_downweight(self, value: str, full_remove: bool = False):
        """Remove or reduce weight of a preference."""
        value_lower = value.lower()
        
        # Weight bounds and deltas (defined locally to avoid circular import)
        MIN_WEIGHT = 0.1
        NEGATION_DELTA = -0.3

        if value_lower in self.items:
            if full_remove:
                del self.items[value_lower]
            else:
                self.items[value_lower].weight = max(MIN_WEIGHT, self.items[value_lower].weight + NEGATION_DELTA)
    
    def get_top(self, n: int = 3, min_weight: float = 0.3) -> List[str]:
        """Get top N preferences above weight threshold."""
        filtered = [(v, p) for v, p in self.items.items() if p.weight >= min_weight]
        sorted_items = sorted(filtered, key=lambda x: (x[1].weight, x[1].count), reverse=True)
        return [item[0] for item in sorted_items[:n]]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "items": {k: v.to_dict() for k, v in self.items.items()},
            "disliked_items": {k: v.to_dict() for k, v in self.disliked_items.items()}
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PreferenceCategory':
        # Handle both old format (dict of preferences) and new format (with items/disliked_items)
        if "items" in data:
            items = {k: Preference.from_dict(v) for k, v in data.get("items", {}).items()}
            disliked_items = {k: Preference.from_dict(v) for k, v in data.get("disliked_items", {}).items()}
            return cls(items=items, disliked_items=disliked_items)
        else:
            # Backward compatibility with old format
            items = {k: Preference.from_dict(v) for k, v in data.items()}
            return cls(items=items)


@dataclass
class UserProfile:
    """
    Long-term user preferences (cross-session memory).
    Stored in database.
    """
    user_id: str
    preferences: Dict[str, PreferenceCategory] = field(default_factory=lambda: {
        "colors": PreferenceCategory(),
        "brands": PreferenceCategory(),
        "materials": PreferenceCategory(),
        "categories": PreferenceCategory(),  # Renamed from bag_types to match SearchPreferences
        "features": PreferenceCategory(),    # Renamed from attributes to match SearchPreferences
        "closure_types": PreferenceCategory(),  # NEW: zipper, magnetic, etc.
        "strap_types": PreferenceCategory(),    # NEW: adjustable, chain, etc.
        "sizes": PreferenceCategory(),          # NEW: small, medium, large
    })
    price_range: Dict[str, Any] = field(default_factory=lambda: {
        "min": None,
        "max": None,
        "confidence": 0.0
    })
    metadata: Dict[str, Any] = field(default_factory=lambda: {
        "name": None,
        "location": None,
        "age_group": None,
        "response_style": "balanced"  # short, balanced, detailed
    })
    behavior_stats: Dict[str, Any] = field(default_factory=lambda: {
        "avg_price_clicked": None,
        "total_sessions": 0,
        "last_active": None
    })
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database storage."""
        return {
            "user_id": self.user_id,
            "preferences": {k: v.to_dict() for k, v in self.preferences.items()},
            "price_range": self.price_range,
            "metadata": self.metadata,
            "behavior_stats": self.behavior_stats,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'UserProfile':
        """Load from database dictionary."""
        profile = cls(user_id=data["user_id"])
        profile.preferences = {
            k: PreferenceCategory.from_dict(v) 
            for k, v in data.get("preferences", {}).items()
        }
        profile.price_range = data.get("price_range", profile.price_range)
        profile.metadata = data.get("metadata", profile.metadata)
        profile.behavior_stats = data.get("behavior_stats", profile.behavior_stats)
        profile.created_at = data.get("created_at", profile.created_at)
        profile.updated_at = data.get("updated_at", profile.updated_at)
        return profile
    
    def get_summary(self) -> str:
        """Generate human-readable summary for LLM context."""
        parts = []
        
        # Colors
        top_colors = self.preferences.get("colors", PreferenceCategory()).get_top(2)
        if top_colors:
            parts.append(f"Prefers {', '.join(top_colors)} colors")
        
        # Brands
        top_brands = self.preferences.get("brands", PreferenceCategory()).get_top(2)
        if top_brands:
            parts.append(f"Likes {', '.join(top_brands)} brands")
        
        # Materials
        top_materials = self.preferences.get("materials", PreferenceCategory()).get_top(2)
        if top_materials:
            parts.append(f"Prefers {', '.join(top_materials)} material")
        
        # Categories (bag types)
        top_types = self.preferences.get("categories", PreferenceCategory()).get_top(2)
        if top_types:
            parts.append(f"Usually looks for {', '.join(top_types)}")
        
        # Price range
        if self.price_range["min"] or self.price_range["max"]:
            price_str = f"₹{self.price_range['min']}-{self.price_range['max']}"
            parts.append(f"Price range: {price_str}")
        
        return ". ".join(parts) if parts else "New user, no preferences yet"
    
    def add_inferred_preference(self, category: str, value: str, weight: float):
        """
        Add or reinforce an inferred preference in the named category.
        Explicit preferences are never overwritten by inferred signals.
        category must be one of: colors, brands, materials, categories, features,
                                  closure_types, strap_types, sizes.
        """
        pref_cat = self.preferences.get(category)
        if pref_cat is None:
            return

        existing = pref_cat.items.get(value.lower())
        if existing and existing.explicit:
            return   # never overwrite explicit preferences with inferred signals

        pref_cat.add_or_update(value, weight_delta=weight, explicit=False)

@dataclass
class SessionState:
    """
    Temporary session state (single-session memory).
    Stored in memory only - never persisted to DB.
    """
    session_id: str
    user_id: str
    
    # Current search intent
    explicit_constraints: Dict[str, List[str]] = field(default_factory=dict)
    # e.g., {"colors": ["red"], "price_max": [5000]}
    
    # Temporary overrides (for this session only)
    temporary_interests: Dict[str, List[str]] = field(default_factory=dict)
    
    # Context flags
    is_gift: bool = False
    is_special_occasion: bool = False
    detected_contradiction: bool = False
    
    # Conversation tracking
    turn_count: int = 0
    last_query: Optional[str] = None
    
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def reset_constraints(self):
        """Clear explicit constraints (e.g., after showing results)."""
        self.explicit_constraints = {}

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SessionState':
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)
# models/preferences.py
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from config.settings import PREFERENCE_SCHEMA

@dataclass
class UserPreferences:
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    brands: List[str] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    colors: List[str] = field(default_factory=list)
    materials: List[str] = field(default_factory=list)
    features: List[str] = field(default_factory=list)
    
    # New exclusion fields
    excluded_colors: List[str] = field(default_factory=list)
    excluded_brands: List[str] = field(default_factory=list)
    excluded_categories: List[str] = field(default_factory=list)
    excluded_materials: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "price_min": self.price_min,
            "price_max": self.price_max,
            "brands": self.brands,
            "categories": self.categories,
            "colors": self.colors,
            "materials": self.materials,
            "features": self.features,
            "excluded_colors": self.excluded_colors,
            "excluded_brands": self.excluded_brands,
            "excluded_categories": self.excluded_categories,
            "excluded_materials": self.excluded_materials
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'UserPreferences':
        return cls(
            price_min=data.get('price_min'),
            price_max=data.get('price_max'),
            brands=data.get('brands', []),
            categories=data.get('categories', []),
            colors=data.get('colors', []),
            materials=data.get('materials', []),
            features=data.get('features', []),
            excluded_colors=data.get('excluded_colors', []),
            excluded_brands=data.get('excluded_brands', []),
            excluded_categories=data.get('excluded_categories', []),
            excluded_materials=data.get('excluded_materials', [])
        )
    
    def has_active_preferences(self) -> bool:
        return any([
            self.price_min is not None,
            self.price_max is not None,
            self.brands,
            self.categories,
            self.colors,
            self.materials,
            self.features,
            self.excluded_colors,
            self.excluded_brands,
            self.excluded_categories
        ])
    
    def clear(self):
        self.price_min = None
        self.price_max = None
        self.brands.clear()
        self.categories.clear()
        self.colors.clear()
        self.materials.clear()
        self.features.clear()
        self.excluded_colors.clear()
        self.excluded_brands.clear()
        self.excluded_categories.clear()
        self.colors.clear()
        self.materials.clear()
        self.features.clear()

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
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "price_min": self.price_min,
            "price_max": self.price_max,
            "brands": self.brands,
            "categories": self.categories,
            "colors": self.colors,
            "materials": self.materials,
            "features": self.features
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
            features=data.get('features', [])
        )
    
    def has_active_preferences(self) -> bool:
        return any([
            self.price_min is not None,
            self.price_max is not None,
            self.brands,
            self.categories,
            self.colors,
            self.materials,
            self.features
        ])
    
    def clear(self):
        self.price_min = None
        self.price_max = None
        self.brands.clear()
        self.categories.clear()
        self.colors.clear()
        self.materials.clear()
        self.features.clear()

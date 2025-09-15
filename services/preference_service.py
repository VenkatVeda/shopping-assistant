# services/preference_service.py
import json
from typing import Dict, Any, Tuple, List
from models.preferences import UserPreferences
from config.settings import VALID_BRANDS, VALID_COLORS, BAG_CATEGORIES, BRAND_CORRECTIONS

class PreferenceService:
    def __init__(self, azure_service):
        self.azure_service = azure_service
        self.current_preferences = UserPreferences()
    
    def update_preferences(self, user_input: str) -> UserPreferences:
        if not self.azure_service.preference_chain:
            return self.current_preferences

        try:
            current_prefs_json = json.dumps(self.current_preferences.to_dict(), indent=2)
            response = self.azure_service.preference_chain.run(
                user_input=user_input,
                previous_prefs=current_prefs_json
            )
            
            new_preferences_dict = json.loads(response)
            new_preferences = UserPreferences.from_dict(new_preferences_dict)
            
            # Validate and merge preferences
            self._validate_and_merge(new_preferences, user_input)
            
        except Exception as e:
            print(f"Error updating preferences: {e}")
            
        return self.current_preferences
    
    def _validate_and_merge(self, new_prefs: UserPreferences, user_input: str):
        # Validate brands
        if new_prefs.brands:
            valid_brands, invalid_brands = self._validate_brands(new_prefs.brands)
            new_prefs.brands = valid_brands
            if invalid_brands:
                print(f"Warning: Unrecognized brands: {invalid_brands}")
        
        # Validate categories
        if new_prefs.categories:
            valid_categories, features_to_add = self._validate_categories(new_prefs.categories)
            new_prefs.categories = valid_categories
            new_prefs.features.extend(features_to_add)
        
        # Merge with current preferences
        append_mode = self._analyze_intent(user_input)
        self._merge_preferences(new_prefs, append_mode)
    
    def _validate_brands(self, brands: List[str]) -> Tuple[List[str], List[str]]:
        valid_brands = []
        invalid_brands = []
        
        for brand in brands:
            brand_lower = brand.lower().strip()
            
            # Exact match
            for valid_brand in VALID_BRANDS:
                if brand_lower == valid_brand.lower():
                    valid_brands.append(valid_brand)
                    break
            else:
                # Check corrections
                if brand_lower in BRAND_CORRECTIONS:
                    valid_brands.append(BRAND_CORRECTIONS[brand_lower])
                else:
                    invalid_brands.append(brand)
                    
        return valid_brands, invalid_brands
    
    def _validate_categories(self, categories: List[str]) -> Tuple[List[str], List[str]]:
        valid_categories = []
        features_to_add = []
        
        category_corrections = {
            "tote": "tote bag",
            "cross body": "crossbody",
            "cross-body": "crossbody",
            "shoulder": "shoulder bag",
            "laptop": "laptop bag",
            "duffle": "duffle bag",
            "duffel": "duffle bag",
        }
        
        for category in categories:
            category = category.lower().strip()
            
            if category in BAG_CATEGORIES:
                valid_categories.append(category)
            elif category in category_corrections:
                corrected = category_corrections[category]
                if corrected in BAG_CATEGORIES:
                    valid_categories.append(corrected)
            elif f"{category} bag" in BAG_CATEGORIES:
                valid_categories.append(f"{category} bag")
            elif category != "tote":  # Prevent tote from being added as feature
                features_to_add.append(category)
                
        return valid_categories, features_to_add
    
    def _analyze_intent(self, user_input: str) -> bool:
        user_input_lower = user_input.lower()
        append_indicators = ["also", "as well", "additionally", "and", "too", "along with"]
        return any(indicator in user_input_lower for indicator in append_indicators)
    
    def _merge_preferences(self, new_prefs: UserPreferences, append_mode: bool):
        if append_mode:
            self.current_preferences.brands.extend(new_prefs.brands)
            self.current_preferences.categories.extend(new_prefs.categories)
            self.current_preferences.colors.extend(new_prefs.colors)
            self.current_preferences.materials.extend(new_prefs.materials)
            self.current_preferences.features.extend(new_prefs.features)
            
            # Remove duplicates
            self.current_preferences.brands = list(dict.fromkeys(self.current_preferences.brands))
            self.current_preferences.categories = list(dict.fromkeys(self.current_preferences.categories))
            self.current_preferences.colors = list(dict.fromkeys(self.current_preferences.colors))
            self.current_preferences.materials = list(dict.fromkeys(self.current_preferences.materials))
            self.current_preferences.features = list(dict.fromkeys(self.current_preferences.features))
        else:
            # Replace preferences
            if new_prefs.brands:
                self.current_preferences.brands = new_prefs.brands
            if new_prefs.categories:
                self.current_preferences.categories = new_prefs.categories
            if new_prefs.colors:
                self.current_preferences.colors = new_prefs.colors
            if new_prefs.materials:
                self.current_preferences.materials = new_prefs.materials
            if new_prefs.features:
                self.current_preferences.features = new_prefs.features
        
        # Update price constraints
        if new_prefs.price_min is not None:
            self.current_preferences.price_min = new_prefs.price_min
        if new_prefs.price_max is not None:
            self.current_preferences.price_max = new_prefs.price_max
    
    def clear_preferences(self):
        self.current_preferences.clear()
    
    def get_summary(self) -> str:
        prefs = self.current_preferences
        parts = []
        
        if prefs.price_min and prefs.price_max:
            parts.append(f"Price: ${prefs.price_min}-${prefs.price_max}")
        elif prefs.price_min:
            parts.append(f"Price: Above ${prefs.price_min}")
        elif prefs.price_max:
            parts.append(f"Price: Under ${prefs.price_max}")
        
        if prefs.brands:
            parts.append(f"Brands: {', '.join(prefs.brands)}")
        if prefs.categories:
            parts.append(f"Categories: {', '.join(prefs.categories)}")
        if prefs.colors:
            parts.append(f"Colors: {', '.join(prefs.colors)}")
        if prefs.materials:
            parts.append(f"Materials: {', '.join(prefs.materials)}")
            
        return " | ".join(parts) if parts else "No active preferences set"

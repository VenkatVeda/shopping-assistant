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
            
            # Use LangSmith automatic tracing (no manual tracking needed)
            response = self.azure_service.preference_chain.run(
                user_input=user_input,
                previous_prefs=current_prefs_json
            )
            
            if response:
                new_preferences_dict = json.loads(response)
                new_preferences = UserPreferences.from_dict(new_preferences_dict)
                
                # 🚨 BACKUP EXCLUSION DETECTION - Fix LLM misses
                self._backup_exclusion_detection(user_input, new_preferences)
                
                # Validate and merge preferences
                self._validate_and_merge(new_preferences, user_input)
            
        except Exception as e:
            print(f"Error updating preferences: {e}")
            
        return self.current_preferences
    
    def _backup_exclusion_detection(self, user_input: str, preferences: UserPreferences):
        """
        Backup exclusion detection when LLM fails to catch exclusion language
        """
        print(f"🔧 BACKUP EXCLUSION: Processing '{user_input}'")
        import re
        user_lower = user_input.lower()
        
        def add_excluded_color(color, preferences):
            """Helper to safely add excluded colors"""
            if not hasattr(preferences, 'excluded_colors') or preferences.excluded_colors is None:
                preferences.excluded_colors = []
            if color not in preferences.excluded_colors:
                preferences.excluded_colors.append(color)
                print(f"🔧 BACKUP: Successfully added '{color}' to excluded_colors")
                return True
            else:
                print(f"🔧 BACKUP: '{color}' already in excluded_colors")
                return False
        
        # Pattern 1: "excluding [color] and [color]"
        excluding_match = re.search(r'excluding\s+([^.]+)', user_lower)
        if excluding_match:
            excluded_text = excluding_match.group(1)
            print(f"🔧 BACKUP: Found excluding pattern: '{excluded_text}'")
            # Extract colors from the excluded text
            for color in VALID_COLORS:
                if color.lower() in excluded_text:
                    print(f"🔧 BACKUP: Found color '{color}' to exclude")
                    add_excluded_color(color, preferences)
        
        # Pattern 2: "don't want [color]" or "no [color]"
        dont_want_patterns = [
            r"don\'?t\s+want\s+([^.]+)",
            r"no\s+([a-z]+)\s+bags?",
            r"avoid\s+([^.]+)",
            r"not\s+([a-z]+)\s+bags?"
        ]
        
        for pattern in dont_want_patterns:
            match = re.search(pattern, user_lower)
            if match:
                excluded_text = match.group(1)
                print(f"🔧 BACKUP: Found don't want pattern: '{excluded_text}'")
                for color in VALID_COLORS:
                    if color.lower() in excluded_text:
                        add_excluded_color(color, preferences)
        
        # Pattern 3: "everything but [color]", "anything except [color]"
        but_except_patterns = [
            r"everything\s+but\s+([^.]+)",
            r"anything\s+except\s+([^.]+)"
        ]
        
        for pattern in but_except_patterns:
            match = re.search(pattern, user_lower)
            if match:
                excluded_text = match.group(1)
                print(f"🔧 BACKUP: Found but/except pattern: '{excluded_text}'")
                for color in VALID_COLORS:
                    if color.lower() in excluded_text:
                        add_excluded_color(color, preferences)
    
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
            "tote": "tote bags",
            "tote bag": "tote bags",
            "cross body": "crossbody bags",
            "cross-body": "crossbody bags", 
            "crossbody": "crossbody bags",
            "shoulder": "shoulder bags",
            "shoulder bag": "shoulder bags",
            "laptop": "laptop bag",
            "duffle": "duffle bags",
            "duffel": "duffle bags",
            "duffle bag": "duffle bags",
            "clutch": "clutches",
            "backpack": "backpacks",
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
            
            # 🚨 FIX: Handle exclusion fields in append mode
            self.current_preferences.excluded_colors.extend(new_prefs.excluded_colors or [])
            self.current_preferences.excluded_brands.extend(new_prefs.excluded_brands or [])
            self.current_preferences.excluded_categories.extend(new_prefs.excluded_categories or [])
            
            # Remove duplicates
            self.current_preferences.brands = list(dict.fromkeys(self.current_preferences.brands))
            self.current_preferences.categories = list(dict.fromkeys(self.current_preferences.categories))
            self.current_preferences.colors = list(dict.fromkeys(self.current_preferences.colors))
            self.current_preferences.materials = list(dict.fromkeys(self.current_preferences.materials))
            self.current_preferences.features = list(dict.fromkeys(self.current_preferences.features))
            
            # Remove duplicates from exclusions
            self.current_preferences.excluded_colors = list(dict.fromkeys(self.current_preferences.excluded_colors))
            self.current_preferences.excluded_brands = list(dict.fromkeys(self.current_preferences.excluded_brands))
            self.current_preferences.excluded_categories = list(dict.fromkeys(self.current_preferences.excluded_categories))
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
            
            # 🚨 FIX: Handle exclusion fields in replace mode
            if new_prefs.excluded_colors:
                self.current_preferences.excluded_colors = new_prefs.excluded_colors
            if new_prefs.excluded_brands:
                self.current_preferences.excluded_brands = new_prefs.excluded_brands
            if new_prefs.excluded_categories:
                self.current_preferences.excluded_categories = new_prefs.excluded_categories
        
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
        
        # Add exclusions to summary
        if prefs.excluded_colors:
            parts.append(f"❌ Excluded Colors: {', '.join(prefs.excluded_colors)}")
        if prefs.excluded_brands:
            parts.append(f"❌ Excluded Brands: {', '.join(prefs.excluded_brands)}")
        if prefs.excluded_categories:
            parts.append(f"❌ Excluded Categories: {', '.join(prefs.excluded_categories)}")
            
        return " | ".join(parts) if parts else "No active preferences set"

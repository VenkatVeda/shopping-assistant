"""
Enhanced Preference Service with NER Integration
Combines NER extraction with LLM processing and state tracking
"""

import json
from typing import Dict, Any, Tuple, List, Optional
from models.preferences import UserPreferences  
from models.enhanced_state import ConversationState, track_ner_preference_extraction, PreferenceSource
from services.ner_service import get_ner_service, EntityType, NERResult
from config.settings import VALID_BRANDS, VALID_COLORS, BAG_CATEGORIES, BRAND_CORRECTIONS
import logging


class EnhancedPreferenceService:
    """Enhanced preference service with NER integration"""
    
    def __init__(self, azure_service, enable_ner: bool = True):
        self.azure_service = azure_service
        self.enable_ner = enable_ner
        self.current_preferences = UserPreferences()
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # Initialize NER service
        if self.enable_ner:
            self.ner_service = get_ner_service()
        else:
            self.ner_service = None
    
    def update_preferences(self, user_input: str, 
                         conversation_state: Optional[ConversationState] = None) -> UserPreferences:
        """
        Enhanced preference update with NER integration and state tracking
        
        Returns:
            Updated preferences (compatible with original interface)
        """
        extraction_metadata = {
            'input_text': user_input,
            'ner_enabled': self.enable_ner,
            'extraction_methods_used': [],
            'entity_extractions': {},
            'confidence_scores': {},
            'processing_time_ms': 0
        }
        
        # Start NER session if state tracking enabled
        if conversation_state:
            session_id = conversation_state.start_ner_session(user_input)
            extraction_metadata['ner_session_id'] = session_id
        
        try:
            # Step 1: NER-based extraction (primary method)
            if self.enable_ner and self.ner_service:
                ner_preferences, ner_metadata = self._extract_with_ner(user_input, conversation_state)
                extraction_metadata['extraction_methods_used'].append('ner')
                extraction_metadata.update(ner_metadata)
                
                # Apply NER results
                self._apply_ner_preferences(ner_preferences, ner_metadata)
            
            # Step 2: LLM-based extraction (fallback/enhancement)
            if self.azure_service.preference_chain:
                llm_preferences, llm_metadata = self._extract_with_llm(user_input)
                extraction_metadata['extraction_methods_used'].append('llm')
                extraction_metadata['llm_metadata'] = llm_metadata
                
                # Merge LLM results with NER results
                self._merge_llm_preferences(llm_preferences, user_input)
            
            # Step 3: Backup pattern-based exclusion detection
            self._backup_exclusion_detection(user_input, self.current_preferences)
            extraction_metadata['extraction_methods_used'].append('pattern_based')
            
            # Step 4: Validation and normalization
            self._validate_and_merge(self.current_preferences, user_input)
            
        except Exception as e:
            self.logger.error(f"Error in preference update: {e}")
            extraction_metadata['error'] = str(e)
        
        # Complete NER session
        if conversation_state and conversation_state.current_ner_session:
            conversation_state.complete_ner_session()
        
        return self.current_preferences
    
    def get_summary(self) -> str:
        """Get a summary of current preferences (compatible with original interface)"""
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
    
    def update_preferences_with_metadata(self, user_input: str, 
                                       conversation_state: Optional[ConversationState] = None) -> Tuple[UserPreferences, Dict[str, Any]]:
        """
        Enhanced preference update that returns metadata for advanced use cases
        
        Returns:
            Tuple of (updated_preferences, extraction_metadata)
        """
        extraction_metadata = {
            'input_text': user_input,
            'ner_enabled': self.enable_ner,
            'extraction_methods_used': [],
            'entity_extractions': {},
            'confidence_scores': {},
            'processing_time_ms': 0
        }

        # Start NER session if state tracking enabled
        if conversation_state:
            session_id = conversation_state.start_ner_session(user_input)
            extraction_metadata['ner_session_id'] = session_id

        try:
            # Step 1: NER-based extraction (primary method)
            if self.enable_ner and self.ner_service:
                ner_preferences, ner_metadata = self._extract_with_ner(user_input, conversation_state)
                extraction_metadata['extraction_methods_used'].append('ner')
                extraction_metadata.update(ner_metadata)
                
                # Apply NER results
                self._apply_ner_preferences(ner_preferences, ner_metadata)

            # Step 2: LLM-based extraction (fallback/enhancement)
            llm_preferences, llm_metadata = self._extract_with_llm(user_input)
            extraction_metadata['extraction_methods_used'].append('llm')
            extraction_metadata['llm_metadata'] = llm_metadata
            
            # Apply LLM results
            self._apply_llm_preferences(llm_preferences, llm_metadata, conversation_state)
            
        except Exception as e:
            self.logger.error(f"Error in preference update: {e}")
            extraction_metadata['error'] = str(e)
        
        # Complete NER session
        if conversation_state and conversation_state.current_ner_session:
            conversation_state.complete_ner_session()

        return self.current_preferences, extraction_metadata
    
    def _extract_with_ner(self, user_input: str, 
                         conversation_state: Optional[ConversationState] = None) -> Tuple[UserPreferences, Dict[str, Any]]:
        """Extract preferences using NER service"""
        import time
        start_time = time.time()
        
        # Run NER extraction
        ner_result: NERResult = self.ner_service.extract_entities(user_input)
        
        processing_time = (time.time() - start_time) * 1000
        
        # Create preferences from NER results
        ner_preferences = UserPreferences()
        entity_extractions = {}
        confidence_scores = {}
        
        # Extract brands
        brands = ner_result.get_unique_values_by_type(EntityType.BRAND)
        if brands:
            ner_preferences.brands = brands
            entity_extractions['brands'] = brands
            brand_entities = ner_result.get_entities_by_type(EntityType.BRAND)
            confidence_scores['brands'] = [e.confidence for e in brand_entities]
            
            # Track in conversation state
            if conversation_state:
                track_ner_preference_extraction(
                    conversation_state, 'brand', brands, 
                    confidence_scores['brands'], 'ner_brand_extractor'
                )
        
        # Extract colors
        colors = ner_result.get_unique_values_by_type(EntityType.COLOR)
        if colors:
            ner_preferences.colors = colors
            entity_extractions['colors'] = colors
            color_entities = ner_result.get_entities_by_type(EntityType.COLOR)
            confidence_scores['colors'] = [e.confidence for e in color_entities]
            
            if conversation_state:
                track_ner_preference_extraction(
                    conversation_state, 'color', colors,
                    confidence_scores['colors'], 'ner_color_extractor'
                )
        
        # Extract categories
        categories = ner_result.get_unique_values_by_type(EntityType.CATEGORY)
        if categories:
            ner_preferences.categories = categories
            entity_extractions['categories'] = categories
            category_entities = ner_result.get_entities_by_type(EntityType.CATEGORY)
            confidence_scores['categories'] = [e.confidence for e in category_entities]
            
            if conversation_state:
                track_ner_preference_extraction(
                    conversation_state, 'category', categories,
                    confidence_scores['categories'], 'ner_category_extractor'
                )
        
        # Extract prices
        price_entities = ner_result.get_entities_by_type(EntityType.PRICE)
        if price_entities:
            for price_entity in price_entities:
                price_type = price_entity.metadata.get('price_type', 'exact')
                
                if price_type == 'min':
                    # Set minimum price
                    price_val = price_entity.metadata.get('price_value')
                    if price_val and (ner_preferences.price_min is None or price_val > ner_preferences.price_min):
                        ner_preferences.price_min = price_val
                
                elif price_type == 'max':
                    # Set maximum price
                    price_val = price_entity.metadata.get('price_value')
                    if price_val and (ner_preferences.price_max is None or price_val < ner_preferences.price_max):
                        ner_preferences.price_max = price_val
                
                elif price_type == 'range':
                    # Set price range
                    min_price = price_entity.metadata.get('price_min')
                    max_price = price_entity.metadata.get('price_max')
                    if min_price:
                        ner_preferences.price_min = min_price
                    if max_price:
                        ner_preferences.price_max = max_price
                
                elif price_type == 'around':
                    # Set approximate price range (±20%)
                    price_val = price_entity.metadata.get('price_value')
                    if price_val:
                        margin = price_val * 0.2
                        ner_preferences.price_min = max(0, price_val - margin)
                        ner_preferences.price_max = price_val + margin
            
            entity_extractions['prices'] = [e.value for e in price_entities]
            confidence_scores['prices'] = [e.confidence for e in price_entities]
            
            if conversation_state:
                track_ner_preference_extraction(
                    conversation_state, 'price', [f"${ner_preferences.price_min}-{ner_preferences.price_max}"],
                    [max(confidence_scores['prices'])], 'ner_price_extractor'
                )
        
        # Handle exclusions
        exclusions = ner_result.get_entities_by_type(EntityType.EXCLUSION)
        if exclusions:
            self._process_ner_exclusions(exclusions, ner_preferences, conversation_state)
            entity_extractions['exclusions'] = [e.value for e in exclusions]
            confidence_scores['exclusions'] = [e.confidence for e in exclusions]
        
        # Handle UI commands (detect and log, but don't add to preferences)
        ui_commands = ner_result.get_entities_by_type(EntityType.UI_COMMAND)
        if ui_commands:
            entity_extractions['ui_commands'] = [e.value for e in ui_commands]
            confidence_scores['ui_commands'] = [e.confidence for e in ui_commands]
            
            # Log UI commands for debugging
            self.logger.info(f"Detected UI commands: {[cmd.value for cmd in ui_commands]}")
        
        metadata = {
            'ner_processing_time_ms': processing_time,
            'total_entities_found': len(ner_result.entities),
            'strategies_used': [s.value for s in ner_result.strategies_used],
            'entity_extractions': entity_extractions,
            'confidence_scores': confidence_scores,
            'ner_summary': self.ner_service.get_extraction_summary(ner_result),
            'ui_commands_detected': len(ui_commands) > 0
        }
        
        return ner_preferences, metadata
    
    def _process_ner_exclusions(self, exclusions: List, ner_preferences: UserPreferences,
                              conversation_state: Optional[ConversationState] = None):
        """Process exclusion entities from NER"""
        for exclusion in exclusions:
            excluded_text = exclusion.value.lower()
            
            # Check for colors in exclusion text
            for color in VALID_COLORS:
                if color.lower() in excluded_text:
                    if not ner_preferences.excluded_colors:
                        ner_preferences.excluded_colors = []
                    if color not in ner_preferences.excluded_colors:
                        ner_preferences.excluded_colors.append(color)
                        
                        if conversation_state:
                            conversation_state.add_ner_extraction(
                                'excluded_color', color, exclusion.confidence,
                                'ner_exclusion_extractor', 'exclusion_pattern_matching'
                            )
            
            # Check for brands in exclusion text
            for brand in VALID_BRANDS:
                if brand.lower() in excluded_text:
                    if not ner_preferences.excluded_brands:
                        ner_preferences.excluded_brands = []
                    if brand not in ner_preferences.excluded_brands:
                        ner_preferences.excluded_brands.append(brand)
                        
                        if conversation_state:
                            conversation_state.add_ner_extraction(
                                'excluded_brand', brand, exclusion.confidence,
                                'ner_exclusion_extractor', 'exclusion_pattern_matching'
                            )
            
            # Check for categories in exclusion text
            for category in BAG_CATEGORIES:
                if category.lower() in excluded_text:
                    if not ner_preferences.excluded_categories:
                        ner_preferences.excluded_categories = []
                    if category not in ner_preferences.excluded_categories:
                        ner_preferences.excluded_categories.append(category)
                        
                        if conversation_state:
                            conversation_state.add_ner_extraction(
                                'excluded_category', category, exclusion.confidence,
                                'ner_exclusion_extractor', 'exclusion_pattern_matching'
                            )
    
    def _extract_with_llm(self, user_input: str) -> Tuple[UserPreferences, Dict[str, Any]]:
        """Extract preferences using LLM (fallback/enhancement method)"""
        try:
            # Use cached extraction if available, fallback to direct chain
            if hasattr(self.azure_service, 'extract_preferences_cached'):
                # Use the cached method (returns dict directly)
                new_preferences_dict = self.azure_service.extract_preferences_cached(
                    user_input, 
                    self.current_preferences.to_dict()
                )
                response = json.dumps(new_preferences_dict)
                metadata = {
                    'llm_response_raw': response,
                    'llm_preferences_parsed': new_preferences_dict,
                    'success': True
                }
            elif hasattr(self.azure_service, 'run_with_tracking') and self.azure_service.preference_chain:
                # Use run_with_tracking to capture metrics
                current_prefs_json = json.dumps(self.current_preferences.to_dict(), indent=2)
                response, metrics = self.azure_service.run_with_tracking(
                    self.azure_service.preference_chain,
                    {
                        "user_input": user_input,
                        "previous_prefs": current_prefs_json
                    }
                )
                new_preferences_dict = json.loads(response)
                
                metadata = {
                    'llm_response_raw': response,
                    'llm_preferences_parsed': new_preferences_dict,
                    'success': True,
                    'metrics': metrics  # Include metrics in metadata
                }
            else:
                # Fallback to direct chain call (returns JSON string)
                current_prefs_json = json.dumps(self.current_preferences.to_dict(), indent=2)
                response = self.azure_service.preference_chain.run(
                    user_input=user_input,
                    previous_prefs=current_prefs_json
                )
                new_preferences_dict = json.loads(response)
                metadata = {
                    'llm_response_raw': response,
                    'llm_preferences_parsed': new_preferences_dict,
                    'success': True
                }
                
            llm_preferences = UserPreferences.from_dict(new_preferences_dict)
            return llm_preferences, metadata
            
        except Exception as e:
            self.logger.error(f"LLM extraction failed: {e}")
            return UserPreferences(), {
                'success': False,
                'error': str(e)
            }
    
    def _apply_ner_preferences(self, ner_preferences: UserPreferences, metadata: Dict[str, Any]):
        """Apply NER-extracted preferences to current preferences"""
        # For NER, we generally trust the extracted entities and add them
        
        if ner_preferences.brands:
            self.current_preferences.brands.extend([
                b for b in ner_preferences.brands 
                if b not in self.current_preferences.brands
            ])
        
        if ner_preferences.colors:
            self.current_preferences.colors.extend([
                c for c in ner_preferences.colors
                if c not in self.current_preferences.colors
            ])
        
        if ner_preferences.categories:
            self.current_preferences.categories.extend([
                cat for cat in ner_preferences.categories
                if cat not in self.current_preferences.categories
            ])
        
        # Handle prices
        if ner_preferences.price_min is not None:
            self.current_preferences.price_min = ner_preferences.price_min
        
        if ner_preferences.price_max is not None:
            self.current_preferences.price_max = ner_preferences.price_max
        
        # Handle exclusions
        if ner_preferences.excluded_colors:
            if not self.current_preferences.excluded_colors:
                self.current_preferences.excluded_colors = []
            self.current_preferences.excluded_colors.extend([
                c for c in ner_preferences.excluded_colors
                if c not in self.current_preferences.excluded_colors
            ])
        
        if ner_preferences.excluded_brands:
            if not self.current_preferences.excluded_brands:
                self.current_preferences.excluded_brands = []
            self.current_preferences.excluded_brands.extend([
                b for b in ner_preferences.excluded_brands
                if b not in self.current_preferences.excluded_brands
            ])
        
        if ner_preferences.excluded_categories:
            if not self.current_preferences.excluded_categories:
                self.current_preferences.excluded_categories = []
            self.current_preferences.excluded_categories.extend([
                c for c in ner_preferences.excluded_categories
                if c not in self.current_preferences.excluded_categories
            ])
    
    def _apply_llm_preferences(self, llm_preferences: UserPreferences, llm_metadata: Dict[str, Any], conversation_state=None):
        """Apply LLM-extracted preferences with exclusion conflict resolution"""
        
        # Handle exclusions first (they take priority)
        self._process_llm_exclusions(llm_preferences, llm_metadata, conversation_state)
        
        # Then apply positive preferences
        self._merge_llm_preferences(llm_preferences, "")  # Use empty string as user_input since we have the preferences
        
        # Log the application
        self.logger.info(f"Applied LLM preferences: {llm_preferences}")
    
    def _process_llm_exclusions(self, llm_preferences: UserPreferences, llm_metadata: Dict[str, Any], conversation_state=None):
        """Process LLM-detected exclusions with conflict resolution"""
        
        # Handle excluded colors
        if llm_preferences.excluded_colors:
            if not self.current_preferences.excluded_colors:
                self.current_preferences.excluded_colors = []
            
            for excluded_color in llm_preferences.excluded_colors:
                # Add to exclusions if not already there
                if excluded_color not in self.current_preferences.excluded_colors:
                    self.current_preferences.excluded_colors.append(excluded_color)
                
                # Remove from positive colors if present (conflict resolution)
                if excluded_color in self.current_preferences.colors:
                    self.current_preferences.colors.remove(excluded_color)
                    self.logger.info(f"Resolved conflict: removed '{excluded_color}' from colors due to exclusion")
        
        # Handle excluded brands
        if llm_preferences.excluded_brands:
            if not self.current_preferences.excluded_brands:
                self.current_preferences.excluded_brands = []
            
            for excluded_brand in llm_preferences.excluded_brands:
                if excluded_brand not in self.current_preferences.excluded_brands:
                    self.current_preferences.excluded_brands.append(excluded_brand)
                
                # Remove from positive brands if present
                if excluded_brand in self.current_preferences.brands:
                    self.current_preferences.brands.remove(excluded_brand)
                    self.logger.info(f"Resolved conflict: removed '{excluded_brand}' from brands due to exclusion")
        
        # Handle excluded categories  
        if llm_preferences.excluded_categories:
            if not self.current_preferences.excluded_categories:
                self.current_preferences.excluded_categories = []
            
            for excluded_cat in llm_preferences.excluded_categories:
                if excluded_cat not in self.current_preferences.excluded_categories:
                    self.current_preferences.excluded_categories.append(excluded_cat)
                
                # Remove from positive categories if present
                if excluded_cat in self.current_preferences.categories:
                    self.current_preferences.categories.remove(excluded_cat)
                    self.logger.info(f"Resolved conflict: removed '{excluded_cat}' from categories due to exclusion")

    def _merge_llm_preferences(self, llm_preferences: UserPreferences, user_input: str):
        """Merge LLM-extracted preferences with current preferences"""
        # LLM results can override/supplement NER results based on intent analysis
        append_mode = self._analyze_intent(user_input)
        
        if append_mode:
            # Add to existing preferences
            if llm_preferences.brands:
                self.current_preferences.brands.extend([
                    b for b in llm_preferences.brands
                    if b not in self.current_preferences.brands
                ])
            
            if llm_preferences.colors:
                self.current_preferences.colors.extend([
                    c for c in llm_preferences.colors
                    if c not in self.current_preferences.colors
                ])
                
            if llm_preferences.categories:
                self.current_preferences.categories.extend([
                    cat for cat in llm_preferences.categories
                    if cat not in self.current_preferences.categories
                ])
        else:
            # Replace existing preferences
            if llm_preferences.brands:
                self.current_preferences.brands = llm_preferences.brands
            
            if llm_preferences.colors:
                self.current_preferences.colors = llm_preferences.colors
                
            if llm_preferences.categories:
                self.current_preferences.categories = llm_preferences.categories
        
        # Always merge other fields
        if llm_preferences.price_min is not None:
            self.current_preferences.price_min = llm_preferences.price_min
        if llm_preferences.price_max is not None:
            self.current_preferences.price_max = llm_preferences.price_max
        
        if llm_preferences.materials:
            self.current_preferences.materials = llm_preferences.materials
        if llm_preferences.features:
            # Filter out UI commands from features
            filtered_features = self._filter_ui_commands_from_features(llm_preferences.features)
            self.current_preferences.features = filtered_features
        
        # Apply exclusions from LLM
        if llm_preferences.excluded_colors:
            if not self.current_preferences.excluded_colors:
                self.current_preferences.excluded_colors = []
            self.current_preferences.excluded_colors.extend([
                c for c in llm_preferences.excluded_colors
                if c not in self.current_preferences.excluded_colors
            ])
            
            # Remove conflicting colors from regular preferences
            for excluded_color in llm_preferences.excluded_colors:
                if excluded_color in self.current_preferences.colors:
                    self.current_preferences.colors.remove(excluded_color)
            
        if llm_preferences.excluded_brands:
            if not self.current_preferences.excluded_brands:
                self.current_preferences.excluded_brands = []
            self.current_preferences.excluded_brands.extend([
                b for b in llm_preferences.excluded_brands
                if b not in self.current_preferences.excluded_brands
            ])
            
            # Remove conflicting brands from regular preferences
            for excluded_brand in llm_preferences.excluded_brands:
                if excluded_brand in self.current_preferences.brands:
                    self.current_preferences.brands.remove(excluded_brand)
            
        if llm_preferences.excluded_categories:
            if not self.current_preferences.excluded_categories:
                self.current_preferences.excluded_categories = []
            self.current_preferences.excluded_categories.extend([
                cat for cat in llm_preferences.excluded_categories
                if cat not in self.current_preferences.excluded_categories
            ])
            
            # Remove conflicting categories from regular preferences
            for excluded_category in llm_preferences.excluded_categories:
                if excluded_category in self.current_preferences.categories:
                    self.current_preferences.categories.remove(excluded_category)
    
    def _filter_ui_commands_from_features(self, features: List[str]) -> List[str]:
        """Filter out UI commands from feature list"""
        if not features:
            return features
        
        # Get UI command extractor to check patterns
        ui_extractor = self.ner_service.extractors.get(EntityType.UI_COMMAND)
        if not ui_extractor:
            return features
        
        filtered_features = []
        
        for feature in features:
            # Check if this feature matches any UI command pattern
            is_ui_command = False
            
            for pattern, _ in ui_extractor.compiled_patterns:
                if pattern.search(feature):
                    is_ui_command = True
                    self.logger.info(f"Filtered out UI command from features: '{feature}'")
                    break
            
            if not is_ui_command:
                filtered_features.append(feature)
        
        return filtered_features
    
    def _backup_exclusion_detection(self, user_input: str, preferences: UserPreferences):
        """Backup exclusion detection using pattern matching"""
        import re
        user_lower = user_input.lower()
        
        def add_excluded_color(color, preferences):
            if not hasattr(preferences, 'excluded_colors') or preferences.excluded_colors is None:
                preferences.excluded_colors = []
            if color not in preferences.excluded_colors:
                preferences.excluded_colors.append(color)
        
        # Pattern-based exclusion detection (from original service)
        excluding_match = re.search(r'excluding\s+([^.]+)', user_lower)
        if excluding_match:
            excluded_text = excluding_match.group(1)
            for color in VALID_COLORS:
                if color.lower() in excluded_text:
                    add_excluded_color(color, preferences)
        
        # "don't want", "no", "avoid" patterns
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
                for color in VALID_COLORS:
                    if color.lower() in excluded_text:
                        add_excluded_color(color, preferences)
        
        # "everything but", "anything except" patterns
        but_except_patterns = [
            r"everything\s+but\s+([^.]+)",
            r"anything\s+except\s+([^.]+)"
        ]
        
        for pattern in but_except_patterns:
            match = re.search(pattern, user_lower)
            if match:
                excluded_text = match.group(1)
                for color in VALID_COLORS:
                    if color.lower() in excluded_text:
                        add_excluded_color(color, preferences)
    
    def _validate_and_merge(self, preferences: UserPreferences, user_input: str):
        """Validate and normalize preferences"""
        # Validate brands
        if preferences.brands:
            valid_brands, invalid_brands = self._validate_brands(preferences.brands)
            preferences.brands = valid_brands
            if invalid_brands:
                self.logger.warning(f"Unrecognized brands: {invalid_brands}")
        
        # Validate categories
        if preferences.categories:
            valid_categories, features_to_add = self._validate_categories(preferences.categories)
            preferences.categories = valid_categories
            preferences.features.extend(features_to_add)
    
    def _validate_brands(self, brands: List[str]) -> Tuple[List[str], List[str]]:
        """Validate brand names"""
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
        """Validate and normalize categories"""
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
            "backpack": "backpacks",
            "clutch": "clutches",
            "duffle": "duffle bags",
            "duffel": "duffle bags",
            "laptop": "laptop bag",
            "brief case": "briefcase",
            "brief-case": "briefcase"
        }
        
        for category in categories:
            category_lower = category.lower().strip()
            
            # Check if it's a valid category
            if category_lower in BAG_CATEGORIES or category in BAG_CATEGORIES:
                valid_categories.append(category)
            # Check corrections
            elif category_lower in category_corrections:
                corrected = category_corrections[category_lower]
                valid_categories.append(corrected)
            else:
                # Treat as feature if not a valid category
                features_to_add.append(category)
        
        return valid_categories, features_to_add
    
    def _analyze_intent(self, user_input: str) -> bool:
        """Analyze user intent to determine append vs replace mode"""
        user_lower = user_input.lower()
        
        # Replace mode indicators
        replace_keywords = ['instead', 'change to', 'switch to', 'only', 'just', 'replace']
        if any(keyword in user_lower for keyword in replace_keywords):
            return False
        
        # Append mode indicators (default)
        append_keywords = ['also', 'and', 'plus', 'along with', 'in addition', 'too']
        if any(keyword in user_lower for keyword in append_keywords):
            return True
        
        # Default to append mode for safer UX
        return True
    
    def get_summary(self) -> str:
        """Generate human-readable summary of preferences"""
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
        if prefs.features:
            parts.append(f"Features: {', '.join(prefs.features)}")
        
        # Add exclusions to summary
        if prefs.excluded_colors:
            parts.append(f"❌ Excluded Colors: {', '.join(prefs.excluded_colors)}")
        if prefs.excluded_brands:
            parts.append(f"❌ Excluded Brands: {', '.join(prefs.excluded_brands)}")
        if prefs.excluded_categories:
            parts.append(f"❌ Excluded Categories: {', '.join(prefs.excluded_categories)}")
        
        return " | ".join(parts) if parts else "No active preferences set"
    
    def get_extraction_diagnostics(self) -> Dict[str, Any]:
        """Get diagnostic information about extraction capabilities"""
        diagnostics = {
            'ner_enabled': self.enable_ner,
            'ner_available': self.ner_service is not None,
            'llm_available': self.azure_service.preference_chain is not None,
            'supported_entities': [],
            'extraction_strategies': []
        }
        
        if self.ner_service:
            diagnostics['supported_entities'] = [et.value for et in EntityType]
            diagnostics['extraction_strategies'] = ['dictionary_lookup', 'regex_patterns', 'fuzzy_matching']
            
            if hasattr(self.ner_service, 'nlp') and self.ner_service.nlp:
                diagnostics['spacy_available'] = True
                diagnostics['extraction_strategies'].append('spacy_ner')
            else:
                diagnostics['spacy_available'] = False
        
        if self.azure_service.preference_chain:
            diagnostics['extraction_strategies'].append('llm_extraction')
        
        diagnostics['extraction_strategies'].append('pattern_based_backup')
        
        return diagnostics
    
    def clear_preferences(self):
        """Clear all current preferences - for compatibility with original interface"""
        self.current_preferences.clear()
        self.logger.info("Preferences cleared")


# Factory function for backward compatibility
def create_preference_service(azure_service, enable_ner: bool = True):
    """Create preference service with optional NER integration"""
    return EnhancedPreferenceService(azure_service, enable_ner=enable_ner)
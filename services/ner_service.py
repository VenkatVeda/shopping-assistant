"""
Named Entity Recognition (NER) Service for Shopping Assistant
Modular NER system to identify brands, colors, categories and track extraction state
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Set, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import re
import logging
from difflib import SequenceMatcher
from collections import defaultdict

# Try importing spaCy, fallback gracefully if not available
try:
    import spacy
    from spacy.lang.en import English
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False
    spacy = None

from config.settings import VALID_BRANDS, VALID_COLORS, BAG_CATEGORIES, BRAND_CORRECTIONS


class ExtractionStrategy(Enum):
    """Available entity extraction strategies"""
    SPACY_NER = "spacy_ner"
    REGEX_PATTERNS = "regex_patterns"
    FUZZY_MATCHING = "fuzzy_matching"
    DICTIONARY_LOOKUP = "dictionary_lookup"
    LLM_EXTRACTION = "llm_extraction"


class EntityType(Enum):
    """Supported entity types"""
    BRAND = "brand"
    COLOR = "color"
    CATEGORY = "category"
    PRICE = "price"
    MATERIAL = "material"
    FEATURE = "feature"
    EXCLUSION = "exclusion"
    UI_COMMAND = "ui_command"


@dataclass
class EntityExtraction:
    """Represents a single entity extraction result"""
    entity_type: EntityType
    value: str
    confidence: float
    start_pos: int
    end_pos: int
    source_text: str
    extraction_strategy: ExtractionStrategy
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __str__(self):
        return f"{self.entity_type.value}:{self.value}({self.confidence:.2f})"


@dataclass
class NERResult:
    """Complete NER processing result"""
    input_text: str
    entities: List[EntityExtraction]
    processing_time_ms: float
    strategies_used: Set[ExtractionStrategy]
    
    def get_entities_by_type(self, entity_type: EntityType) -> List[EntityExtraction]:
        """Get all entities of a specific type"""
        return [e for e in self.entities if e.entity_type == entity_type]
    
    def get_unique_values_by_type(self, entity_type: EntityType) -> List[str]:
        """Get unique entity values for a type, sorted by confidence"""
        entities = self.get_entities_by_type(entity_type)
        # Sort by confidence descending, then deduplicate
        seen = set()
        result = []
        for entity in sorted(entities, key=lambda x: x.confidence, reverse=True):
            if entity.value.lower() not in seen:
                seen.add(entity.value.lower())
                result.append(entity.value)
        return result


class BaseEntityExtractor(ABC):
    """Abstract base class for entity extractors"""
    
    def __init__(self, entity_type: EntityType, strategy: ExtractionStrategy):
        self.entity_type = entity_type
        self.strategy = strategy
        self.logger = logging.getLogger(f"{self.__class__.__name__}")
    
    @abstractmethod
    def extract(self, text: str) -> List[EntityExtraction]:
        """Extract entities from text"""
        pass
    
    def _create_extraction(self, value: str, confidence: float, start: int, 
                          end: int, text: str, **metadata) -> EntityExtraction:
        """Helper to create EntityExtraction objects"""
        return EntityExtraction(
            entity_type=self.entity_type,
            value=value,
            confidence=confidence,
            start_pos=start,
            end_pos=end,
            source_text=text,
            extraction_strategy=self.strategy,
            metadata=metadata
        )


class BrandExtractor(BaseEntityExtractor):
    """Extract brand names using multiple strategies"""
    
    def __init__(self):
        super().__init__(EntityType.BRAND, ExtractionStrategy.DICTIONARY_LOOKUP)
        self.valid_brands = VALID_BRANDS
        self.brand_corrections = BRAND_CORRECTIONS
        
        # Compile regex patterns for common brand abbreviations
        self.brand_patterns = self._compile_brand_patterns()
    
    def _compile_brand_patterns(self) -> List[Tuple[re.Pattern, str, float]]:
        """Compile regex patterns for brand detection"""
        patterns = []
        
        # Exact brand matches (case insensitive)
        for brand in self.valid_brands:
            pattern = re.compile(rf'\b{re.escape(brand)}\b', re.IGNORECASE)
            patterns.append((pattern, brand, 0.95))
        
        # Brand correction patterns
        for abbrev, full_name in self.brand_corrections.items():
            pattern = re.compile(rf'\b{re.escape(abbrev)}\b', re.IGNORECASE)
            patterns.append((pattern, full_name, 0.85))
        
        return patterns
    
    def extract(self, text: str) -> List[EntityExtraction]:
        """Extract brand entities from text"""
        extractions = []
        text_lower = text.lower()
        
        # Strategy 1: Dictionary lookup with exact matching
        for brand in self.valid_brands:
            brand_lower = brand.lower()
            start = 0
            while True:
                pos = text_lower.find(brand_lower, start)
                if pos == -1:
                    break
                
                # Check word boundaries
                if (pos == 0 or not text[pos-1].isalnum()) and \
                   (pos + len(brand) == len(text) or not text[pos + len(brand)].isalnum()):
                    extraction = self._create_extraction(
                        value=brand,
                        confidence=0.95,
                        start=pos,
                        end=pos + len(brand),
                        text=text,
                        matched_text=text[pos:pos + len(brand)]
                    )
                    extractions.append(extraction)
                
                start = pos + 1
        
        # Strategy 2: Regex patterns for abbreviations
        for pattern, brand_name, confidence in self.brand_patterns:
            for match in pattern.finditer(text):
                extraction = self._create_extraction(
                    value=brand_name,
                    confidence=confidence,
                    start=match.start(),
                    end=match.end(),
                    text=text,
                    matched_text=match.group(),
                    correction_applied=brand_name not in self.valid_brands
                )
                extractions.append(extraction)
        
        # Strategy 3: Fuzzy matching for partial brand names
        words = re.findall(r'\b\w+\b', text)
        for word in words:
            best_match, similarity = self._fuzzy_match_brand(word)
            if similarity > 0.8:  # High similarity threshold
                # Find position of word in original text
                word_pos = text.lower().find(word.lower())
                if word_pos != -1:
                    extraction = self._create_extraction(
                        value=best_match,
                        confidence=similarity * 0.7,  # Reduce confidence for fuzzy matches
                        start=word_pos,
                        end=word_pos + len(word),
                        text=text,
                        matched_text=word,
                        fuzzy_match=True,
                        similarity_score=similarity
                    )
                    extractions.append(extraction)
        
        return self._deduplicate_extractions(extractions)
    
    def _fuzzy_match_brand(self, word: str) -> Tuple[str, float]:
        """Find best fuzzy match for a word against brand names"""
        best_match = ""
        best_similarity = 0.0
        
        word_lower = word.lower()
        
        for brand in self.valid_brands:
            brand_lower = brand.lower()
            similarity = SequenceMatcher(None, word_lower, brand_lower).ratio()
            
            # Also check against individual words in multi-word brands
            for brand_word in brand_lower.split():
                word_similarity = SequenceMatcher(None, word_lower, brand_word).ratio()
                similarity = max(similarity, word_similarity)
            
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = brand
        
        return best_match, best_similarity
    
    def _deduplicate_extractions(self, extractions: List[EntityExtraction]) -> List[EntityExtraction]:
        """Remove duplicate/overlapping extractions, keeping highest confidence"""
        if not extractions:
            return extractions
        
        # Sort by confidence descending
        extractions.sort(key=lambda x: x.confidence, reverse=True)
        
        # Remove overlapping extractions
        filtered = []
        for extraction in extractions:
            overlapping = False
            for existing in filtered:
                # Check if extractions overlap
                if (extraction.start_pos < existing.end_pos and extraction.end_pos > existing.start_pos):
                    overlapping = True
                    break
            
            if not overlapping:
                filtered.append(extraction)
        
        return filtered


class ColorExtractor(BaseEntityExtractor):
    """Extract color names using multiple strategies"""
    
    def __init__(self):
        super().__init__(EntityType.COLOR, ExtractionStrategy.DICTIONARY_LOOKUP)
        self.valid_colors = VALID_COLORS
        
        # Extended color patterns including common variations
        self.color_patterns = self._compile_color_patterns()
    
    def _compile_color_patterns(self) -> List[Tuple[re.Pattern, str, float]]:
        """Compile regex patterns for color detection"""
        patterns = []
        
        # Exact color matches
        for color in self.valid_colors:
            pattern = re.compile(rf'\b{re.escape(color)}\b', re.IGNORECASE)
            patterns.append((pattern, color, 0.95))
        
        # Color variations and synonyms
        color_variations = {
            'grey': 'gray',
            'gray': 'grey',
            'dark blue': 'navy',
            'light blue': 'blue',
            'dark red': 'red',
            'burgundy': 'red',
            'maroon': 'red',
            'off-white': 'cream',
            'off white': 'cream',
            'ivory': 'cream',
        }
        
        for variation, standard_color in color_variations.items():
            if standard_color in self.valid_colors:
                pattern = re.compile(rf'\b{re.escape(variation)}\b', re.IGNORECASE)
                patterns.append((pattern, standard_color, 0.85))
        
        return patterns
    
    def extract(self, text: str) -> List[EntityExtraction]:
        """Extract color entities from text"""
        extractions = []
        
        # Strategy 1: Dictionary lookup
        text_lower = text.lower()
        for color in self.valid_colors:
            color_lower = color.lower()
            start = 0
            while True:
                pos = text_lower.find(color_lower, start)
                if pos == -1:
                    break
                
                # Check word boundaries
                if (pos == 0 or not text[pos-1].isalnum()) and \
                   (pos + len(color) == len(text) or not text[pos + len(color)].isalnum()):
                    extraction = self._create_extraction(
                        value=color,
                        confidence=0.95,
                        start=pos,
                        end=pos + len(color),
                        text=text,
                        matched_text=text[pos:pos + len(color)]
                    )
                    extractions.append(extraction)
                
                start = pos + 1
        
        # Strategy 2: Pattern matching for variations
        for pattern, color_name, confidence in self.color_patterns:
            for match in pattern.finditer(text):
                extraction = self._create_extraction(
                    value=color_name,
                    confidence=confidence,
                    start=match.start(),
                    end=match.end(),
                    text=text,
                    matched_text=match.group(),
                    variation_match=True
                )
                extractions.append(extraction)
        
        return self._deduplicate_extractions(extractions)
    
    def _deduplicate_extractions(self, extractions: List[EntityExtraction]) -> List[EntityExtraction]:
        """Remove duplicate/overlapping extractions"""
        if not extractions:
            return extractions
        
        # Sort by confidence descending
        extractions.sort(key=lambda x: x.confidence, reverse=True)
        
        # Remove overlapping extractions
        filtered = []
        for extraction in extractions:
            overlapping = False
            for existing in filtered:
                if (extraction.start_pos < existing.end_pos and extraction.end_pos > existing.start_pos):
                    overlapping = True
                    break
            
            if not overlapping:
                filtered.append(extraction)
        
        return filtered


class CategoryExtractor(BaseEntityExtractor):
    """Extract bag categories using multiple strategies"""
    
    def __init__(self):
        super().__init__(EntityType.CATEGORY, ExtractionStrategy.DICTIONARY_LOOKUP)
        self.valid_categories = BAG_CATEGORIES
        
        # Category normalization mappings
        self.category_corrections = {
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
        
        self.category_patterns = self._compile_category_patterns()
    
    def _compile_category_patterns(self) -> List[Tuple[re.Pattern, str, float]]:
        """Compile regex patterns for category detection"""
        patterns = []
        
        # Exact category matches
        for category in self.valid_categories:
            pattern = re.compile(rf'\b{re.escape(category)}\b', re.IGNORECASE)
            patterns.append((pattern, category, 0.95))
        
        # Category corrections
        for variation, standard_category in self.category_corrections.items():
            pattern = re.compile(rf'\b{re.escape(variation)}\b', re.IGNORECASE)
            patterns.append((pattern, standard_category, 0.85))
        
        return patterns
    
    def extract(self, text: str) -> List[EntityExtraction]:
        """Extract category entities from text"""
        extractions = []
        
        # Strategy 1: Pattern matching with corrections
        for pattern, category_name, confidence in self.category_patterns:
            for match in pattern.finditer(text):
                extraction = self._create_extraction(
                    value=category_name,
                    confidence=confidence,
                    start=match.start(),
                    end=match.end(),
                    text=text,
                    matched_text=match.group(),
                    normalized=category_name != match.group().lower()
                )
                extractions.append(extraction)
        
        return self._deduplicate_extractions(extractions)
    
    def _deduplicate_extractions(self, extractions: List[EntityExtraction]) -> List[EntityExtraction]:
        """Remove duplicate/overlapping extractions"""
        if not extractions:
            return extractions
        
        # Sort by confidence descending
        extractions.sort(key=lambda x: x.confidence, reverse=True)
        
        # Remove overlapping extractions
        filtered = []
        for extraction in extractions:
            overlapping = False
            for existing in filtered:
                if (extraction.start_pos < existing.end_pos and extraction.end_pos > existing.start_pos):
                    overlapping = True
                    break
            
            if not overlapping:
                filtered.append(extraction)
        
        return filtered


class ExclusionExtractor(BaseEntityExtractor):
    """Extract exclusion preferences (negative sentiments)"""
    
    def __init__(self):
        super().__init__(EntityType.EXCLUSION, ExtractionStrategy.REGEX_PATTERNS)
        
        # Exclusion patterns
        self.exclusion_patterns = [
            # Direct exclusions
            r"excluding\s+([^.]+)",
            r"don\'?t\s+want\s+([^.]+)",
            r"no\s+([a-z]+(?:\s+[a-z]+)*)\s+bags?",
            r"avoid\s+([^.]+)",
            r"not\s+([a-z]+(?:\s+[a-z]+)*)\s+bags?",
            
            # But/except patterns  
            r"everything\s+but\s+(?:not\s+)?([a-z]+(?:\s+[a-z]+)*)",  # Handle "everything but not brown"
            r"anything\s+except\s+([^.]+)",
            r"anything\s+but\s+(?:not\s+)?([a-z]+(?:\s+[a-z]+)*)",  # Handle "anything but not black"
            r"but\s+not\s+([a-z]+(?:\s+[a-z]+)*)\s+(?:bags?|ones?)",  # Handle "but not black ones"
            r"any\s+bag\s+except\s+([a-z]+(?:\s+[a-z]+)*(?:\s+colou?rs?)?)",  # Handle "any bag except black colour"
            r"except\s+([a-z]+(?:\s+[a-z]+)*(?:\s+colou?rs?)?)",  # Handle "except black colour"
            
            # Colors/any excluding patterns
            r"any\s+colou?rs?\s+excluding\s+([^.]+)",
            r"any\s+colou?rs?\s+but\s+(?:not\s+)?([a-z]+(?:\s+[a-z]+)*)",
            
            # Negative preferences
            r"I\s+hate\s+([^.]+)",
            r"dislike\s+([^.]+)",
            r"never\s+([^.]+)"
        ]
        
        self.compiled_patterns = [(re.compile(p, re.IGNORECASE), p) for p in self.exclusion_patterns]
    
    def extract(self, text: str) -> List[EntityExtraction]:
        """Extract exclusion entities from text"""
        extractions = []
        
        for pattern, pattern_str in self.compiled_patterns:
            for match in pattern.finditer(text):
                excluded_text = match.group(1).strip()
                
                extraction = self._create_extraction(
                    value=excluded_text,
                    confidence=0.90,
                    start=match.start(),
                    end=match.end(), 
                    text=text,
                    matched_text=match.group(),
                    exclusion_pattern=pattern_str,
                    excluded_content=excluded_text
                )
                extractions.append(extraction)
        
        return extractions


class PriceExtractor(BaseEntityExtractor):
    """Extract price ranges and constraints"""
    
    def __init__(self):
        super().__init__(EntityType.PRICE, ExtractionStrategy.REGEX_PATTERNS)
        
        # Price extraction patterns
        self.price_patterns = [
            # Above/over patterns
            (r"above\s+\$?(\d+(?:\.\d{2})?)", "min", 0.95),
            (r"over\s+\$?(\d+(?:\.\d{2})?)", "min", 0.95),
            (r"more\s+than\s+\$?(\d+(?:\.\d{2})?)", "min", 0.90),
            (r"greater\s+than\s+\$?(\d+(?:\.\d{2})?)", "min", 0.90),
            (r"at\s+least\s+\$?(\d+(?:\.\d{2})?)", "min", 0.90),
            (r"minimum\s+\$?(\d+(?:\.\d{2})?)", "min", 0.90),
            (r"\$?(\d+(?:\.\d{2})?)\s*\+", "min", 0.85),
            
            # Below/under patterns
            (r"below\s+\$?(\d+(?:\.\d{2})?)", "max", 0.95),
            (r"under\s+\$?(\d+(?:\.\d{2})?)", "max", 0.95),
            (r"less\s+than\s+\$?(\d+(?:\.\d{2})?)", "max", 0.90),
            (r"cheaper\s+than\s+\$?(\d+(?:\.\d{2})?)", "max", 0.85),
            (r"maximum\s+\$?(\d+(?:\.\d{2})?)", "max", 0.90),
            (r"up\s+to\s+\$?(\d+(?:\.\d{2})?)", "max", 0.90),
            
            # Range patterns
            (r"between\s+\$?(\d+(?:\.\d{2})?)\s+(?:and|to)\s+\$?(\d+(?:\.\d{2})?)", "range", 0.95),
            (r"\$?(\d+(?:\.\d{2})?)\s*-\s*\$?(\d+(?:\.\d{2})?)", "range", 0.90),
            
            # Exact price patterns
            (r"exactly\s+\$?(\d+(?:\.\d{2})?)", "exact", 0.90),
            (r"around\s+\$?(\d+(?:\.\d{2})?)", "around", 0.80),
            (r"about\s+\$?(\d+(?:\.\d{2})?)", "around", 0.80),
        ]
        
        self.compiled_patterns = [(re.compile(p, re.IGNORECASE), t, c) for p, t, c in self.price_patterns]
    
    def extract(self, text: str) -> List[EntityExtraction]:
        """Extract price entities from text"""
        extractions = []
        
        for pattern, price_type, confidence in self.compiled_patterns:
            for match in pattern.finditer(text):
                if price_type == "range":
                    # Handle range patterns with two groups
                    price1 = float(match.group(1))
                    price2 = float(match.group(2))
                    min_price = min(price1, price2)
                    max_price = max(price1, price2)
                    
                    extraction = self._create_extraction(
                        value=f"{min_price}-{max_price}",
                        confidence=confidence,
                        start=match.start(),
                        end=match.end(),
                        text=text,
                        matched_text=match.group(),
                        price_type="range",
                        price_min=min_price,
                        price_max=max_price
                    )
                else:
                    # Handle single price patterns
                    price = float(match.group(1))
                    
                    extraction = self._create_extraction(
                        value=str(price),
                        confidence=confidence,
                        start=match.start(),
                        end=match.end(),
                        text=text,
                        matched_text=match.group(),
                        price_type=price_type,
                        price_value=price
                    )
                
                extractions.append(extraction)
        
        return extractions


class UICommandExtractor(BaseEntityExtractor):
    """Extract UI/interaction commands that shouldn't be treated as product features"""
    
    def __init__(self):
        super().__init__(EntityType.UI_COMMAND, ExtractionStrategy.REGEX_PATTERNS)
        
        # UI command patterns
        self.ui_patterns = [
            # Show more/options requests
            r"show\s+more\s+(?:options?|results?|products?)",
            r"(?:some\s+)?more\s+options?",
            r"(?:some\s+)?more\s+results?", 
            r"see\s+more\s+(?:options?|results?|products?)",
            r"display\s+more\s+(?:options?|results?|products?)",
            
            # Navigation commands
            r"next\s+(?:page|options?|results?)",
            r"previous\s+(?:page|options?|results?)",
            r"go\s+back",
            r"start\s+over",
            r"clear\s+(?:all|preferences)",
            
            # Help/info requests
            r"help\s+me",
            r"what\s+(?:can|do)\s+you",
            r"how\s+(?:do\s+)?(?:i|can)",
            r"tell\s+me\s+(?:more|about)",
            
            # General interaction
            r"thank\s+you",
            r"thanks",
            r"okay",
            r"ok",
            r"yes",
            r"no"
        ]
        
        self.compiled_patterns = [(re.compile(p, re.IGNORECASE), p) for p in self.ui_patterns]
    
    def extract(self, text: str) -> List[EntityExtraction]:
        """Extract UI command entities from text"""
        extractions = []
        
        for pattern, pattern_str in self.compiled_patterns:
            for match in pattern.finditer(text):
                command_text = match.group().strip()
                
                extraction = self._create_extraction(
                    value=command_text,
                    confidence=0.95,
                    start=match.start(),
                    end=match.end(), 
                    text=text,
                    matched_text=match.group(),
                    ui_pattern=pattern_str,
                    command_type=self._classify_command(command_text)
                )
                extractions.append(extraction)
        
        return extractions
    
    def _classify_command(self, command_text: str) -> str:
        """Classify the type of UI command"""
        command_lower = command_text.lower()
        
        if any(word in command_lower for word in ["more", "show", "see", "display"]):
            return "show_more"
        elif any(word in command_lower for word in ["next", "previous", "back"]):
            return "navigation"  
        elif any(word in command_lower for word in ["clear", "start"]):
            return "reset"
        elif any(word in command_lower for word in ["help", "how", "what"]):
            return "help"
        else:
            return "interaction"


class NERService:
    """Main Named Entity Recognition service"""
    
    def __init__(self, enable_spacy: bool = True):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.enable_spacy = enable_spacy and SPACY_AVAILABLE
        
        # Initialize extractors
        self.extractors = {
            EntityType.BRAND: BrandExtractor(),
            EntityType.COLOR: ColorExtractor(),
            EntityType.CATEGORY: CategoryExtractor(),
            EntityType.PRICE: PriceExtractor(),
            EntityType.EXCLUSION: ExclusionExtractor(),
            EntityType.UI_COMMAND: UICommandExtractor()
        }
        
        # Initialize spaCy if available
        self.nlp = None
        if self.enable_spacy:
            self._initialize_spacy()
    
    def _initialize_spacy(self):
        """Initialize spaCy NLP pipeline"""
        try:
            # Try to load English model
            self.nlp = spacy.load("en_core_web_sm")
            self.logger.info("spaCy model 'en_core_web_sm' loaded successfully")
        except Exception as e:
            self.logger.warning(f"Could not load spaCy model: {e}")
            self.logger.info("Falling back to basic English language support")
            self.nlp = English()
    
    def extract_entities(self, text: str, entity_types: Optional[Set[EntityType]] = None) -> NERResult:
        """Extract entities from text using all configured extractors"""
        import time
        start_time = time.time()
        
        if entity_types is None:
            entity_types = set(self.extractors.keys())
        
        all_extractions = []
        strategies_used = set()
        
        # Run each extractor
        for entity_type in entity_types:
            if entity_type in self.extractors:
                extractor = self.extractors[entity_type]
                try:
                    extractions = extractor.extract(text)
                    all_extractions.extend(extractions)
                    strategies_used.add(extractor.strategy)
                    self.logger.debug(f"Extracted {len(extractions)} {entity_type.value} entities")
                except Exception as e:
                    self.logger.error(f"Error in {entity_type.value} extraction: {e}")
        
        # Optional: Add spaCy NER for additional entities
        if self.nlp and self.enable_spacy:
            spacy_extractions = self._extract_with_spacy(text)
            all_extractions.extend(spacy_extractions)
            if spacy_extractions:
                strategies_used.add(ExtractionStrategy.SPACY_NER)
        
        processing_time = (time.time() - start_time) * 1000  # Convert to milliseconds
        
        return NERResult(
            input_text=text,
            entities=all_extractions,
            processing_time_ms=processing_time,
            strategies_used=strategies_used
        )
    
    def _extract_with_spacy(self, text: str) -> List[EntityExtraction]:
        """Extract entities using spaCy NER"""
        extractions = []
        
        try:
            doc = self.nlp(text)
            
            for ent in doc.ents:
                # Map spaCy entity types to our entity types
                entity_type = self._map_spacy_entity_type(ent.label_)
                if entity_type:
                    extraction = EntityExtraction(
                        entity_type=entity_type,
                        value=ent.text,
                        confidence=0.75,  # Medium confidence for spaCy
                        start_pos=ent.start_char,
                        end_pos=ent.end_char,
                        source_text=text,
                        extraction_strategy=ExtractionStrategy.SPACY_NER,
                        metadata={
                            'spacy_label': ent.label_,
                            'spacy_description': spacy.explain(ent.label_)
                        }
                    )
                    extractions.append(extraction)
                    
        except Exception as e:
            self.logger.error(f"spaCy extraction error: {e}")
        
        return extractions
    
    def _map_spacy_entity_type(self, spacy_label: str) -> Optional[EntityType]:
        """Map spaCy entity labels to our entity types"""
        mapping = {
            'ORG': EntityType.BRAND,  # Organizations could be brands
            'PRODUCT': EntityType.BRAND,  # Products could indicate brands
            'MONEY': EntityType.PRICE,  # Money entities for prices
        }
        return mapping.get(spacy_label)
    
    def get_extraction_summary(self, ner_result: NERResult) -> Dict[str, Any]:
        """Get a summary of extraction results"""
        summary = {
            'input_text': ner_result.input_text[:100] + '...' if len(ner_result.input_text) > 100 else ner_result.input_text,
            'total_entities': len(ner_result.entities),
            'processing_time_ms': ner_result.processing_time_ms,
            'strategies_used': [s.value for s in ner_result.strategies_used],
            'entities_by_type': {}
        }
        
        for entity_type in EntityType:
            entities = ner_result.get_unique_values_by_type(entity_type)
            if entities:
                summary['entities_by_type'][entity_type.value] = entities
        
        return summary


# Initialize global NER service instance
_ner_service_instance = None

def get_ner_service() -> NERService:
    """Get singleton NER service instance"""
    global _ner_service_instance
    if _ner_service_instance is None:
        _ner_service_instance = NERService()
    return _ner_service_instance
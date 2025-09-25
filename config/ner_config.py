"""
NER Configuration Settings
Configuration for Named Entity Recognition service
"""

import os
from typing import Dict, List, Set
from enum import Enum

# NER Service Configuration
class NERConfig:
    """Configuration class for NER service"""
    
    # Enable/disable NER features
    ENABLE_NER = os.getenv('ENABLE_NER', 'true').lower() == 'true'
    ENABLE_SPACY = os.getenv('ENABLE_SPACY', 'true').lower() == 'true'
    ENABLE_FUZZY_MATCHING = os.getenv('ENABLE_FUZZY_MATCHING', 'true').lower() == 'true'
    
    # Confidence thresholds
    MIN_CONFIDENCE_BRAND = float(os.getenv('MIN_CONFIDENCE_BRAND', '0.7'))
    MIN_CONFIDENCE_COLOR = float(os.getenv('MIN_CONFIDENCE_COLOR', '0.8'))
    MIN_CONFIDENCE_CATEGORY = float(os.getenv('MIN_CONFIDENCE_CATEGORY', '0.75'))
    MIN_CONFIDENCE_EXCLUSION = float(os.getenv('MIN_CONFIDENCE_EXCLUSION', '0.85'))
    
    # Fuzzy matching settings
    FUZZY_MATCH_THRESHOLD = float(os.getenv('FUZZY_MATCH_THRESHOLD', '0.8'))
    FUZZY_MATCH_CONFIDENCE_PENALTY = float(os.getenv('FUZZY_MATCH_CONFIDENCE_PENALTY', '0.7'))
    
    # spaCy model configuration
    SPACY_MODEL = os.getenv('SPACY_MODEL', 'en_core_web_sm')
    SPACY_DOWNLOAD_IF_MISSING = os.getenv('SPACY_DOWNLOAD_IF_MISSING', 'true').lower() == 'true'
    
    # Processing limits
    MAX_TEXT_LENGTH = int(os.getenv('MAX_TEXT_LENGTH', '10000'))
    MAX_ENTITIES_PER_TYPE = int(os.getenv('MAX_ENTITIES_PER_TYPE', '10'))
    
    # Debug and logging
    NER_DEBUG_MODE = os.getenv('NER_DEBUG_MODE', 'false').lower() == 'true'
    LOG_EXTRACTION_DETAILS = os.getenv('LOG_EXTRACTION_DETAILS', 'false').lower() == 'true'


# Extended entity configuration
EXTENDED_COLOR_SYNONYMS = {
    'grey': 'gray',
    'gray': 'grey', 
    'dark blue': 'navy',
    'light blue': 'blue',
    'sky blue': 'blue',
    'royal blue': 'blue',
    'dark red': 'red',
    'burgundy': 'red',
    'maroon': 'red',
    'crimson': 'red',
    'off-white': 'cream',
    'off white': 'cream',
    'ivory': 'cream',
    'champagne': 'cream',
    'beige': 'tan',
    'khaki': 'tan',
    'olive': 'green',
    'forest green': 'green',
    'lime': 'green',
    'mint': 'green',
    'lavender': 'purple',
    'violet': 'purple',
    'magenta': 'pink',
    'rose': 'pink',
    'coral': 'orange',
    'peach': 'orange',
    'bronze': 'gold',
    'copper': 'gold'
}

EXTENDED_BRAND_PATTERNS = {
    # Common abbreviations and misspellings
    r'\bck\b': 'Calvin Klein',
    r'\bcalvin\b': 'Calvin Klein',
    r'\brm\b': 'Rebecca Minkoff',
    r'\bth\b': 'Tommy Hilfiger',
    r'\btommy\b': 'Tommy Hilfiger',
    r'\bpierre\b': 'PIERRE CARDIN',
    r'\bralph\s+lauren\b': 'Lauren Ralph Lauren',
    r'\bamerican\s+t\b': 'American Tourister',
    r'\bmk\b': 'Michael Kors',
    r'\bmichael\s+kors\b': 'Michael Kors',
    r'\bkate\s+spade\b': 'Kate Spade',
    r'\bcoach\b': 'Coach',
    r'\bgucci\b': 'Gucci',
    r'\blv\b': 'Louis Vuitton',
    r'\blouis\s+vuitton\b': 'Louis Vuitton',
    r'\bprada\b': 'Prada',
    r'\bchanel\b': 'Chanel',
    r'\bhermes\b': 'Hermes',
    r'\bversace\b': 'Versace',
    r'\barmani\b': 'Armani Exchange',
    r'\bdkny\b': 'DKNY',
    r'\bburberry\b': 'Burberry'
}

EXTENDED_CATEGORY_VARIATIONS = {
    # Common category variations and synonyms
    'tote': 'tote bags',
    'totes': 'tote bags',
    'tote bag': 'tote bags',
    'crossbody': 'crossbody bags',
    'cross-body': 'crossbody bags',
    'cross body': 'crossbody bags',
    'crossbody bag': 'crossbody bags',
    'shoulder': 'shoulder bags',
    'shoulder bag': 'shoulder bags',
    'backpack': 'backpacks',
    'back pack': 'backpacks',
    'knapsack': 'backpacks',
    'rucksack': 'backpacks',
    'clutch': 'clutches',
    'clutch bag': 'clutches',
    'evening bag': 'clutches',
    'duffle': 'duffle bags',
    'duffel': 'duffle bags',
    'duffle bag': 'duffle bags',
    'duffel bag': 'duffle bags',
    'gym bag': 'duffle bags',
    'travel bag': 'duffle bags',
    'laptop': 'laptop bag',
    'laptop bag': 'laptop bag',
    'computer bag': 'laptop bag',
    'briefcase': 'briefcase',
    'brief case': 'briefcase',
    'brief-case': 'briefcase',
    'attache': 'briefcase',
    'messenger': 'messenger',
    'messenger bag': 'messenger',
    'satchel': 'satchel',
    'satchel bag': 'satchel',
    'handbag': 'handbag',
    'hand bag': 'handbag',
    'purse': 'purse',
    'pocketbook': 'purse',
    'wallet': 'wallet',
    'billfold': 'wallet',
    'wristlet': 'wristlet',
    'mini bag': 'wristlet',
    'pouch': 'wristlet'
}

# Exclusion patterns with confidence scores
EXCLUSION_PATTERNS_CONFIG = [
    {
        'pattern': r"excluding\s+([^.,!?]+)",
        'confidence': 0.95,
        'description': 'Direct exclusion with "excluding"'
    },
    {
        'pattern': r"don\'?t\s+want\s+([^.,!?]+)",
        'confidence': 0.90,
        'description': 'Negative preference with "don\'t want"'
    },
    {
        'pattern': r"no\s+([a-z]+(?:\s+[a-z]+)*)\s+bags?",
        'confidence': 0.85,
        'description': 'Negative with "no [item] bags"'
    },
    {
        'pattern': r"avoid\s+([^.,!?]+)",
        'confidence': 0.90,
        'description': 'Avoidance instruction'
    },
    {
        'pattern': r"not\s+([a-z]+(?:\s+[a-z]+)*)\s+bags?",
        'confidence': 0.80,
        'description': 'Negative with "not [item] bags"'
    },
    {
        'pattern': r"everything\s+but\s+([^.,!?]+)",
        'confidence': 0.85,
        'description': 'Exception with "everything but"'
    },
    {
        'pattern': r"anything\s+except\s+([^.,!?]+)",
        'confidence': 0.85,
        'description': 'Exception with "anything except"'
    },
    {
        'pattern': r"I\s+hate\s+([^.,!?]+)",
        'confidence': 0.75,
        'description': 'Strong negative preference'
    },
    {
        'pattern': r"dislike\s+([^.,!?]+)",
        'confidence': 0.70,
        'description': 'Mild negative preference'
    },
    {
        'pattern': r"never\s+([^.,!?]+)",
        'confidence': 0.80,
        'description': 'Absolute avoidance'
    }
]

# Entity validation rules
ENTITY_VALIDATION_RULES = {
    'brand': {
        'min_length': 2,
        'max_length': 50,
        'allowed_chars': r'^[a-zA-Z0-9\s\-&\.\']+$',
        'required_confidence': NERConfig.MIN_CONFIDENCE_BRAND
    },
    'color': {
        'min_length': 3,
        'max_length': 20,
        'allowed_chars': r'^[a-zA-Z\s\-]+$',
        'required_confidence': NERConfig.MIN_CONFIDENCE_COLOR
    },
    'category': {
        'min_length': 3,
        'max_length': 30,
        'allowed_chars': r'^[a-zA-Z\s\-]+$',
        'required_confidence': NERConfig.MIN_CONFIDENCE_CATEGORY
    }
}

# Performance monitoring configuration
PERFORMANCE_CONFIG = {
    'max_processing_time_ms': 5000,  # Maximum processing time per request
    'enable_performance_logging': NERConfig.LOG_EXTRACTION_DETAILS,
    'cache_extraction_results': True,
    'cache_ttl_seconds': 3600,  # 1 hour cache
    'max_cache_size': 1000
}

# Integration settings
INTEGRATION_CONFIG = {
    'fallback_to_llm': True,  # Fallback to LLM if NER fails
    'combine_ner_llm_results': True,  # Combine NER and LLM extractions
    'prefer_ner_over_llm': True,  # Prefer NER results when both available
    'enable_backup_patterns': True,  # Enable pattern-based backup extraction
    'track_extraction_sources': True  # Track which method extracted each entity
}


def get_ner_config() -> Dict:
    """Get complete NER configuration"""
    return {
        'service_config': {
            'enable_ner': NERConfig.ENABLE_NER,
            'enable_spacy': NERConfig.ENABLE_SPACY,
            'enable_fuzzy_matching': NERConfig.ENABLE_FUZZY_MATCHING,
            'spacy_model': NERConfig.SPACY_MODEL,
            'max_text_length': NERConfig.MAX_TEXT_LENGTH,
            'debug_mode': NERConfig.NER_DEBUG_MODE
        },
        'confidence_thresholds': {
            'brand': NERConfig.MIN_CONFIDENCE_BRAND,
            'color': NERConfig.MIN_CONFIDENCE_COLOR, 
            'category': NERConfig.MIN_CONFIDENCE_CATEGORY,
            'exclusion': NERConfig.MIN_CONFIDENCE_EXCLUSION
        },
        'fuzzy_matching': {
            'threshold': NERConfig.FUZZY_MATCH_THRESHOLD,
            'confidence_penalty': NERConfig.FUZZY_MATCH_CONFIDENCE_PENALTY
        },
        'extended_mappings': {
            'color_synonyms': EXTENDED_COLOR_SYNONYMS,
            'brand_patterns': EXTENDED_BRAND_PATTERNS,
            'category_variations': EXTENDED_CATEGORY_VARIATIONS
        },
        'exclusion_patterns': EXCLUSION_PATTERNS_CONFIG,
        'validation_rules': ENTITY_VALIDATION_RULES,
        'performance': PERFORMANCE_CONFIG,
        'integration': INTEGRATION_CONFIG
    }


def validate_ner_config():
    """Validate NER configuration settings"""
    issues = []
    
    # Check confidence thresholds
    if not (0.0 <= NERConfig.MIN_CONFIDENCE_BRAND <= 1.0):
        issues.append("MIN_CONFIDENCE_BRAND must be between 0.0 and 1.0")
    
    if not (0.0 <= NERConfig.MIN_CONFIDENCE_COLOR <= 1.0):
        issues.append("MIN_CONFIDENCE_COLOR must be between 0.0 and 1.0")
    
    if not (0.0 <= NERConfig.MIN_CONFIDENCE_CATEGORY <= 1.0):
        issues.append("MIN_CONFIDENCE_CATEGORY must be between 0.0 and 1.0")
    
    # Check fuzzy matching settings
    if not (0.0 <= NERConfig.FUZZY_MATCH_THRESHOLD <= 1.0):
        issues.append("FUZZY_MATCH_THRESHOLD must be between 0.0 and 1.0")
    
    # Check processing limits
    if NERConfig.MAX_TEXT_LENGTH <= 0:
        issues.append("MAX_TEXT_LENGTH must be positive")
    
    if NERConfig.MAX_ENTITIES_PER_TYPE <= 0:
        issues.append("MAX_ENTITIES_PER_TYPE must be positive")
    
    return issues if issues else None


# Initialize configuration validation on import
_config_issues = validate_ner_config()
if _config_issues:
    import warnings
    for issue in _config_issues:
        warnings.warn(f"NER Configuration Issue: {issue}")
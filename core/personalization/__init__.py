"""
Personalization module for bag shopping assistant.
"""

from .models import UserProfile, SessionState, Preference, PreferenceCategory
from .engine import PersonalizationEngine
from .extractor import PreferenceExtractor
from .storage import InMemoryStorage, ProfileStorage
from .intent_mode import (
    classify_personalisation_mode,
    inject_diversity,
    dimension_aware_boost,
    decayed_weight,
    query_specificity_score,
    detect_profile_conflicts,
    compute_session_drift,
    PersonalisationMode,
    PersonalisationDecision,
    SessionSignals,
    ProfileConflictResult,
)

__all__ = [
    'UserProfile',
    'SessionState',
    'Preference',
    'PreferenceCategory',
    'PersonalizationEngine',
    'PreferenceExtractor',
    'InMemoryStorage',
    'ProfileStorage',
    # Intent mode classifier
    'classify_personalisation_mode',
    'inject_diversity',
    'dimension_aware_boost',
    'decayed_weight',
    'query_specificity_score',
    'detect_profile_conflicts',
    'compute_session_drift',
    'PersonalisationMode',
    'PersonalisationDecision',
    'SessionSignals',
    'ProfileConflictResult',
]
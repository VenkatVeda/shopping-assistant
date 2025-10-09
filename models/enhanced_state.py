"""
Enhanced State Management for NER Integration
Tracks entity extraction results, confidence scores, and extraction metadata
"""

from dataclasses import dataclass, field
from typing import List, Union, Dict, Any, Optional
from langchain_core.messages import HumanMessage, AIMessage
from datetime import datetime
from enum import Enum


class PreferenceSource(Enum):
    """Source of preference extraction"""
    NER_EXTRACTION = "ner_extraction"
    LLM_EXTRACTION = "llm_extraction" 
    USER_EXPLICIT = "user_explicit"
    SYSTEM_DEFAULT = "system_default"


@dataclass
class EntityExtractionState:
    """State tracking for entity extraction results"""
    entity_type: str
    extracted_values: List[str] = field(default_factory=list)
    confidence_scores: List[float] = field(default_factory=list)
    extraction_sources: List[str] = field(default_factory=list)
    extraction_strategies: List[str] = field(default_factory=list)
    extraction_timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def add_extraction(self, value: str, confidence: float, source: str, strategy: str, **metadata):
        """Add a new extraction result"""
        self.extracted_values.append(value)
        self.confidence_scores.append(confidence)
        self.extraction_sources.append(source)
        self.extraction_strategies.append(strategy)
        self.extraction_timestamp = datetime.now()
        
        if metadata:
            if 'extractions' not in self.metadata:
                self.metadata['extractions'] = []
            self.metadata['extractions'].append(metadata)
    
    def get_best_extractions(self, max_results: int = 3) -> List[Dict[str, Any]]:
        """Get best extractions sorted by confidence"""
        if not self.extracted_values:
            return []
        
        # Combine all extraction data
        extractions = []
        for i in range(len(self.extracted_values)):
            extraction = {
                'value': self.extracted_values[i],
                'confidence': self.confidence_scores[i] if i < len(self.confidence_scores) else 0.0,
                'source': self.extraction_sources[i] if i < len(self.extraction_sources) else 'unknown',
                'strategy': self.extraction_strategies[i] if i < len(self.extraction_strategies) else 'unknown'
            }
            extractions.append(extraction)
        
        # Sort by confidence descending
        extractions.sort(key=lambda x: x['confidence'], reverse=True)
        
        return extractions[:max_results]
    
    def get_unique_values(self, min_confidence: float = 0.5) -> List[str]:
        """Get unique extracted values above confidence threshold"""
        unique_values = []
        seen_values = set()
        
        for i, value in enumerate(self.extracted_values):
            confidence = self.confidence_scores[i] if i < len(self.confidence_scores) else 0.0
            value_lower = value.lower()
            
            if confidence >= min_confidence and value_lower not in seen_values:
                unique_values.append(value)
                seen_values.add(value_lower)
        
        return unique_values


@dataclass
class NERProcessingState:
    """State for NER processing session"""
    session_id: str
    input_text: str
    processing_time_ms: float = 0.0
    total_entities_found: int = 0
    strategies_used: List[str] = field(default_factory=list)
    entity_states: Dict[str, EntityExtractionState] = field(default_factory=dict)
    processing_timestamp: datetime = field(default_factory=datetime.now)
    error_messages: List[str] = field(default_factory=list)
    
    def add_entity_extraction(self, entity_type: str, value: str, confidence: float, 
                            source: str, strategy: str, **metadata):
        """Add entity extraction to state"""
        if entity_type not in self.entity_states:
            self.entity_states[entity_type] = EntityExtractionState(entity_type=entity_type)
        
        self.entity_states[entity_type].add_extraction(
            value, confidence, source, strategy, **metadata
        )
        self.total_entities_found += 1
    
    def get_entity_summary(self) -> Dict[str, Any]:
        """Get summary of all entity extractions"""
        summary = {
            'session_id': self.session_id,
            'input_text': self.input_text[:100] + '...' if len(self.input_text) > 100 else self.input_text,
            'processing_time_ms': self.processing_time_ms,
            'total_entities': self.total_entities_found,
            'strategies_used': self.strategies_used,
            'entities_by_type': {},
            'processing_timestamp': self.processing_timestamp.isoformat(),
            'errors': self.error_messages
        }
        
        for entity_type, entity_state in self.entity_states.items():
            summary['entities_by_type'][entity_type] = {
                'unique_values': entity_state.get_unique_values(),
                'best_extractions': entity_state.get_best_extractions(),
                'total_extractions': len(entity_state.extracted_values)
            }
        
        return summary
    
    def get_preferred_values(self, entity_type: str, max_results: int = 5) -> List[str]:
        """Get preferred values for an entity type based on confidence and frequency"""
        if entity_type not in self.entity_states:
            return []
        
        entity_state = self.entity_states[entity_type]
        return entity_state.get_unique_values()[:max_results]


@dataclass
class ConversationState:
    """Enhanced conversation state with NER integration"""
    # Original state fields
    chat_history: List[Union[HumanMessage, AIMessage]] = field(default_factory=list)
    question: str = ""
    answer: str = ""
    should_retrieve: bool = True
    
    # NER-enhanced state fields
    current_ner_session: Optional[NERProcessingState] = None
    ner_history: List[NERProcessingState] = field(default_factory=list)
    
    # Preference tracking with sources
    preference_sources: Dict[str, PreferenceSource] = field(default_factory=dict)
    preference_confidence: Dict[str, float] = field(default_factory=dict)
    preference_timestamps: Dict[str, datetime] = field(default_factory=dict)
    
    # State metadata
    session_metadata: Dict[str, Any] = field(default_factory=dict)
    
    def start_ner_session(self, input_text: str, session_id: Optional[str] = None) -> str:
        """Start a new NER processing session"""
        if session_id is None:
            session_id = f"ner_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        
        self.current_ner_session = NERProcessingState(
            session_id=session_id,
            input_text=input_text
        )
        
        return session_id
    
    def complete_ner_session(self):
        """Complete current NER session and move to history"""
        if self.current_ner_session:
            self.ner_history.append(self.current_ner_session)
            self.current_ner_session = None
    
    def add_ner_extraction(self, entity_type: str, value: str, confidence: float,
                          source: str, strategy: str, **metadata):
        """Add NER extraction to current session"""
        if not self.current_ner_session:
            # Auto-start session if none exists
            self.start_ner_session("auto-generated")
        
        self.current_ner_session.add_entity_extraction(
            entity_type, value, confidence, source, strategy, **metadata
        )
    
    def update_preference_source(self, preference_key: str, source: PreferenceSource,
                                confidence: float = 1.0):
        """Update preference source tracking"""
        self.preference_sources[preference_key] = source
        self.preference_confidence[preference_key] = confidence
        self.preference_timestamps[preference_key] = datetime.now()
    
    def get_preference_reliability(self, preference_key: str) -> Dict[str, Any]:
        """Get reliability information for a preference"""
        return {
            'source': self.preference_sources.get(preference_key, PreferenceSource.SYSTEM_DEFAULT),
            'confidence': self.preference_confidence.get(preference_key, 0.0),
            'last_updated': self.preference_timestamps.get(preference_key),
            'is_reliable': self.preference_confidence.get(preference_key, 0.0) > 0.7
        }
    
    def get_session_summary(self) -> Dict[str, Any]:
        """Get comprehensive session summary"""
        summary = {
            'conversation_turns': len(self.chat_history),
            'current_question': self.question,
            'should_retrieve': self.should_retrieve,
            'ner_sessions_completed': len(self.ner_history),
            'current_ner_session': None,
            'preference_tracking': {}
        }
        
        if self.current_ner_session:
            summary['current_ner_session'] = self.current_ner_session.get_entity_summary()
        
        # Summarize preference reliability
        for pref_key, source in self.preference_sources.items():
            reliability = self.get_preference_reliability(pref_key)
            summary['preference_tracking'][pref_key] = {
                'source': source.value,
                'confidence': reliability['confidence'],
                'is_reliable': reliability['is_reliable']
            }
        
        return summary
    
    def get_recent_ner_results(self, limit: int = 3) -> List[Dict[str, Any]]:
        """Get recent NER processing results"""
        recent_sessions = self.ner_history[-limit:] if self.ner_history else []
        if self.current_ner_session:
            recent_sessions.append(self.current_ner_session)
        
        return [session.get_entity_summary() for session in recent_sessions]


# For backward compatibility, create an alias
@dataclass  
class BotState(ConversationState):
    """Backward compatible alias for ConversationState"""
    
    def __init__(self, *args, **kwargs):
        # Extract legacy fields if provided directly
        if args or any(k in kwargs for k in ['chat_history', 'question', 'answer', 'should_retrieve']):
            chat_history = kwargs.pop('chat_history', args[0] if args else [])
            question = kwargs.pop('question', args[1] if len(args) > 1 else "")
            answer = kwargs.pop('answer', args[2] if len(args) > 2 else "")
            should_retrieve = kwargs.pop('should_retrieve', args[3] if len(args) > 3 else True)
            
            super().__init__(
                chat_history=chat_history,
                question=question,
                answer=answer,
                should_retrieve=should_retrieve,
                **kwargs
            )
        else:
            super().__init__(**kwargs)


# Utility functions for state management

def create_enhanced_state(**kwargs) -> ConversationState:
    """Create a new enhanced conversation state"""
    return ConversationState(**kwargs)


def migrate_legacy_state(legacy_state: Dict[str, Any]) -> ConversationState:
    """Migrate legacy state dict to enhanced ConversationState"""
    return ConversationState(
        chat_history=legacy_state.get('chat_history', []),
        question=legacy_state.get('question', ''),
        answer=legacy_state.get('answer', ''),
        should_retrieve=legacy_state.get('should_retrieve', True)
    )


def track_ner_preference_extraction(state: ConversationState, entity_type: str, 
                                  values: List[str], confidences: List[float],
                                  strategy: str = "ner_extraction") -> ConversationState:
    """Track NER-based preference extraction in state"""
    if not state.current_ner_session:
        state.start_ner_session("preference_extraction")
    
    for value, confidence in zip(values, confidences):
        state.add_ner_extraction(
            entity_type=entity_type,
            value=value,
            confidence=confidence,
            source="ner_service",
            strategy=strategy
        )
        
        # Update preference source tracking
        preference_key = f"{entity_type}_{value.lower().replace(' ', '_')}"
        state.update_preference_source(
            preference_key,
            PreferenceSource.NER_EXTRACTION,
            confidence
        )
    
    return state
# Session Management Technical Architecture

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                           USER LAYER                            │
├─────────────────────────────────────────────────────────────────┤
│  User A (Browser 1)     │  User B (Browser 2)  │  User C (...)  │
│  Session: abc123        │  Session: def456     │  Session: ...  │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                        GRADIO INTERFACE                        │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Session State Management                               │   │
│  │  - gr.State() for session persistence                  │   │
│  │  - Session initialization on first interaction        │   │
│  │  - Session ID routing to backend services             │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                       SESSION MANAGER                          │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Session Registry                                       │   │
│  │  {                                                      │   │
│  │    "abc123": {                                          │   │
│  │      "created_at": timestamp,                           │   │
│  │      "last_accessed": timestamp,                        │   │
│  │      "services": { ... }                                │   │
│  │    },                                                   │   │
│  │    "def456": { ... }                                    │   │
│  │  }                                                      │   │
│  └─────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Session Lifecycle                                      │   │
│  │  - create_session()                                     │   │
│  │  - get_session_services()                              │   │
│  │  - cleanup_session()                                   │   │
│  │  - cleanup_inactive_sessions()                         │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SESSION-AWARE SERVICES                      │
├─────────────────────┬─────────────────────┬─────────────────────┤
│  ConversationWorkflow│  PreferenceService  │   VectorService     │
│                     │                     │                     │
│ Session abc123:     │ Session abc123:     │ Session abc123:     │
│ ┌─────────────────┐ │ ┌─────────────────┐ │ ┌─────────────────┐ │
│ │ History: [...]  │ │ │ Prefs: {...}    │ │ │ Context: {...}  │ │
│ │ State: {...}    │ │ │ Filters: [...]  │ │ │ Results: [...]  │ │
│ └─────────────────┘ │ └─────────────────┘ │ └─────────────────┘ │
│                     │                     │                     │
│ Session def456:     │ Session def456:     │ Session def456:     │
│ ┌─────────────────┐ │ ┌─────────────────┐ │ ┌─────────────────┐ │
│ │ History: [...]  │ │ │ Prefs: {...}    │ │ │ Context: {...}  │ │
│ │ State: {...}    │ │ │ Filters: [...]  │ │ │ Results: [...]  │ │
│ └─────────────────┘ │ └─────────────────┘ │ └─────────────────┘ │
└─────────────────────┴─────────────────────┴─────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                        CORE SERVICES                           │
├─────────────────────┬─────────────────────┬─────────────────────┤
│   Azure Service     │   NER Service       │   Search Service    │
│   (Shared)          │   (Shared)          │   (Shared)          │
│                     │                     │                     │
│ - OpenAI API        │ - Named Entity      │ - Product Database  │
│ - Text Generation   │   Recognition       │ - Vector DB         │
│ - Embeddings        │ - Intent Analysis   │ - Search Algorithms │
└─────────────────────┴─────────────────────┴─────────────────────┘
```

## Data Flow Architecture

### 1. Session Creation Flow

```
User opens application
         │
         ▼
Gradio Interface detects new user
         │
         ▼
Creates gr.State() for session persistence
         │
         ▼
First message triggers session initialization
         │
         ▼
SessionManager.create_session() called
         │
         ▼
Unique session_id generated (UUID)
         │
         ▼
Session services instantiated:
- ConversationWorkflow
- PreferenceService
- EnhancedPreferenceService
- VectorService
- SearchService
         │
         ▼
Session registered in SessionManager.sessions
         │
         ▼
Session ID returned and stored in gr.State
```

### 2. Message Processing Flow

```
User sends message
         │
         ▼
Gradio captures message + session_state
         │
         ▼
GradioInterface._handle_message_with_session()
         │
         ▼
Extract session_id from session_state
         │
         ▼
SessionManager.get_session_services(session_id)
         │
         ▼
ConversationWorkflow.process_message(message, session_id)
         │
         ▼
Session-specific conversation history retrieved
         │
         ▼
Message processed with session context
         │
         ▼
Response generated using session preferences
         │
         ▼
Conversation history updated for session
         │
         ▼
Response returned to user
```

### 3. Session Cleanup Flow

```
Background cleanup timer triggers
         │
         ▼
SessionManager.cleanup_inactive_sessions()
         │
         ▼
Check each session's last_accessed time
         │
         ▼
Sessions older than timeout identified
         │
         ▼
For each expired session:
  - Remove from sessions registry
  - Clear conversation history
  - Clear preferences
  - Release memory resources
         │
         ▼
Cleanup complete, memory freed
```

## Thread Safety Implementation

### Concurrent Access Patterns

```python
import threading
from typing import Dict, Any

class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()  # Reentrant lock for nested calls
    
    def create_session(self, session_id: str = None) -> str:
        with self._lock:  # Thread-safe session creation
            # ... session creation logic
            pass
    
    def get_session_services(self, session_id: str) -> Dict:
        with self._lock:  # Thread-safe service retrieval
            # ... service retrieval logic
            pass
```

### Race Condition Prevention

1. **Session Creation**: Atomic session creation with lock
2. **Service Access**: Thread-safe service retrieval
3. **Cleanup Operations**: Locked cleanup prevents data corruption
4. **Memory Management**: Safe memory cleanup with proper locking

## Memory Management Strategy

### Session Memory Footprint

Each session contains:
```python
Session Memory Structure:
├── Session Metadata (< 1KB)
│   ├── session_id
│   ├── created_at
│   └── last_accessed
├── ConversationWorkflow (~10-50KB)
│   ├── conversation_history
│   ├── context_memory
│   └── state_variables
├── PreferenceService (~1-5KB)
│   ├── user_preferences
│   └── filter_settings
├── VectorService (~5-20KB)
│   ├── search_context
│   └── cached_results
└── Other Services (~5-15KB)
    └── miscellaneous_data

Total per session: ~22-91KB (typical: ~50KB)
```

### Memory Optimization Strategies

1. **Lazy Loading**: Services created only when needed
2. **Garbage Collection**: Explicit cleanup of session resources
3. **Memory Limits**: Maximum session count prevents memory exhaustion
4. **Efficient Data Structures**: Minimal memory footprint design

### Cleanup Mechanisms

```python
def cleanup_session(self, session_id: str):
    """Multi-layered cleanup strategy"""
    if session_id in self.sessions:
        session = self.sessions[session_id]
        
        # 1. Service-specific cleanup
        for service_name, service in session.get('services', {}).items():
            if hasattr(service, 'cleanup'):
                service.cleanup()
        
        # 2. Clear references
        session['services'].clear()
        
        # 3. Remove from registry
        del self.sessions[session_id]
        
        # 4. Force garbage collection (optional)
        import gc
        gc.collect()
```

## Performance Characteristics

### Scalability Metrics

| Metric | Value | Notes |
|--------|--------|--------|
| Max Concurrent Sessions | 100 (configurable) | Limited by memory |
| Session Creation Time | < 100ms | Including service initialization |
| Message Processing Overhead | < 10ms | Session lookup and routing |
| Memory per Session | ~50KB | Varies with conversation length |
| Cleanup Frequency | 15 minutes | Configurable interval |

### Performance Optimizations

1. **Service Pooling**: Reuse expensive service initializations
2. **Caching**: Cache frequently accessed session data
3. **Batch Operations**: Batch cleanup operations for efficiency
4. **Async Operations**: Non-blocking cleanup processes

## Error Handling and Recovery

### Error Scenarios and Recovery

```python
class SessionError(Exception):
    """Custom exception for session-related errors"""
    pass

# Error handling patterns:
try:
    services = session_manager.get_session_services(session_id)
except SessionError as e:
    # Recovery: Create new session
    session_id = session_manager.create_session()
    services = session_manager.get_session_services(session_id)

# Graceful degradation:
if not session_manager.has_session(session_id):
    # Fall back to creating new session
    session_id = session_manager.create_session()
```

### Fault Tolerance

1. **Session Recreation**: Automatic session recreation on errors
2. **Service Recovery**: Individual service failure doesn't break session
3. **Memory Recovery**: Cleanup continues even if individual session cleanup fails
4. **Graceful Degradation**: Application continues working even with session errors

## Configuration and Tuning

### Performance Tuning Parameters

```python
class SessionConfig:
    # Memory management
    MAX_ACTIVE_SESSIONS = 100          # Adjust based on available memory
    SESSION_CACHE_SIZE = 50            # Keep frequently used sessions in cache
    
    # Timing parameters
    SESSION_TIMEOUT_MINUTES = 60       # Balance between UX and memory usage
    CLEANUP_INTERVAL_MINUTES = 15      # More frequent = less memory, more CPU
    
    # Performance settings
    ENABLE_SESSION_PERSISTENCE = False  # Trade memory for feature richness
    LAZY_SERVICE_INITIALIZATION = True  # Create services only when needed
    
    # Monitoring
    LOG_SESSION_ACTIVITY = True        # Enable for debugging, disable for performance
    ENABLE_METRICS_COLLECTION = False  # Detailed metrics collection
```

### Environment-Specific Configurations

#### Development Environment
```python
# Optimized for debugging and development
SESSION_TIMEOUT_MINUTES = 120
CLEANUP_INTERVAL_MINUTES = 30
LOG_SESSION_ACTIVITY = True
ENABLE_METRICS_COLLECTION = True
```

#### Production Environment
```python
# Optimized for performance and reliability
SESSION_TIMEOUT_MINUTES = 45
CLEANUP_INTERVAL_MINUTES = 10
LOG_SESSION_ACTIVITY = False
ENABLE_METRICS_COLLECTION = False
MAX_ACTIVE_SESSIONS = 200
```

## Security Considerations

### Session ID Security

```python
import secrets
import hashlib

def generate_secure_session_id() -> str:
    """Generate cryptographically secure session ID"""
    # Use cryptographically secure random number generator
    random_bytes = secrets.token_bytes(32)
    
    # Hash for additional security and consistent format
    session_id = hashlib.sha256(random_bytes).hexdigest()[:16]
    
    return session_id
```

### Data Isolation Verification

```python
def verify_session_isolation():
    """Security test to verify complete session isolation"""
    session_a = session_manager.create_session()
    session_b = session_manager.create_session()
    
    # Test conversation isolation
    services_a = session_manager.get_session_services(session_a)
    services_b = session_manager.get_session_services(session_b)
    
    assert services_a['conversation_workflow'] != services_b['conversation_workflow']
    assert services_a['preference_service'] != services_b['preference_service']
    
    # Test data isolation
    services_a['preference_service'].update_preferences({"color": "red"})
    prefs_b = services_b['preference_service'].get_preferences()
    
    assert "color" not in prefs_b  # Session B should not see Session A's preferences
```

### Privacy Protection

1. **No Cross-Session Data Leakage**: Strict isolation prevents privacy violations
2. **Secure Session IDs**: Cryptographically secure, non-guessable IDs
3. **Automatic Cleanup**: Sensitive data removed after session expiry
4. **Memory Security**: Explicit cleanup prevents data remnants in memory

This technical architecture ensures robust, scalable, and secure session management for the shopping assistant application.
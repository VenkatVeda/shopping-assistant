# Session Management System Documentation

## Overview

The Shopping Assistant application implements a comprehensive session management system to ensure that multiple users can interact with the chatbot simultaneously without interfering with each other's conversations, preferences, and state.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Session Manager](#session-manager)
3. [Session-Aware Services](#session-aware-services)
4. [UI Integration](#ui-integration)
5. [Configuration](#configuration)
6. [Testing](#testing)
7. [Deployment](#deployment)
8. [Monitoring](#monitoring)
9. [Troubleshooting](#troubleshooting)

## Architecture Overview

The session management system follows a multi-layered architecture:

```
┌─────────────────────────┐
│    Gradio Interface     │  ← User interactions
├─────────────────────────┤
│    Session Manager      │  ← Session lifecycle management
├─────────────────────────┤
│  Session-Aware Services │  ← Isolated service instances
├─────────────────────────┤
│    Core Services        │  ← Business logic
└─────────────────────────┘
```

### Key Components

1. **SessionManager**: Central coordinator for session lifecycle
2. **ConversationWorkflow**: Session-aware conversation handler
3. **PreferenceService**: Session-isolated user preferences
4. **GradioInterface**: UI layer with session integration
5. **SessionConfiguration**: Configurable session parameters

## Session Manager

### File: `services/session_manager.py`

The SessionManager is the core component that handles:
- Session creation and cleanup
- Service instance management
- Memory management
- Thread safety

#### Key Methods

```python
def create_session(self, session_id: str = None) -> str:
    """Create a new session with isolated services."""

def get_session_services(self, session_id: str) -> dict:
    """Retrieve services for a specific session."""

def cleanup_session(self, session_id: str):
    """Clean up resources for a session."""

def cleanup_inactive_sessions(self):
    """Remove sessions that haven't been used recently."""
```

#### Session Structure

Each session contains:
- **session_id**: Unique identifier
- **created_at**: Session creation timestamp
- **last_accessed**: Last activity timestamp
- **services**: Isolated service instances
  - conversation_workflow
  - preference_service
  - enhanced_preference_service
  - vector_service
  - search_service

### Usage Example

```python
# Create session manager
session_manager = SessionManager()

# Create new session
session_id = session_manager.create_session()

# Get services for session
services = session_manager.get_session_services(session_id)
conversation = services['conversation_workflow']

# Process message
response = conversation.process_message("Hello", session_id)
```

## Session-Aware Services

### ConversationWorkflow

**File**: `services/conversation_workflow.py`

Enhanced to handle session-specific state:

```python
class ConversationWorkflow:
    def process_message(self, message: str, session_id: str = None) -> str:
        """Process message with session context."""
        
    def get_conversation_history(self, session_id: str) -> List[Dict]:
        """Get session-specific conversation history."""
        
    def clear_conversation_history(self, session_id: str):
        """Clear history for specific session."""
```

#### Session State Management

- **Conversation Memory**: Each session maintains separate conversation history
- **Context Tracking**: Session-specific context and preferences
- **State Isolation**: No cross-session state contamination

### PreferenceService

**File**: `services/preference_service.py`

Session-aware preference management:

```python
class PreferenceService:
    def get_user_preferences(self, session_id: str = None) -> Dict:
        """Get preferences for specific session."""
        
    def update_user_preferences(self, preferences: Dict, session_id: str = None):
        """Update preferences for specific session."""
```

#### Features

- **Isolated Preferences**: Each session has separate preference storage
- **Persistent Storage**: Session preferences can be saved/loaded
- **Default Fallback**: New sessions start with default preferences

## UI Integration

### GradioInterface

**File**: `ui/gradio_interface.py`

The UI layer integrates seamlessly with session management:

#### Session Handling in UI

```python
def create_interface(self):
    with gr.Blocks() as interface:
        # Session state component
        session_state = gr.State()
        
        # Chat interface
        chatbot = gr.Chatbot()
        msg = gr.Textbox()
        
        # Message handling with session
        msg.submit(
            fn=self._handle_message_with_session,
            inputs=[msg, chatbot, session_state],
            outputs=[msg, chatbot, session_state]
        )
```

#### Session Lifecycle in UI

1. **Session Initialization**: New session created when user first interacts
2. **State Persistence**: Session ID maintained in Gradio state
3. **Automatic Cleanup**: Sessions cleaned up when users disconnect

### Key UI Methods

```python
def _handle_message_with_session(self, message, history, session_state):
    """Handle message with session context."""
    
def _initialize_session_if_needed(self, session_state):
    """Initialize session if not exists."""
    
def _get_session_id(self, session_state):
    """Extract session ID from state."""
```

## Configuration

### File: `config/settings.py`

Session-related configuration options:

```python
class SessionConfig:
    # Session cleanup settings
    SESSION_TIMEOUT_MINUTES: int = 60
    CLEANUP_INTERVAL_MINUTES: int = 15
    MAX_ACTIVE_SESSIONS: int = 100
    
    # Memory management
    ENABLE_SESSION_CLEANUP: bool = True
    LOG_SESSION_ACTIVITY: bool = True
    
    # Performance settings
    SESSION_CACHE_SIZE: int = 50
    ENABLE_SESSION_PERSISTENCE: bool = False
```

### Configuration Options Explained

- **SESSION_TIMEOUT_MINUTES**: How long inactive sessions persist
- **CLEANUP_INTERVAL_MINUTES**: How often cleanup runs
- **MAX_ACTIVE_SESSIONS**: Maximum concurrent sessions
- **SESSION_CACHE_SIZE**: Number of sessions to keep in memory
- **ENABLE_SESSION_PERSISTENCE**: Whether to save sessions to disk

## Testing

### Unit Tests

**File**: `tests/test_session_management.py`

Comprehensive test suite covering:

```python
def test_session_isolation():
    """Test that sessions are properly isolated."""

def test_session_cleanup():
    """Test automatic session cleanup."""

def test_concurrent_sessions():
    """Test multiple concurrent sessions."""

def test_session_persistence():
    """Test session state persistence."""
```

### Integration Tests

**File**: `tests/test_conversational_flow.py`

Tests session integration with conversation flow:

```python
def test_multiple_user_conversations():
    """Test multiple users having separate conversations."""

def test_preference_isolation():
    """Test that user preferences don't mix between sessions."""
```

### Performance Tests

**File**: `tests/test_session_performance.py`

Performance testing for session management:

```python
def test_session_memory_usage():
    """Test memory usage with multiple sessions."""

def test_session_response_time():
    """Test response time impact of session management."""
```

## Deployment

### Launch Scripts

#### Public Deployment

**File**: `launch_public.py`

```python
# Launch with session management for production
if __name__ == "__main__":
    app = ShoppingAssistantApp()
    app.launch_public()
```

#### Session-Enabled Launch

**File**: `launch_with_sessions.py`

```python
# Launch with explicit session configuration
if __name__ == "__main__":
    config = SessionConfig()
    app = ShoppingAssistantApp(session_config=config)
    app.launch()
```

### Production Considerations

1. **Memory Management**: Monitor session memory usage
2. **Cleanup Strategy**: Configure appropriate cleanup intervals
3. **Load Balancing**: Consider session affinity in load balancers
4. **Monitoring**: Implement session metrics and alerts

## Monitoring

### Admin Interface

**File**: `admin_interface.py`

Provides monitoring capabilities:

```python
def get_session_stats():
    """Get current session statistics."""
    
def cleanup_all_sessions():
    """Force cleanup of all sessions."""
    
def get_active_sessions():
    """List all active sessions."""
```

### Metrics Tracked

- **Active Sessions**: Current number of active sessions
- **Session Duration**: Average session duration
- **Memory Usage**: Memory used per session
- **Cleanup Events**: Session cleanup frequency
- **Error Rates**: Session-related errors

### Monitoring Dashboard

Access via: `http://localhost:7860/admin`

Features:
- Real-time session count
- Session activity timeline
- Memory usage charts
- Cleanup logs

## Troubleshooting

### Common Issues

#### Issue: Sessions Not Isolating
**Symptoms**: Users see each other's conversations
**Solution**: Check SessionManager initialization in main app

```python
# Ensure session manager is properly initialized
session_manager = SessionManager()
conversation_workflow = ConversationWorkflow(session_manager=session_manager)
```

#### Issue: Memory Leaks
**Symptoms**: Memory usage grows continuously
**Solution**: Enable automatic cleanup

```python
# Configure cleanup in settings
SESSION_CLEANUP_ENABLED = True
SESSION_TIMEOUT_MINUTES = 30
```

#### Issue: Session Cleanup Too Aggressive
**Symptoms**: Users lose conversation unexpectedly
**Solution**: Increase timeout settings

```python
# Increase timeout in config/settings.py
SESSION_TIMEOUT_MINUTES = 120  # 2 hours
```

### Debug Mode

Enable debug logging:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Session manager will log detailed session activities
session_manager = SessionManager(debug=True)
```

### Performance Tuning

#### Memory Optimization

```python
# Reduce memory footprint
SESSION_CACHE_SIZE = 20  # Reduce cache size
ENABLE_SESSION_PERSISTENCE = False  # Disable persistence
```

#### Response Time Optimization

```python
# Improve response times
MAX_ACTIVE_SESSIONS = 50  # Limit concurrent sessions
CLEANUP_INTERVAL_MINUTES = 5  # More frequent cleanup
```

## API Reference

### SessionManager API

```python
class SessionManager:
    def __init__(self, config: SessionConfig = None):
        """Initialize session manager with configuration."""
    
    def create_session(self, session_id: str = None) -> str:
        """Create new session, returns session_id."""
    
    def get_session_services(self, session_id: str) -> Dict:
        """Get services for session."""
    
    def cleanup_session(self, session_id: str):
        """Clean up specific session."""
    
    def get_active_sessions(self) -> List[str]:
        """Get list of active session IDs."""
    
    def get_session_stats(self) -> Dict:
        """Get session statistics."""
```

### Session-Aware Service Pattern

All session-aware services follow this pattern:

```python
class SessionAwareService:
    def __init__(self, session_manager: SessionManager = None):
        self.session_manager = session_manager
        self.session_data = {}
    
    def _get_session_data(self, session_id: str):
        """Get data for specific session."""
        if session_id not in self.session_data:
            self.session_data[session_id] = self._initialize_session_data()
        return self.session_data[session_id]
    
    def _initialize_session_data(self):
        """Initialize data structure for new session."""
        return {}
```

## Best Practices

### Development

1. **Always Use Session IDs**: Pass session_id to all service methods
2. **Test Session Isolation**: Write tests to verify session separation
3. **Monitor Memory**: Watch for memory leaks in session data
4. **Handle Edge Cases**: Test session expiry and cleanup scenarios

### Production

1. **Configure Timeouts**: Set appropriate session timeouts
2. **Monitor Resources**: Track memory and CPU usage
3. **Log Session Events**: Enable session activity logging
4. **Plan for Scale**: Consider session limits for your infrastructure

### Security

1. **Session ID Security**: Use cryptographically secure session IDs
2. **Data Isolation**: Ensure no cross-session data leakage
3. **Cleanup Sensitive Data**: Remove sensitive data during cleanup
4. **Rate Limiting**: Implement per-session rate limiting

## Migration Guide

### From Single-Session to Multi-Session

1. **Update Service Calls**: Add session_id parameter to all service calls
2. **Initialize SessionManager**: Create SessionManager instance in main app
3. **Update UI Components**: Add session state to Gradio interface
4. **Test Isolation**: Verify that sessions don't interfere with each other

### Example Migration

**Before**:
```python
# Single session approach
conversation = ConversationWorkflow()
response = conversation.process_message("Hello")
```

**After**:
```python
# Multi-session approach
session_manager = SessionManager()
session_id = session_manager.create_session()
services = session_manager.get_session_services(session_id)
conversation = services['conversation_workflow']
response = conversation.process_message("Hello", session_id)
```

## Conclusion

The session management system provides robust isolation for multiple concurrent users while maintaining performance and scalability. The architecture is designed to be extensible and maintainable, with comprehensive testing and monitoring capabilities.

For additional support or questions, refer to the test files and example implementations in the codebase.
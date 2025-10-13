# services/session_manager.py
"""
Session Manager for Shopping Assistant
Manages per-user sessions to prevent cross-contamination of user data
"""

import uuid
from typing import Dict, Optional
from datetime import datetime, timedelta
import threading
from services.enhanced_preference_service import EnhancedPreferenceService


class SessionData:
    """Container for user session data"""
    
    def __init__(self, session_id: str, preference_service, workflow):
        self.session_id = session_id
        self.preference_service = preference_service
        self.workflow = workflow
        self.created_at = datetime.now()
        self.last_accessed = datetime.now()
        self.chat_history_ui = []  # UI-specific chat history
        
        # Search pagination state
        self.last_search_query = None
        self.last_search_preferences = None
        self.last_search_results = []  # Complete results from last search
        self.displayed_count = 0  # How many results currently displayed
        self.has_more_results = False  # Whether there are more results to show
        
    def update_access_time(self):
        """Update the last accessed timestamp"""
        self.last_accessed = datetime.now()
        
    def is_expired(self, timeout_hours: int = 24) -> bool:
        """Check if session has expired"""
        return datetime.now() - self.last_accessed > timedelta(hours=timeout_hours)
    
    def update_search_state(self, query: str, preferences, all_results: list, displayed_count: int = 6):
        """Update the search pagination state"""
        self.last_search_query = query
        self.last_search_preferences = preferences
        self.last_search_results = all_results
        self.displayed_count = displayed_count
        self.has_more_results = len(all_results) > displayed_count
        
    def can_show_more(self) -> bool:
        """Check if more results can be shown"""
        return (self.has_more_results and 
                self.last_search_results and 
                self.displayed_count < len(self.last_search_results))
    
    def get_next_results(self, batch_size: int = 6) -> list:
        """Get the next batch of results"""
        if not self.can_show_more():
            return []
        
        start_idx = self.displayed_count
        end_idx = min(start_idx + batch_size, len(self.last_search_results))
        next_batch = self.last_search_results[start_idx:end_idx]
        
        self.displayed_count = end_idx
        self.has_more_results = end_idx < len(self.last_search_results)
        
        return next_batch
    
    def clear_search_state(self):
        """Clear search pagination state"""
        self.last_search_query = None
        self.last_search_preferences = None
        self.last_search_results = []
        self.displayed_count = 0
        self.has_more_results = False


class SessionManager:
    """Manages user sessions to prevent cross-contamination"""
    
    def __init__(self, azure_service, search_service, formatter, session_timeout_hours: int = 24):
        self.azure_service = azure_service
        self.search_service = search_service
        self.formatter = formatter
        self.session_timeout_hours = session_timeout_hours
        
        # Thread-safe session storage
        self._sessions: Dict[str, SessionData] = {}
        self._lock = threading.RLock()
        
        # Query analytics
        self.query_count = 0
        self.session_created_count = 0
        
        # Cleanup thread for expired sessions
        self._cleanup_thread = threading.Thread(target=self._cleanup_expired_sessions, daemon=True)
        self._cleanup_thread.start()
    
    def log_user_query(self, session_id: str, query: str, response_type: str = "unknown", metrics=None):
        """Log user queries for monitoring and analytics with performance metrics"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        session_short = session_id[:8] if session_id else "unknown"
        self.query_count += 1
        
        # Enhanced query log with metrics
        query_log = f"📝 [{timestamp}] [USER_QUERY] Session: {session_short} | Type: {response_type} | Query: {query}"
        
        # Add performance metrics if available
        if metrics and 'tokens' in metrics:
            query_log += f" | ✨ Tokens: {metrics['tokens']} | ⏱️ {metrics['latency']:.2f}s"
            if 'cost' in metrics:
                query_log += f" | 💰 ${metrics['cost']:.4f}"
        
        print(query_log)
        
        # Analytics summary
        analytics_log = f"📊 [{timestamp}] [ANALYTICS] Total queries: {self.query_count} | Active sessions: {len(self._sessions)}"
        analytics_log += f" | LangSmith tracking: {'✅' if self.azure_service.is_langsmith_enabled() else '❌'}"
        
        print(analytics_log)
    
    def create_session(self) -> str:
        """Create a new session and return session ID"""
        session_id = str(uuid.uuid4())
        
        with self._lock:
            # Create isolated services for this session
            preference_service = EnhancedPreferenceService(self.azure_service)
            
            # Import here to avoid circular import
            from workflows.conversation_flow import ConversationWorkflow
            workflow = ConversationWorkflow(
                preference_service,
                self.search_service,
                self.azure_service,
                self.formatter,
                session_manager=self  # Pass session manager for pagination support
            )
            
            session_data = SessionData(session_id, preference_service, workflow)
            self._sessions[session_id] = session_data
            self.session_created_count += 1
            
        print(f"🆔 [SESSION_CREATED] {session_id[:8]} | Total sessions created: {self.session_created_count}")
        return session_id
    
    def get_session(self, session_id: str) -> Optional[SessionData]:
        """Get session data by ID"""
        with self._lock:
            session_data = self._sessions.get(session_id)
            if session_data and not session_data.is_expired(self.session_timeout_hours):
                session_data.update_access_time()
                return session_data
            elif session_data:
                # Session expired, remove it
                del self._sessions[session_id]
                print(f"🗑️ Removed expired session: {session_id}")
        return None
    
    def delete_session(self, session_id: str):
        """Manually delete a session"""
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                print(f"🗑️ Deleted session: {session_id}")
    
    def get_or_create_session(self, session_id: Optional[str] = None) -> tuple[str, SessionData]:
        """Get existing session or create new one"""
        if session_id:
            session_data = self.get_session(session_id)
            if session_data:
                return session_id, session_data
        
        # Create new session if none exists or expired
        new_session_id = self.create_session()
        session_data = self.get_session(new_session_id)
        return new_session_id, session_data
    
    def _cleanup_expired_sessions(self):
        """Background cleanup of expired sessions"""
        import time
        while True:
            try:
                time.sleep(3600)  # Check every hour
                expired_sessions = []
                
                with self._lock:
                    for session_id, session_data in self._sessions.items():
                        if session_data.is_expired(self.session_timeout_hours):
                            expired_sessions.append(session_id)
                    
                    for session_id in expired_sessions:
                        del self._sessions[session_id]
                
                if expired_sessions:
                    print(f"🧹 Cleaned up {len(expired_sessions)} expired sessions")
                    
            except Exception as e:
                print(f"❌ Error in session cleanup: {e}")
    
    def get_session_count(self) -> int:
        """Get current number of active sessions"""
        with self._lock:
            return len(self._sessions)
    
    def get_session_info(self) -> Dict[str, dict]:
        """Get information about all active sessions (for debugging)"""
        with self._lock:
            return {
                session_id: {
                    'created_at': session_data.created_at.isoformat(),
                    'last_accessed': session_data.last_accessed.isoformat(),
                    'chat_messages': len(session_data.chat_history_ui),
                    'preferences': session_data.preference_service.get_summary()
                }
                for session_id, session_data in self._sessions.items()
            }
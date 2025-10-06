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
from workflows.conversation_flow import ConversationWorkflow


class SessionData:
    """Container for user session data"""
    
    def __init__(self, session_id: str, preference_service, workflow):
        self.session_id = session_id
        self.preference_service = preference_service
        self.workflow = workflow
        self.created_at = datetime.now()
        self.last_accessed = datetime.now()
        self.chat_history_ui = []  # UI-specific chat history
        
    def update_access_time(self):
        """Update the last accessed timestamp"""
        self.last_accessed = datetime.now()
        
    def is_expired(self, timeout_hours: int = 24) -> bool:
        """Check if session has expired"""
        return datetime.now() - self.last_accessed > timedelta(hours=timeout_hours)


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
        
        # Cleanup thread for expired sessions
        self._cleanup_thread = threading.Thread(target=self._cleanup_expired_sessions, daemon=True)
        self._cleanup_thread.start()
    
    def create_session(self) -> str:
        """Create a new session and return session ID"""
        session_id = str(uuid.uuid4())
        
        with self._lock:
            # Create isolated services for this session
            preference_service = EnhancedPreferenceService(self.azure_service)
            workflow = ConversationWorkflow(
                preference_service,
                self.search_service,
                self.azure_service,
                self.formatter
            )
            
            session_data = SessionData(session_id, preference_service, workflow)
            self._sessions[session_id] = session_data
            
        print(f"🆔 Created new session: {session_id}")
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
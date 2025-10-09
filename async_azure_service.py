# async_azure_service.py
"""
Async wrapper for Azure service to improve parallel processing performance
"""

import asyncio
import time
from typing import Dict, Any
from services.azure_service import AzureService


class AsyncAzureService:
    """Async wrapper for Azure service operations"""
    
    def __init__(self, azure_service: AzureService):
        self.azure_service = azure_service
        self._loop = None
    
    async def extract_preferences_async(self, user_input: str, current_preferences: Dict = None) -> Dict:
        """Extract preferences asynchronously"""
        loop = asyncio.get_event_loop()
        
        # Run the synchronous operation in a thread pool
        return await loop.run_in_executor(
            None, 
            self.azure_service.extract_preferences,
            user_input,
            current_preferences
        )
    
    async def conversation_chain_async(self, query_data: Dict[str, Any]) -> Dict[str, Any]:
        """Run conversation chain asynchronously"""
        loop = asyncio.get_event_loop()
        
        # Run the synchronous operation in a thread pool
        return await loop.run_in_executor(
            None,
            self.azure_service.conversation_chain.invoke,
            query_data
        )
    
    async def generate_embeddings_async(self, text: str) -> list:
        """Generate embeddings asynchronously"""
        loop = asyncio.get_event_loop()
        
        return await loop.run_in_executor(
            None,
            self.azure_service.embeddings.embed_query,
            text
        )
    
    def is_available(self) -> bool:
        """Check if Azure service is available"""
        return self.azure_service.is_available()
    
    @property
    def embeddings(self):
        """Access to embeddings service"""
        return self.azure_service.embeddings
    
    @property
    def conversation_chain(self):
        """Access to conversation chain"""
        return self.azure_service.conversation_chain


class AsyncConversationWorkflow:
    """Async version of conversation workflow for better parallel processing"""
    
    def __init__(self, preference_service, search_service, azure_service, formatter, session_manager=None):
        self.preference_service = preference_service
        self.search_service = search_service
        self.azure_service = AsyncAzureService(azure_service)
        self.formatter = formatter
        self.session_manager = session_manager
        
        # Import the original workflow for delegation
        from workflows.conversation_flow import ConversationWorkflow
        self.sync_workflow = ConversationWorkflow(
            preference_service, search_service, azure_service, formatter, session_manager
        )
    
    async def process_message_async(self, user_input: str, session_id: str = None) -> str:
        """Process message asynchronously for better parallel performance"""
        start_time = time.time()
        
        try:
            # Check if this is a "show more" request (quick, no Azure calls needed)
            if self._is_show_more_request(user_input):
                # Run in thread pool since it's still synchronous but fast
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    self.sync_workflow._handle_show_more_request,
                    session_id
                )
                return result
            
            # For complex queries that need Azure services, run asynchronously
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self.sync_workflow.process_message,
                user_input,
                session_id
            )
            
            processing_time = time.time() - start_time
            print(f"⚡ Async message processing completed in {processing_time:.2f}s for session {session_id[:8] if session_id else 'unknown'}")
            
            return result
            
        except Exception as e:
            print(f"❌ Async processing error for session {session_id[:8] if session_id else 'unknown'}: {e}")
            return "I apologize, but I'm experiencing some technical difficulties. Please try again."
    
    def _is_show_more_request(self, user_input: str) -> bool:
        """Check if user is requesting to show more results"""
        show_more_patterns = [
            "show more", "more results", "more options", "see more", 
            "show me more", "load more", "more products", "next",
            "continue", "more items", "additional results"
        ]
        user_input_lower = user_input.lower().strip()
        return any(pattern in user_input_lower for pattern in show_more_patterns)
    
    def clear_memory(self):
        """Clear conversation memory"""
        self.sync_workflow.clear_memory()


# Monkey patch to replace workflow with async version
def enhance_session_with_async(session_manager):
    """Enhance existing session manager with async workflows"""
    print("🚀 Enhancing sessions with async processing...")
    
    # Replace workflows in existing sessions
    with session_manager._lock:
        for session_id, session_data in session_manager._sessions.items():
            # Create async workflow
            async_workflow = AsyncConversationWorkflow(
                session_data.preference_service,
                session_manager.search_service,
                session_manager.azure_service,
                session_manager.formatter,
                session_manager
            )
            
            # Replace the workflow but keep the original as fallback
            session_data.workflow._async_version = async_workflow
            
            # Add async processing method
            async def async_process_message(user_input: str, session_id: str = None):
                return await async_workflow.process_message_async(user_input, session_id)
            
            session_data.workflow.process_message_async = async_process_message
    
    print(f"✅ Enhanced {session_manager.get_session_count()} sessions with async processing")


def create_async_session_data_class():
    """Create enhanced session data class with async support"""
    from services.session_manager import SessionData
    
    class AsyncSessionData(SessionData):
        """Enhanced session data with async processing support"""
        
        def __init__(self, session_id: str, preference_service, workflow):
            super().__init__(session_id, preference_service, workflow)
            
            # Enhance workflow with async capabilities
            if not hasattr(workflow, '_async_version'):
                workflow._async_version = AsyncConversationWorkflow(
                    preference_service,
                    None,  # Will be set by session manager
                    None,  # Will be set by session manager
                    None,  # Will be set by session manager
                    None   # Will be set by session manager
                )
        
        async def process_message_async(self, user_input: str) -> str:
            """Process message asynchronously"""
            if hasattr(self.workflow, '_async_version'):
                return await self.workflow._async_version.process_message_async(user_input, self.session_id)
            else:
                # Fallback to sync processing in thread pool
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    None,
                    self.workflow.process_message,
                    user_input,
                    self.session_id
                )
    
    return AsyncSessionData
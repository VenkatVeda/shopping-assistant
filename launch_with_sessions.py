# launch_with_sessions.py
"""
Launch script for Shopping Assistant with Session Management
This demonstrates the session isolation capabilities
"""

import sys
import time
from main import ShoppingAssistantApp

def launch_with_session_monitoring():
    """Launch the app with session monitoring"""
    try:
        print("🎯 Launching Shopping Assistant with Advanced Session Management")
        print("="*70)
        print("✨ Features:")
        print("   • Isolated user sessions")
        print("   • Automatic session cleanup")
        print("   • Concurrent user support")
        print("   • Session-based chat history")
        print("   • Per-user preference isolation")
        print("   • Pagination with 'Show More' functionality")
        print("="*70)
        
        app = ShoppingAssistantApp()
        
        # Print session manager stats
        print(f"\n🔧 Session Configuration:")
        print(f"   • Session timeout: 24 hours")
        print(f"   • Cleanup interval: 1 hour")
        print(f"   • Initial active sessions: {app.session_manager.get_session_count()}")
        
        # Launch the application
        app.launch(
            server_name="0.0.0.0",
            server_port=7860,
            share=False,
            debug=True,
            show_error=True
        )
        
    except KeyboardInterrupt:
        print("\n👋 Shutting down gracefully...")
        if app and app.session_manager:
            print(f"   Final session count: {app.session_manager.get_session_count()}")
    except Exception as e:
        print(f"❌ Error starting application: {e}")
        raise

def test_session_isolation():
    """Test that sessions are properly isolated"""
    print("🧪 Testing Session Isolation...")
    
    app = ShoppingAssistantApp()
    session_manager = app.session_manager
    
    # Create two test sessions
    session1_id, session1_data = session_manager.get_or_create_session()
    session2_id, session2_data = session_manager.get_or_create_session()
    
    print(f"Created session 1: {session1_id[:8]}...")
    print(f"Created session 2: {session2_id[:8]}...")
    
    # Test preference isolation
    session1_data.preference_service.update_preferences("I want red leather bags under $100")
    session2_data.preference_service.update_preferences("I want blue canvas bags over $200")
    
    print(f"Session 1 preferences: {session1_data.preference_service.get_summary()}")
    print(f"Session 2 preferences: {session2_data.preference_service.get_summary()}")
    
    # Test chat history isolation
    session1_data.chat_history_ui.append(("user", "Hello from session 1"))
    session2_data.chat_history_ui.append(("user", "Hello from session 2"))
    
    print(f"Session 1 chat messages: {len(session1_data.chat_history_ui)}")
    print(f"Session 2 chat messages: {len(session2_data.chat_history_ui)}")
    
    print(f"Total active sessions: {session_manager.get_session_count()}")
    print("✅ Session isolation test completed!")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_session_isolation()
    else:
        launch_with_session_monitoring()
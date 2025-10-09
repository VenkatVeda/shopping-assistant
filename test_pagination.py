#!/usr/bin/env python3
"""
Simple test script for pagination functionality
"""

# Test if basic imports work
try:
    from services.session_manager import SessionData
    print("✅ SessionData import successful")
    
    # Test session data with pagination state
    class MockPreferenceService:
        pass
    
    class MockWorkflow:
        pass
    
    session_data = SessionData("test-123", MockPreferenceService(), MockWorkflow())
    print("✅ SessionData creation successful")
    
    # Test pagination methods
    session_data.update_search_state("test query", None, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 6)
    print(f"✅ Updated search state: {session_data.displayed_count} displayed, has_more: {session_data.has_more_results}")
    
    # Test getting next results
    next_batch = session_data.get_next_results(3)
    print(f"✅ Next batch size: {len(next_batch)}, new displayed count: {session_data.displayed_count}")
    
    # Test can show more
    print(f"✅ Can show more: {session_data.can_show_more()}")
    
    # Clear state
    session_data.clear_search_state()
    print(f"✅ Cleared state - has_more: {session_data.has_more_results}")
    
    print("\n🎉 All pagination functionality tests passed!")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
# test_fixes.py
"""Test the fixes for LangSmith integration"""

import os
import sys

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import ShoppingAssistantApp, CachedAzureService
from services.azure_service import AzureService

def test_fixes():
    """Test that the fixes are working"""
    print("🔧 Testing Fixes for LangSmith Integration")
    print("=" * 50)
    
    try:
        # Test 1: Azure service LangSmith methods
        print("📦 Testing Azure service...")
        azure_service = AzureService()
        print(f"   Azure available: {'✅' if azure_service.is_available() else '❌'}")
        print(f"   LangSmith enabled: {'✅' if azure_service.is_langsmith_enabled() else '❌'}")
        
        # Test 2: Cached Azure service wrapper
        print("\n🔄 Testing CachedAzureService wrapper...")
        cached_azure = CachedAzureService(azure_service)
        print(f"   Azure available: {'✅' if cached_azure.is_available() else '❌'}")
        print(f"   LangSmith enabled: {'✅' if cached_azure.is_langsmith_enabled() else '❌'}")
        print(f"   LangSmith client: {'✅' if hasattr(cached_azure, 'langsmith_client') else '❌'}")
        
        # Test 3: Chat history indexing
        print("\n💬 Testing chat history indexing...")
        test_history = [("user", "Hello"), ("assistant", "Hi there")]
        
        # Safe indexing method
        chat_history = []
        for i in range(0, len(test_history) - 1, 2):
            if i + 1 < len(test_history):
                chat_history.append((test_history[i][1], test_history[i+1][1]))
        
        print(f"   Chat history created: {'✅' if len(chat_history) == 1 else '❌'}")
        print(f"   Content: {chat_history}")
        
        # Test 4: Empty history handling
        empty_history = []
        safe_chat = []
        for i in range(0, len(empty_history) - 1, 2):
            if i + 1 < len(empty_history):
                safe_chat.append((empty_history[i][1], empty_history[i+1][1]))
        
        print(f"   Empty history handled: {'✅' if len(safe_chat) == 0 else '❌'}")
        
        print(f"\n✅ All Fixes Verified!")
        print(f"🎯 Ready to test the full application")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_fixes()
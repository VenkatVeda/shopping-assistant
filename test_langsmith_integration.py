# test_langsmith_integration.py
"""Test script for LangSmith integration"""

import os
import sys

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.azure_service import AzureService
from config.settings import LANGSMITH_CONFIG

def test_langsmith_integration():
    """Test the LangSmith integration"""
    print("🔍 Testing LangSmith Integration")
    print("=" * 50)
    
    try:
        # Check LangSmith configuration
        print("📋 LangSmith Configuration:")
        print(f"   API Key: {'✅ Configured' if LANGSMITH_CONFIG['api_key'] else '❌ Missing'}")
        print(f"   Project: {LANGSMITH_CONFIG['project']}")
        print(f"   Tracing: {'✅ Enabled' if LANGSMITH_CONFIG['tracing'] else '❌ Disabled'}")
        print(f"   Endpoint: {LANGSMITH_CONFIG['endpoint']}")
        
        # Check environment variables
        print(f"\n🌍 Environment Variables:")
        env_vars = ['LANGCHAIN_API_KEY', 'LANGCHAIN_PROJECT', 'LANGCHAIN_TRACING_V2', 'LANGCHAIN_ENDPOINT']
        for var in env_vars:
            value = os.getenv(var)
            status = "✅ Set" if value else "❌ Missing"
            print(f"   {var}: {status}")
        
        # Initialize Azure service
        print(f"\n🚀 Initializing Azure Service...")
        azure_service = AzureService()
        
        print(f"   Azure available: {'✅' if azure_service.is_available() else '❌'}")
        print(f"   LangSmith enabled: {'✅' if azure_service.is_langsmith_enabled() else '❌'}")
        
        if azure_service.is_langsmith_enabled():
            print(f"   LangSmith client: {type(azure_service.langsmith_client).__name__}")
        
        # Test LangSmith tracking
        if azure_service.is_available() and azure_service.conversation_chain:
            print(f"\n🧪 Testing LangSmith automatic tracking...")
            
            # This will be automatically tracked by LangSmith
            result = azure_service.conversation_chain.invoke({
                "preferences": "Looking for leather bags",
                "recent_chat_history": "",
                "question": "Show me some leather bags"
            })
            
            print(f"✅ LLM call completed (check LangSmith dashboard for metrics)")
            print(f"📊 Response: {result.get('text', '')[:100] if isinstance(result, dict) else str(result)[:100]}...")
            
            if azure_service.is_langsmith_enabled():
                print(f"🔍 View detailed metrics at: https://smith.langchain.com")
                print(f"📈 Project: {LANGSMITH_CONFIG['project']}")
        else:
            print(f"\n⚠️ Azure service not fully available - this is expected in local testing")
        
        print(f"\n✅ LangSmith Integration Test Results:")
        print(f"   Configuration: {'✅ Complete' if LANGSMITH_CONFIG['api_key'] else '⚠️ API key needed'}")
        print(f"   Environment: {'✅ Ready' if os.getenv('LANGCHAIN_TRACING_V2') else '⚠️ Variables needed'}")
        print(f"   Service Integration: {'✅ Ready' if azure_service.is_langsmith_enabled() else '⚠️ Client not initialized'}")
        
        print(f"\n🎯 Next Steps:")
        if not LANGSMITH_CONFIG['api_key']:
            print(f"   1. Get LangSmith API key from https://smith.langchain.com")
            print(f"   2. Add LANGCHAIN_API_KEY to your .env file")
            print(f"   3. Set LANGCHAIN_API_KEY secret in Render dashboard")
        else:
            print(f"   1. ✅ Configuration complete!")
            print(f"   2. ✅ Deploy to Render with LangSmith tracking")
            print(f"   3. ✅ Monitor metrics at https://smith.langchain.com")
        
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_langsmith_integration()
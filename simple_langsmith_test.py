# simple_langsmith_test.py
"""Simple test to demonstrate LangSmith tracking in action"""

import os
import sys

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.azure_service import AzureService
from workflows.conversation_flow import ConversationWorkflow
from services.enhanced_preference_service import EnhancedPreferenceService
from services.vector_service import VectorService
from services.search_service import SearchService
from utils.data_loader import DataLoader
from ui.formatters import ProductFormatter

def simple_langsmith_demo():
    """Simple demo of LangSmith tracking working"""
    print("🎯 Simple LangSmith Integration Demo")
    print("=" * 50)
    
    try:
        # Initialize minimal services
        print("📦 Initializing services...")
        azure_service = AzureService()
        
        if not azure_service.is_available():
            print("⚠️ Azure service not available - skipping LLM test")
            return
        
        if not azure_service.is_langsmith_enabled():
            print("⚠️ LangSmith not enabled - check credentials")
            return
        
        print(f"✅ Azure service available")
        print(f"✅ LangSmith enabled for project: pr-roasted-ephemera-54")
        
        # Test direct LLM call (this will be tracked in LangSmith)
        print("\n🤖 Testing LangSmith automatic tracking...")
        print("🔍 Making LLM call - check LangSmith dashboard for real-time metrics!")
        
        result = azure_service.conversation_chain.invoke({
            "preferences": "Looking for stylish bags",
            "recent_chat_history": "",
            "question": "What bags do you recommend?"
        })
        
        print(f"✅ LLM Response: {result.get('text', '')[:150]}...")
        print(f"\n📊 Metrics automatically tracked in LangSmith!")
        print(f"🌐 View at: https://smith.langchain.com")
        print(f"📈 Project: pr-roasted-ephemera-54")
        print(f"⏱️ Check dashboard for:")
        print(f"   - Token usage (input + output)")
        print(f"   - Response latency")
        print(f"   - API costs")
        print(f"   - Full conversation trace")
        
        # Test another call to show multiple traces
        print(f"\n🔄 Making second call for comparison...")
        result2 = azure_service.conversation_chain.invoke({
            "preferences": "Budget-friendly options",
            "recent_chat_history": "User: What bags do you recommend?\nAssistant: " + result.get('text', '')[:100] + "...",
            "question": "Show me something under $50"
        })
        
        print(f"✅ Second Response: {result2.get('text', '')[:150]}...")
        print(f"\n🎉 Demo Complete!")
        print(f"📊 Two traces now visible in LangSmith dashboard")
        print(f"🔍 You can compare their performance metrics directly")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    simple_langsmith_demo()
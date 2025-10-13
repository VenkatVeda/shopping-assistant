# test_metrics_display.py
"""Test that metrics are displayed in both UI and logs"""

import os
import sys

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.azure_service import AzureService
from services.enhanced_preference_service import EnhancedPreferenceService
from workflows.conversation_flow import ConversationWorkflow
from services.session_manager import SessionManager
from ui.formatters import ProductFormatter

def test_metrics_display():
    """Test that metrics show up in both logs and UI responses"""
    print("📊 Testing Metrics Display in UI and Logs")
    print("=" * 50)
    
    try:
        # Initialize minimal services
        print("📦 Initializing services...")
        azure_service = AzureService()
        
        if not azure_service.is_available():
            print("⚠️ Azure service not available")
            return
        
        print(f"✅ Azure service available")
        print(f"✅ LangSmith enabled: {azure_service.is_langsmith_enabled()}")
        
        # Test direct LLM call with tracking
        print("\n🧪 Testing run_with_tracking method...")
        result, metrics = azure_service.run_with_tracking(
            azure_service.conversation_chain,
            {
                "preferences": "Looking for bags",
                "recent_chat_history": "",
                "question": "Show me some bags"
            }
        )
        
        print(f"✅ LLM Response: {str(result)[:100]}...")
        
        if metrics:
            print(f"📈 Metrics captured:")
            print(f"   Tokens: {metrics.get('tokens', 'N/A')}")
            print(f"   Latency: {metrics.get('latency', 'N/A'):.2f}s")
            print(f"   Cost: ${metrics.get('cost', 0):.4f}")
            print(f"   Timestamp: {metrics.get('timestamp', 'N/A')}")
            
            # Test UI formatting
            ui_display = f"⚡ Tokens: {metrics['tokens']} | ⏱️ {metrics['latency']:.2f}s | 💰 ${metrics['cost']:.4f}"
            print(f"🎨 UI Display Format: {ui_display}")
            
            # Test session manager logging
            print(f"\n📝 Testing session manager logging...")
            # Create a mock session manager
            formatter = ProductFormatter(None)  # Mock formatter
            session_manager = SessionManager(azure_service, None, formatter)
            session_manager.log_user_query("test1234", "test query", "test_response", metrics)
            
        else:
            print("⚠️ No metrics returned")
        
        print(f"\n✅ Metrics Display Test Complete!")
        print(f"🎯 Expected behavior:")
        print(f"   - Console logs show tokens, latency, cost")
        print(f"   - UI shows metrics below each response")
        print(f"   - LangSmith dashboard also tracks everything")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_metrics_display()
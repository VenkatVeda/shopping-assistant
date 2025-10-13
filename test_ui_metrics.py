# test_ui_metrics.py
"""Quick test to see the UI with LangSmith performance metrics"""

import os
import sys
import time

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import ShoppingAssistantApp

def test_ui_with_langsmith():
    """Test the UI with LangSmith integration"""
    print("🎨 Testing UI with LangSmith Integration")
    print("=" * 50)
    
    try:
        # Initialize the complete application
        print("📦 Initializing Shopping Assistant with LangSmith...")
        app = ShoppingAssistantApp(enable_parallel=False)
        
        print("\n🚀 Starting Gradio interface...")
        print("📊 LangSmith tracking will be visible at: https://smith.langchain.com")
        print("🎯 Project: pr-roasted-ephemera-54")
        print("\n🌐 Opening interface at http://localhost:7860")
        print("💡 Try asking: 'Show me some leather bags'")
        print("🔍 All metrics will be automatically tracked in LangSmith!")
        print("\n⌨️ Press Ctrl+C to stop the server")
        
        # Launch the UI
        app.launch(
            share=False,
            debug=False,
            server_name="0.0.0.0",
            server_port=7860
        )
        
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_ui_with_langsmith()
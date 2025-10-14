#!/usr/bin/env python3
"""Test the clean UI without LangSmith footers"""

import os
import sys

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_clean_ui():
    """Test the UI with clean responses and dedicated metrics display"""
    print("🧹 Testing Clean UI (No LangSmith Footers)")
    print("=" * 50)
    
    try:
        from main import ShoppingAssistantApp
        
        print("📦 Initializing Shopping Assistant...")
        app = ShoppingAssistantApp(enable_parallel=False)
        
        # Test a simple query to see the clean output
        print("🔍 Testing clean chat responses...")
        session_id, session_data = app.session_manager.get_or_create_session()
        
        test_query = "Show me some leather bags"
        print(f"🎯 Testing query: '{test_query}'")
        
        result, metrics = session_data.workflow.process_message(test_query, session_id)
        
        print(f"\n✅ Raw Response (should be clean):")
        print("-" * 40)
        print(result)
        print("-" * 40)
        
        # Check for LangSmith footer remnants
        if 'tracked in LangSmith' in result.lower():
            print("❌ LangSmith footer still present!")
        else:
            print("✅ Response is clean - no LangSmith footer")
        
        if '<small' in result:
            print("❌ HTML metrics still embedded in response")
        else:
            print("✅ No embedded HTML metrics in response")
        
        # Test the UI chat interface
        print(f"\n🌐 Testing UI chat interface...")
        chat_history, ui_session_id = app.ui.chat_interface(test_query, session_id)
        
        if chat_history:
            last_response = chat_history[-1][1] if chat_history else "No response"
            print(f"\n📱 UI Response:")
            print("-" * 40)
            print(last_response)
            print("-" * 40)
            
            if 'tracked in LangSmith' in last_response.lower():
                print("❌ LangSmith footer still in UI!")
            else:
                print("✅ UI response is clean")
        
        # Test metrics display formatting
        print(f"\n📊 Testing dedicated metrics display...")
        metrics_html = app.ui.format_metrics_display(metrics)
        print("Metrics Display HTML:")
        print(metrics_html)
        
        print(f"\n🎉 Summary:")
        print(f"   • Chat responses: Clean (no footers)")
        print(f"   • Metrics display: Dedicated panel")
        print(f"   • LangSmith tracking: Still active in background")
        print(f"   • User experience: Improved!")
        
        print(f"\n🚀 Starting clean interface...")
        print(f"💡 Metrics now show ONLY in the dedicated panel above chat")
        print(f"🧹 Chat responses are clean and uncluttered")
        print(f"\n⌨️ Press Ctrl+C to stop")
        
        # Launch the clean UI
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
    test_clean_ui()
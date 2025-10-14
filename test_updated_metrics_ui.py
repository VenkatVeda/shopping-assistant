#!/usr/bin/env python3
"""Test the updated UI with dedicated metrics display"""

import os
import sys

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_updated_ui():
    """Test the updated UI with dedicated metrics display"""
    print("🎨 Testing Updated UI with Dedicated Metrics Display")
    print("=" * 60)
    
    try:
        from main import ShoppingAssistantApp
        
        print("📦 Initializing Shopping Assistant...")
        app = ShoppingAssistantApp(enable_parallel=False)
        
        print("🎯 Testing metrics display functionality...")
        
        # Test metrics formatting
        ui = app.ui
        
        # Test empty metrics
        empty_metrics = ui.format_metrics_display()
        print("✅ Empty metrics display:")
        print(empty_metrics)
        
        # Test with sample metrics
        sample_metrics = {
            'tokens': 1535,
            'latency': 1.35,
            'cost': 0.0003,
            'timestamp': '08:07:42'
        }
        
        metrics_display = ui.format_metrics_display(sample_metrics)
        print("\n✅ Sample metrics display:")
        print(metrics_display)
        
        print("\n🌐 Starting interface with enhanced metrics display...")
        print("📊 Metrics will now show in a dedicated, highly visible component")
        print("🔍 Each query will update both inline metrics and the metrics panel")
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
    test_updated_ui()
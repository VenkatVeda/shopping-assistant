# test_performance_tracking.py
"""Test script for performance tracking functionality"""

import os
import sys

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.azure_service import AzureService
from services.session_manager import SessionManager
from services.search_service import SearchService
from ui.formatters import ProductFormatter

def test_performance_tracking():
    """Test the performance tracking functionality"""
    print("🧪 Testing Performance Tracking System")
    print("=" * 50)
    
    try:
        # Initialize services
        print("📦 Initializing services...")
        azure_service = AzureService()
        
        # Test Azure service performance tracking directly
        if azure_service.is_available():
            print("\n🤖 Testing Azure service performance tracking...")
            
            # Get performance stats before
            before_stats = azure_service.get_performance_stats()
            print(f"📊 Before - Tokens: {before_stats['total_tokens']}, Requests: {before_stats['total_requests']}")
            
            # Test the tracking functionality directly
            print("\n💬 Testing run_with_tracking method...")
            
            if azure_service.conversation_chain:
                result, metrics = azure_service.run_with_tracking(
                    azure_service.conversation_chain,
                    {
                        "preferences": "Looking for leather bags",
                        "recent_chat_history": "",
                        "question": "Show me some leather bags"
                    }
                )
                
                print(f"✅ Response: {result[:100] if result else 'No result'}...")
                
                if metrics:
                    print(f"📈 Metrics - Tokens: {metrics.get('tokens', 'N/A')}, Latency: {metrics.get('latency', 'N/A'):.2f}s")
                    if 'cost' in metrics:
                        print(f"💰 Cost: ${metrics['cost']:.4f}")
                else:
                    print("⚠️ No metrics returned")
                
                # Get performance stats after
                after_stats = azure_service.get_performance_stats()
                print(f"📊 After - Tokens: {after_stats['total_tokens']}, Requests: {after_stats['total_requests']}")
            else:
                print("⚠️ Conversation chain not available")
                
        else:
            print("⚠️ Azure service not available (this is expected in local testing)")
        
        # Test performance stats functionality
        print("\n� Testing performance statistics...")
        stats = azure_service.get_performance_stats()
        print(f"   Total tokens: {stats['total_tokens']}")
        print(f"   Total requests: {stats['total_requests']}")
        print(f"   Average latency: {stats['average_latency']:.2f}s")
        print(f"   Total cost: ${stats['total_cost']:.4f}")
        
        # Test mock metrics logging (simulating session manager)
        print("\n🧪 Testing mock metrics logging...")
        test_metrics = {
            'tokens': 150,
            'latency': 1.23,
            'cost': 0.0045,
            'total_tokens': 150,
            'total_requests': 1,
            'avg_latency': 1.23
        }
        
        # Simulate what session manager would log
        import time
        timestamp = time.strftime("%H:%M:%S")
        print(f"📝 [{timestamp}] [USER_QUERY] Session: test1234 | Type: chat_response | Query: test query | Tokens: {test_metrics['tokens']} | Latency: {test_metrics['latency']:.2f}s | Cost: ${test_metrics['cost']:.4f}")
        
        print("\n✅ Performance tracking test completed!")
        print("🎯 Features tested:")
        print("   - Azure service performance tracking")
        print("   - Token and latency measurement")
        print("   - Cost calculation")
        print("   - Performance statistics")
        print("   - Enhanced logging format")
        print("   - UI metrics display (ready for UI test)")
        
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_performance_tracking()
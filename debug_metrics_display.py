#!/usr/bin/env python3
"""Quick test to verify metrics display in UI"""

import time
from workflows.conversation_flow import ConversationFlow
from services.session_manager import SessionManager

def test_metrics_display():
    """Test that metrics are properly captured and returned"""
    
    print("🧪 Testing metrics display system...")
    
    # Initialize services
    session_manager = SessionManager()
    
    # Get a session
    session_id, session_data = session_manager.get_or_create_session()
    print(f"📋 Using session: {session_id}")
    
    # Test query
    test_query = "Hi, I need a black bag"
    
    print(f"🔍 Testing query: '{test_query}'")
    
    # Process the message
    start_time = time.time()
    result, metrics = session_data.workflow.process_message(test_query, session_id)
    processing_time = time.time() - start_time
    
    print(f"\n📊 RESULTS:")
    print(f"Response: {result[:100]}...")
    print(f"Processing time: {processing_time:.2f}s")
    
    print(f"\n🎯 METRICS ANALYSIS:")
    if metrics:
        print(f"✅ Metrics returned: {metrics}")
        print(f"   - Tokens: {metrics.get('tokens', 'N/A')}")
        print(f"   - Latency: {metrics.get('latency', 'N/A')}")
        print(f"   - Cost: {metrics.get('cost', 'N/A')}")
        print(f"   - Timestamp: {metrics.get('timestamp', 'N/A')}")
    else:
        print("❌ No metrics returned!")
        
        # Check if Azure service has metrics
        azure_service = session_data.workflow.azure_service
        if hasattr(azure_service, 'last_metrics') and azure_service.last_metrics:
            print(f"🔍 Found metrics in Azure service: {azure_service.last_metrics}")
        else:
            print("❌ No metrics found in Azure service either!")
    
    # Test UI formatting
    if metrics and 'tokens' in metrics:
        metrics_info = f"\n\n<small class='metrics-info'>⚡ Tokens: {metrics['tokens']} | ⏱️ {metrics['latency']:.2f}s"
        if 'cost' in metrics:
            metrics_info += f" | 💰 ${metrics['cost']:.4f}"
        metrics_info += "</small>"
        
        full_response = result + metrics_info
        print(f"\n🎨 UI FORMATTED RESPONSE:")
        print(f"{full_response}")
    else:
        print(f"\n❌ Cannot format metrics for UI - no metrics available!")

if __name__ == "__main__":
    test_metrics_display()
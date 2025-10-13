#!/usr/bin/env python3
"""Test metrics display through the full UI pipeline"""

import sys
import os
sys.path.append(os.path.dirname(__file__))

from ui.gradio_interface import GradioInterface
from services.session_manager import SessionManager

def test_full_ui_metrics():
    """Test metrics flow through the complete UI pipeline"""
    print("🧪 Testing complete UI metrics pipeline...")
    
    # Initialize session manager and UI
    session_manager = SessionManager()
    ui = GradioInterface(session_manager)
    
    print("✅ UI initialized")
    
    # Test different types of queries
    test_queries = [
        "Hi, I need a black bag",
        "Show me leather bags under $100",
        "What about red handbags?"
    ]
    
    for i, query in enumerate(test_queries, 1):
        print(f"\n🔍 Test {i}: '{query}'")
        
        try:
            # Use the chat interface directly
            history, session_id = ui.chat_interface(query, None)
            
            # Check the last response for metrics
            if history:
                last_response = history[-1][1]  # Assistant response
                print(f"📊 Response contains metrics: {'⚡ Tokens:' in last_response}")
                if '⚡ Tokens:' in last_response:
                    # Extract metrics from response
                    metrics_part = last_response.split('<small class=\'metrics-info\'>')[1].split('</small>')[0]
                    print(f"   Metrics: {metrics_part}")
                else:
                    print(f"   ❌ No metrics found in response")
                    print(f"   Response preview: {last_response[-200:]}")
            else:
                print("   ❌ No response received")
                
        except Exception as e:
            print(f"   ❌ Error: {e}")

if __name__ == "__main__":
    test_full_ui_metrics()
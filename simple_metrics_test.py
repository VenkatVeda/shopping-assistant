#!/usr/bin/env python3
"""Simple metrics test"""

import sys
import os
sys.path.append(os.path.dirname(__file__))

from services.azure_service import AzureService

def test_azure_metrics():
    """Test Azure service metrics directly"""
    print("🧪 Testing Azure service metrics...")
    
    azure_service = AzureService()
    
    # Test conversation
    test_input = {
        "preferences": "Looking for black bags",
        "recent_chat_history": "",
        "question": "Hi, I need a black bag"
    }
    
    print("🔍 Running LLM with tracking...")
    result, metrics = azure_service.run_with_tracking(
        azure_service.conversation_chain,
        test_input
    )
    
    print(f"\n📊 DIRECT METRICS TEST:")
    print(f"Result: {str(result)[:100]}...")
    print(f"Metrics: {metrics}")
    
    # Check stored metrics
    print(f"\n🔍 STORED METRICS:")
    print(f"Azure service last_metrics: {azure_service.last_metrics}")

if __name__ == "__main__":
    test_azure_metrics()
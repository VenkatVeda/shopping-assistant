"""
Test script to verify metrics display in product search
"""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from services.azure_service import AzureService
from services.enhanced_preference_service import EnhancedPreferenceService
from workflows.conversation_flow import ConversationWorkflow
from services.search_service import SearchService
from services.vector_service import VectorService
from ui.formatters import ProductFormatter
from utils.data_loader import DataLoader

print("🔍 Testing metrics display for product search...")

try:
    # Initialize services
    azure_service = AzureService()
    vector_service = VectorService()
    preference_service = EnhancedPreferenceService(azure_service)
    data_loader = DataLoader()
    search_service = SearchService(vector_service, data_loader)
    formatter = ProductFormatter()
    
    # Create workflow
    workflow = ConversationWorkflow(
        preference_service=preference_service,
        search_service=search_service,
        azure_service=azure_service,
        formatter=formatter
    )
    
    print("✅ Services initialized successfully")
    
    # Test a product search query
    test_query = "I want black bags"
    print(f"🔍 Testing query: '{test_query}'")
    
    result, metrics = workflow.process_message(test_query)
    
    print(f"📋 Result: {result[:100]}...")
    print(f"📊 Metrics: {metrics}")
    
    if metrics and 'tokens' in metrics:
        print("✅ Metrics captured successfully!")
        print(f"   Tokens: {metrics['tokens']}")
        print(f"   Latency: {metrics['latency']:.2f}s")
        print(f"   Cost: ${metrics['cost']:.4f}")
    else:
        print("❌ No metrics captured")
        
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
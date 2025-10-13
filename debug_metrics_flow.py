"""
Debug script to trace metrics flow step by step
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

print("🔍 Debug: Tracing metrics flow step by step...")

try:
    # Initialize services
    azure_service = AzureService()
    print(f"1️⃣ Azure service initialized, last_metrics: {getattr(azure_service, 'last_metrics', 'None')}")
    
    vector_service = VectorService(azure_service.embeddings)
    preference_service = EnhancedPreferenceService(azure_service)
    data_loader = DataLoader()
    search_service = SearchService(vector_service, data_loader)
    formatter = ProductFormatter(data_loader)
    
    # Create workflow
    workflow = ConversationWorkflow(
        preference_service=preference_service,
        search_service=search_service,
        azure_service=azure_service,
        formatter=formatter
    )
    
    print("2️⃣ Services initialized successfully")
    
    # Test a product search query step by step
    test_query = "I want black bags"
    print(f"3️⃣ Testing query: '{test_query}'")
    
    # Check azure service before call
    print(f"4️⃣ Before process_message - azure_service.last_metrics: {getattr(azure_service, 'last_metrics', 'None')}")
    
    result, metrics = workflow.process_message(test_query)
    
    # Check azure service after call
    print(f"5️⃣ After process_message - azure_service.last_metrics: {getattr(azure_service, 'last_metrics', 'None')}")
    
    print(f"6️⃣ Workflow returned metrics: {metrics}")
    print(f"7️⃣ Result length: {len(result) if result else 0}")
    
    if metrics:
        print("✅ METRICS CAPTURED!")
        for key, value in metrics.items():
            print(f"   {key}: {value}")
    else:
        print("❌ NO METRICS RETURNED")
        
    # Check if the issue is in the UI display logic
    if metrics and 'tokens' in metrics:
        metrics_info = f"⚡ Tokens: {metrics['tokens']} | ⏱️ {metrics['latency']:.2f}s"
        if 'cost' in metrics:
            metrics_info += f" | 💰 ${metrics['cost']:.4f}"
        print(f"8️⃣ UI format would be: {metrics_info}")
    else:
        print("8️⃣ UI would show: No metrics available")
        
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
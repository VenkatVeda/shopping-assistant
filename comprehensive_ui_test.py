"""
Comprehensive test to simulate the exact UI workflow and find where metrics are lost
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
from services.session_manager import SessionManager
from ui.gradio_interface import GradioInterface

print("🔍 Comprehensive Test: Simulating Exact UI Workflow")
print("=" * 60)

try:
    # Initialize all services exactly like main.py
    print("1️⃣ Initializing services...")
    azure_service = AzureService()
    vector_service = VectorService(azure_service.embeddings)
    preference_service = EnhancedPreferenceService(azure_service)
    data_loader = DataLoader()
    search_service = SearchService(vector_service, data_loader)
    formatter = ProductFormatter(data_loader)
    
    # Create session manager
    session_manager = SessionManager(azure_service, preference_service, search_service, formatter)
    
    # Create UI interface
    ui = GradioInterface(session_manager, enable_parallel=False)
    
    print("✅ All services initialized")
    
    # Test the exact UI workflow
    test_query = "I want black bags"
    print(f"\n2️⃣ Testing UI workflow with query: '{test_query}'")
    
    # Simulate the exact UI call
    print("\n3️⃣ Calling ui.chat_interface() (same as in UI)...")
    
    # This is the exact same call that Gradio makes
    chat_history, session_id = ui.chat_interface(test_query, None)
    
    print(f"\n4️⃣ Results:")
    print(f"   Session ID: {session_id}")
    print(f"   Chat history length: {len(chat_history)}")
    
    if chat_history:
        last_user_msg = chat_history[-1][0] if chat_history[-1] else "No user message"
        last_bot_msg = chat_history[-1][1] if chat_history[-1] else "No bot message"
        
        print(f"   User message: {last_user_msg[:100]}...")
        print(f"   Bot response length: {len(last_bot_msg)}")
        
        # Check if metrics are in the bot response
        if "⚡ Tokens:" in last_bot_msg:
            print("✅ METRICS FOUND IN UI RESPONSE!")
            # Extract metrics from response
            lines = last_bot_msg.split('\n')
            for line in lines:
                if "⚡ Tokens:" in line:
                    print(f"   Metrics line: {line.strip()}")
        else:
            print("❌ NO METRICS FOUND IN UI RESPONSE")
            print(f"   Response end: ...{last_bot_msg[-200:]}")
    
    print(f"\n5️⃣ Final azure_service.last_metrics: {getattr(azure_service, 'last_metrics', 'None')}")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
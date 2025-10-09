#!/usr/bin/env python3

"""
Test script for Show More functionality
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_show_more_ui():
    """Test the show more functionality"""
    try:
        # Import required modules
        from ui.gradio_interface import GradioInterface
        from services.session_manager import SessionManager
        from services.azure_service import AzureService
        from services.search_service import SearchService
        from services.vector_service import VectorService
        from ui.formatters import ProductFormatter
        from utils.data_loader import DataLoader
        
        print("🔄 Setting up services...")
        
        # Initialize services
        azure_service = AzureService()
        data_loader = DataLoader()  # Data is loaded automatically in __init__
        
        vector_service = VectorService(data_loader)
        search_service = SearchService(vector_service, data_loader)
        formatter = ProductFormatter(data_loader)
        
        # Create session manager
        session_manager = SessionManager(
            azure_service=azure_service,
            search_service=search_service,
            formatter=formatter
        )
        
        # Create Gradio interface
        interface = GradioInterface(session_manager)
        
        print("✅ Successfully created GradioInterface with Show More functionality")
        print("🚀 Launching interface...")
        
        # Build and launch the UI
        demo = interface.build_ui()
        demo.launch(
            server_name="127.0.0.1",
            server_port=7860,
            share=False,
            debug=True
        )
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_show_more_ui()
#!/usr/bin/env python3

"""
Launch Shopping Assistant with Show More functionality
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def main():
    """Launch the shopping assistant with pagination"""
    try:
        print("🚀 Starting Shopping Assistant with Pagination...")
        
        # Import required modules
        from services.azure_service import AzureService
        from services.vector_service import VectorService
        from services.search_service import SearchService
        from services.session_manager import SessionManager
        from utils.data_loader import DataLoader
        from ui.formatters import ProductFormatter
        from ui.gradio_interface import GradioInterface
        
        print("📦 Loading services...")
        
        # Initialize services
        azure_service = AzureService()
        data_loader = DataLoader()
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
        
        print("✅ Services loaded successfully!")
        print("🌐 Launching web interface...")
        
        # Build and launch the UI
        demo = interface.build_ui()
        demo.launch(
            server_name="127.0.0.1",
            server_port=7860,
            share=False,
            debug=True
        )
        
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
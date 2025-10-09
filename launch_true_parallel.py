#!/usr/bin/env python3
"""
Launch Shopping Assistant with True Parallel Processing

This launcher uses the TrueParallelInterface which guarantees no FIFO queuing
and enables truly concurrent processing for multiple users.
"""

import sys
import os

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.azure_service import AzureService
from services.vector_service import VectorService
from services.enhanced_preference_service import EnhancedPreferenceService as PreferenceService
from services.search_service import SearchService
from services.session_manager import SessionManager
from utils.data_loader import DataLoader
from ui.formatters import ProductFormatter
from workflows.conversation_flow import ConversationWorkflow
from true_parallel_fix import TrueParallelInterface


class TrueParallelShoppingApp:
    """Shopping Assistant with guaranteed parallel processing"""
    
    def __init__(self):
        print("🚀 Initializing True Parallel Shopping Assistant...")
        
        # Initialize core services
        self.azure_service = AzureService()
        print(f"✅ Azure OpenAI: {'Connected' if self.azure_service.is_available() else 'Failed'}")
        
        # Vector service for embeddings and search
        self.vector_service = VectorService(self.azure_service.embeddings)
        print(f"✅ Vector Service: {'Ready' if self.vector_service.is_available() else 'Failed'}")
        
        # Data loader for product information
        self.data_loader = DataLoader()
        print(f"✅ Product Data: {len(self.data_loader.url_to_image)} products loaded")
        
        # Search service combining vector search with product data
        self.search_service = SearchService(self.vector_service, self.data_loader)
        print("✅ Search Service: Ready")
        
        # Product formatter for displaying search results
        self.formatter = ProductFormatter(self.data_loader)
        print("✅ Product Formatter: Ready")
        
        # Session manager for user isolation
        self.session_manager = SessionManager(
            self.azure_service, 
            self.search_service, 
            self.formatter
        )
        print("✅ Session Manager: Ready")
        
        # True parallel interface
        self.interface = TrueParallelInterface(self.session_manager)
        print("✅ True Parallel Interface: Ready")
        
        print("\n🎯 All services initialized successfully!")
        print("📊 Parallel Processing Features:")
        print("   • Dedicated ThreadPoolExecutor with 50 workers")
        print("   • True concurrent processing (no FIFO)")
        print("   • Real-time processing statistics")
        print("   • Session isolation maintained")
        print("   • No request queuing\n")
    
    def launch(self, **kwargs):
        """Launch the application with true parallel processing"""
        try:
            self.interface.launch(**kwargs)
        except KeyboardInterrupt:
            print("\n👋 Shutting down gracefully...")
        except Exception as e:
            print(f"❌ Error: {e}")
            raise


def main():
    """Main entry point"""
    # Parse command line arguments
    mode = "dev"
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
    
    try:
        app = TrueParallelShoppingApp()
        
        if mode == "dev":
            print("🔧 Launching in development mode...")
            app.launch(debug=True, share=False)
        elif mode == "prod":
            print("🌐 Launching in production mode...")
            app.launch(debug=False, share=True)
        elif mode == "local":
            print("🏠 Launching for local testing...")
            app.launch(server_name="127.0.0.1", server_port=7860, share=False)
        elif mode == "demo":
            print("🎪 Launching demo mode...")
            app.launch(debug=True, share=False, server_port=7861)
        else:
            print(f"Unknown mode: {mode}")
            print("Available modes: dev, prod, local, demo")
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ Failed to start application: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
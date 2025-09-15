# main.py
"""
Smart Shopping Assistant - Main Application Entry Point

This is the main entry point for the modularized shopping assistant application.
It initializes all services and components, then launches the Gradio interface.
"""

from services.azure_service import AzureService
from services.vector_service import VectorService
from services.preference_service import PreferenceService
from services.search_service import SearchService
from utils.data_loader import DataLoader
from ui.formatters import ProductFormatter
from ui.gradio_interface import GradioInterface
from workflows.conversation_flow import ConversationWorkflow


class ShoppingAssistantApp:
    """Main application class that orchestrates all components"""
    
    def __init__(self):
        self.azure_service = None
        self.vector_service = None
        self.preference_service = None
        self.search_service = None
        self.data_loader = None
        self.formatter = None
        self.workflow = None
        self.ui = None
        
        self._initialize_services()
    
    def _initialize_services(self):
        """Initialize all services and components"""
        print("🚀 Initializing Smart Shopping Assistant...")
        
        # Initialize Azure service first
        print("   Initializing Azure OpenAI service...")
        self.azure_service = AzureService()
        
        # Initialize vector service
        print("   Initializing vector database...")
        self.vector_service = VectorService(self.azure_service.embeddings)
        
        # Initialize data loader
        print("   Loading product data...")
        self.data_loader = DataLoader()
        
        # Initialize preference service
        print("   Setting up preference management...")
        self.preference_service = PreferenceService(self.azure_service)
        
        # Initialize search service
        print("   Setting up search functionality...")
        self.search_service = SearchService(self.vector_service, self.data_loader)
        
        # Initialize formatter
        print("   Setting up product formatters...")
        self.formatter = ProductFormatter(self.data_loader)
        
        # Initialize conversation workflow
        print("   Building conversation workflow...")
        self.workflow = ConversationWorkflow(
            self.preference_service,
            self.search_service,
            self.azure_service,
            self.formatter
        )
        
        # Initialize UI
        print("   Building user interface...")
        self.ui = GradioInterface(
            self.workflow,
            self.preference_service,
            self.formatter
        )
        
        self._print_system_status()
    
    def _print_system_status(self):
        """Print the status of all system components"""
        print("\n📊 System Status:")
        print(f"   - Azure OpenAI: {'✅ Connected' if self.azure_service.is_available() else '❌ Not Available'}")
        print(f"   - Vector Database: {'✅ Loaded' if self.vector_service.is_available() else '❌ Not Available'}")
        print(f"   - Product Data: {'✅ Loaded' if self.data_loader.url_to_image else '❌ Not Available'}")
        print(f"   - Preference Service: {'✅ Ready' if self.preference_service else '❌ Not Ready'}")
        print(f"   - Search Service: {'✅ Ready' if self.search_service else '❌ Not Ready'}")
        print(f"   - UI Interface: {'✅ Ready' if self.ui else '❌ Not Ready'}")
    
    def launch(self, **kwargs):
        """Launch the Gradio interface"""
        if not self.ui:
            raise RuntimeError("UI not initialized. Call _initialize_services() first.")
        
        print("\n🌐 Launching web interface...")
        
        # Default launch settings
        launch_settings = {
            "share": False,
            "debug": False,
            "server_name": "0.0.0.0",
            "server_port": 7860
        }
        
        # Override with any provided kwargs
        launch_settings.update(kwargs)
        
        demo = self.ui.build_ui()
        demo.launch(**launch_settings)


def main():
    """Main entry point for the application"""
    try:
        app = ShoppingAssistantApp()
        app.launch()
    except KeyboardInterrupt:
        print("\n👋 Shutting down gracefully...")
    except Exception as e:
        print(f"❌ Error starting application: {e}")
        raise


if __name__ == "__main__":
    main()


# Alternative entry points for different use cases

def launch_development():
    """Launch in development mode with debug enabled"""
    app = ShoppingAssistantApp()
    app.launch(debug=True, share=False)


def launch_production():
    """Launch in production mode"""
    app = ShoppingAssistantApp()
    app.launch(debug=False, share=True)


def launch_local():
    """Launch for local testing only"""
    app = ShoppingAssistantApp()
    app.launch(server_name="127.0.0.1", server_port=7860, share=False)


# For testing individual components
def test_services():
    """Test individual services without launching UI"""
    print("🧪 Testing services...")
    
    # Test Azure service
    azure_service = AzureService()
    print(f"Azure service available: {azure_service.is_available()}")
    
    # Test vector service
    vector_service = VectorService(azure_service.embeddings)
    print(f"Vector service available: {vector_service.is_available()}")
    
    # Test data loader
    data_loader = DataLoader()
    print(f"Product data loaded: {len(data_loader.url_to_image)} products")
    
    # Test preference service
    preference_service = PreferenceService(azure_service)
    test_input = "I want black leather bags under $100"
    updated_prefs = preference_service.update_preferences(test_input)
    print(f"Preference update test: {preference_service.get_summary()}")
    
    print("✅ All services tested successfully!")


if __name__ == "__main__":
    import sys
    
    # Handle different launch modes based on command line arguments
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
        if mode == "dev":
            launch_development()
        elif mode == "prod":
            launch_production()
        elif mode == "local":
            launch_local()
        elif mode == "test":
            test_services()
        else:
            print(f"Unknown mode: {mode}")
            print("Available modes: dev, prod, local, test")
            sys.exit(1)
    else:
        main()
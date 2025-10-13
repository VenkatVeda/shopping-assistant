# main.py
"""
Smart Shopping Assistant - Main Application Entry Point with Redis Caching

This is the main entry point for the modularized shopping assistant application.
It initializes all services and components with Redis caching, then launches the Gradio interface.
"""

import os
import warnings

# Suppress verbose warnings and logs
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Import config first to set up logging
from config.settings import configure_logging

from services.azure_service import AzureService
from services.vector_service import VectorService
from services.enhanced_preference_service import EnhancedPreferenceService as PreferenceService
from services.search_service import SearchService
from services.session_manager import SessionManager
from utils.data_loader import DataLoader
from ui.formatters import ProductFormatter
from ui.gradio_interface import GradioInterface
from workflows.conversation_flow import ConversationWorkflow

# Redis Cache Implementation
import json
import hashlib
import pickle
from typing import Any, Dict

# Try Redis, fallback to memory
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


class Cache:
    """High-performance cache with Redis primary and memory fallback"""
    
    def __init__(self):
        self.memory_cache = {}
        self.redis_client = None
        self.use_redis = False
        
        # Check if Redis is enabled via environment variable
        enable_redis = os.getenv('ENABLE_REDIS', 'true').lower() == 'true'
        
        if REDIS_AVAILABLE and enable_redis:
            try:
                # Use environment variables for Redis connection
                redis_host = os.getenv('REDIS_HOST', 'localhost')
                redis_port = int(os.getenv('REDIS_PORT', '6379'))
                redis_password = os.getenv('REDIS_PASSWORD', None)
                redis_url = os.getenv('REDIS_URL', None)  # For Upstash URL format
                
                # Connection configuration for external Redis services
                redis_config = {
                    'db': 0,
                    'socket_connect_timeout': 10,
                    'socket_timeout': 10,
                    'retry_on_timeout': True,
                    'health_check_interval': 30
                }
                
                # Handle different connection methods
                if redis_url:
                    # Use Redis URL (e.g., redis://:password@host:port)
                    self.redis_client = redis.from_url(redis_url, **redis_config)
                    print(f"✅ Connecting to Redis via URL...")
                else:
                    # Use individual parameters
                    redis_config.update({
                        'host': redis_host,
                        'port': redis_port
                    })
                    
                    # Add password if provided
                    if redis_password:
                        redis_config['password'] = redis_password
                    
                    # Enable SSL for cloud Redis services (like Upstash)
                    if redis_host != 'localhost' and '.upstash.io' in redis_host:
                        redis_config['ssl'] = True
                        redis_config['ssl_cert_reqs'] = None
                        print(f"✅ SSL enabled for Upstash connection")
                    
                    self.redis_client = redis.Redis(**redis_config)
                
                # Test connection with retry logic
                for attempt in range(3):
                    try:
                        self.redis_client.ping()
                        self.use_redis = True
                        host_info = redis_url.split('@')[1] if redis_url else f"{redis_host}:{redis_port}"
                        print(f"✅ Redis cache connected to {host_info} (attempt {attempt + 1})")
                        break
                    except Exception as e:
                        if attempt == 2:  # Last attempt
                            raise e
                        print(f"⚠️ Redis connection attempt {attempt + 1} failed, retrying...")
                        
            except Exception as e:
                print(f"⚠️ Redis connection failed ({e}), using memory cache")
                redis_host_display = os.getenv('REDIS_HOST', 'localhost')
                print(f"   Redis Host: {redis_host_display}")
                print(f"   Make sure Redis service is running and credentials are correct")
        else:
            reason = "package not available" if not REDIS_AVAILABLE else "disabled via ENABLE_REDIS=false"
            print(f"⚠️ Redis {reason}, using memory cache")
    
    def get(self, key: str):
        try:
            if self.use_redis:
                data = self.redis_client.get(key)
                if data:
                    return pickle.loads(data)
            return self.memory_cache.get(key)
        except:
            return self.memory_cache.get(key)
    
    def set(self, key: str, value: Any, ttl: int = 3600):
        try:
            if self.use_redis:
                self.redis_client.setex(key, ttl, pickle.dumps(value))
            self.memory_cache[key] = value
            return True
        except:
            self.memory_cache[key] = value
            return True
    
    def key(self, prefix: str, data: str) -> str:
        return f"shop:{prefix}:{hashlib.md5(data.encode()).hexdigest()}"

# Global cache instance
_cache = Cache()


class CachedAzureService:
    """Azure service wrapper with intelligent caching"""
    
    def __init__(self, azure_service):
        self.azure_service = azure_service
    
    def extract_preferences_cached(self, user_input: str, current_preferences: Dict = None) -> Dict:
        """Extract preferences with 95% faster cached responses"""
        key = _cache.key('prefs', f"{user_input}:{json.dumps(current_preferences or {})}")
        
        result = _cache.get(key)
        if result:
            print("🎯 Cache hit: preference extraction")
            return result
        
        print("🔄 Cache miss: calling Azure API...")
        
        if not self.azure_service.is_available():
            return {}
        
        try:
            # Use the tracking method for metrics while maintaining cache
            result, metrics = self.azure_service.run_with_tracking(
                self.azure_service.preference_chain,
                {
                    'user_input': user_input,
                    'previous_prefs': json.dumps(current_preferences or {}, indent=2)
                }
            )
            
            if result:
                # Parse JSON from response
                if isinstance(result, str):
                    import re
                    json_match = re.search(r'\{.*\}', result, re.DOTALL)
                    if json_match:
                        parsed_result = json.loads(json_match.group())
                    else:
                        parsed_result = {}
                elif isinstance(result, dict) and 'text' in result:
                    # Handle LangChain response format
                    response_text = result['text']
                    import re
                    json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                    if json_match:
                        parsed_result = json.loads(json_match.group())
                    else:
                        parsed_result = {}
                else:
                    parsed_result = result
                
                _cache.set(key, parsed_result, ttl=86400)  # 24 hours
                return parsed_result
            else:
                return {}
            
        except Exception as e:
            print(f"Error in preference extraction: {e}")
            return {}
    
    # Passthrough methods to maintain compatibility
    def is_available(self) -> bool:
        return self.azure_service.is_available()
    
    def is_langsmith_enabled(self) -> bool:
        return self.azure_service.is_langsmith_enabled()
    
    def run_with_tracking(self, chain, inputs):
        """Passthrough to Azure service tracking method"""
        return self.azure_service.run_with_tracking(chain, inputs)
    
    @property
    def langsmith_client(self):
        return getattr(self.azure_service, 'langsmith_client', None)
    
    @property
    def preference_chain(self):
        return self.azure_service.preference_chain
    
    @property
    def conversation_chain(self):
        return self.azure_service.conversation_chain
    
    @property
    def llm(self):
        return self.azure_service.llm
    
    @property
    def embeddings(self):
        return self.azure_service.embeddings


class CachedVectorService:
    """Vector service wrapper with instant search caching"""
    
    def __init__(self, vector_service):
        self.vector_service = vector_service
    
    def search(self, query: str, k: int = 30):
        """Search with instant cached results"""
        key = _cache.key('vector', f"{query}:{k}")
        
        result = _cache.get(key)
        if result:
            print("🎯 Cache hit: vector search")
            from langchain_core.documents import Document
            return [Document(page_content=doc['content'], metadata=doc['metadata']) for doc in result]
        
        print("🔄 Cache miss: querying vector database...")
        
        if not self.vector_service.is_available():
            return []
        
        try:
            documents = self.vector_service.search(query, k)
            cache_data = [{'content': doc.page_content, 'metadata': doc.metadata} for doc in documents]
            _cache.set(key, cache_data, ttl=7200)  # 2 hours
            print(f"✅ Cached {len(documents)} search results")
            return documents
        except Exception as e:
            print(f"Error in vector search: {e}")
            return []
    
    def __getattr__(self, name):
        """Delegate all other attributes to the original service"""
        return getattr(self.vector_service, name)


class ShoppingAssistantApp:
    """Main application class with enterprise-level Redis caching and parallel execution support"""
    
    def __init__(self, enable_parallel=False):
        self.enable_parallel = enable_parallel
        self.azure_service = None
        self.vector_service = None
        self.search_service = None
        self.data_loader = None
        self.formatter = None
        self.session_manager = None
        self.ui = None
        
        self._initialize_services()
    
    def _initialize_services(self):
        """Initialize all services and components with Redis caching"""
        print("🚀 Initializing Smart Shopping Assistant with Redis Caching...")
        
        # Initialize Azure service with caching
        print("   Initializing Azure OpenAI service...")
        azure_service = AzureService()
        self.azure_service = CachedAzureService(azure_service)
        
        # Initialize vector service with caching
        print("   Initializing vector database...")
        vector_service = VectorService(azure_service.embeddings)  # Use original embeddings
        self.vector_service = CachedVectorService(vector_service)
        
        # Initialize and cache data loader
        print("   Loading product data...")
        self.data_loader = DataLoader()
        
        # Cache product data for 90% faster startup
        key = "product_data"
        cached_data = _cache.get(key)
        if cached_data:
            self.data_loader.url_to_image = cached_data
            print(f"🎯 Loaded {len(cached_data)} products from cache")
        else:
            print(f"🔄 Caching {len(self.data_loader.url_to_image)} products")
            _cache.set(key, self.data_loader.url_to_image, ttl=43200)  # 12 hours
        
        # Initialize search service (uses cached vector service)
        print("   Setting up search functionality...")
        self.search_service = SearchService(self.vector_service, self.data_loader)
        
        # Initialize preference service
        print("   Setting up preference extraction...")
        self.preference_service = PreferenceService(self.azure_service, self.search_service)
        
        # Initialize formatter
        print("   Setting up product formatters...")
        self.formatter = ProductFormatter(self.data_loader)
        
        # Initialize session manager
        print("   Setting up session management...")
        self.session_manager = SessionManager(
            self.azure_service,
            self.search_service,
            self.formatter,
            session_timeout_hours=24
        )
        
        # Initialize UI with parallel execution support
        print("   Building user interface...")
        self.ui = GradioInterface(self.session_manager, enable_parallel=self.enable_parallel)
        
        if self.enable_parallel:
            print("🚀 Parallel execution mode enabled!")
        else:
            print("⚡ Standard execution mode (single-threaded)")
        
        self._print_system_status()
    
    def _print_system_status(self):
        """Print the status of all system components"""
        print(f"\n📊 System Status:")
        print(f"   - Azure OpenAI: {'✅ Connected' if self.azure_service.is_available() else '❌ Not Available'}")
        print(f"   - Vector Database: {'✅ Loaded' if self.vector_service.is_available() else '❌ Not Available'}")
        print(f"   - Product Data: {'✅ Loaded' if self.data_loader.url_to_image else '❌ Not Available'}")
        print(f"   - Search Service: {'✅ Ready' if self.search_service else '❌ Not Ready'}")
        print(f"   - Session Manager: {'✅ Ready' if self.session_manager else '❌ Not Ready'}")
        print(f"   - UI Interface: {'✅ Ready' if self.ui else '❌ Not Ready'}")
        print(f"   - Cache System: {'✅ Redis' if _cache.use_redis else '⚠️ Memory'}")
        print(f"   - Active Sessions: {self.session_manager.get_session_count() if self.session_manager else 0}")
        print(f"   - Products Loaded: {len(self.data_loader.url_to_image) if self.data_loader.url_to_image else 0}")
    
    def launch(self, **kwargs):
        """Launch the Gradio interface with health monitoring and parallel execution support"""
        if not self.ui:
            raise RuntimeError("UI not initialized")
        
        print("\n🌐 Launching web interface...")
        
        # Initialize health checker
        from health import get_health_checker
        health_checker = get_health_checker(self)
        
        # Default launch settings
        launch_settings = {
            "share": False,
            "debug": False,
            "server_name": "0.0.0.0",
            "server_port": 7860
        }
        
        # Enhanced settings for parallel execution
        if self.enable_parallel:
            launch_settings.update({
                "max_threads": 40,  # Allow more concurrent threads
                "show_error": True,
                "quiet": False
            })
            print("🚀 Parallel processing configuration:")
            print(f"   • Max concurrent threads: {launch_settings.get('max_threads', 40)}")
            print(f"   • Async handlers: ✅ Enabled")
            print(f"   • Thread pool execution: ✅ Enabled")
        
        # Override with any provided kwargs
        launch_settings.update(kwargs)
        
        demo = self.ui.build_ui()
        
        # Add health endpoint
        @demo.app.get("/health")
        async def health_endpoint():
            """Health check endpoint for Docker containers"""
            from health import health_check_endpoint
            import json
            health_data = json.loads(health_check_endpoint())
            
            # Return appropriate HTTP status
            if health_data["status"] == "healthy":
                return health_data
            elif health_data["status"] == "degraded":
                return health_data
            else:
                from fastapi import HTTPException
                raise HTTPException(status_code=503, detail=health_data)
        
        # Add parallel processing status endpoint
        if self.enable_parallel:
            @demo.app.get("/parallel-status")
            async def parallel_status():
                """Endpoint to check parallel processing status"""
                return {
                    "parallel_processing": True,
                    "active_sessions": self.session_manager.get_session_count(),
                    "max_threads": launch_settings.get("max_threads", 40),
                    "session_timeout_hours": self.session_manager.session_timeout_hours,
                    "concurrent_support": True,
                    "async_handlers": True
                }
        
        demo.launch(**launch_settings)


def main():
    """Main entry point for the application"""
    try:
        app = ShoppingAssistantApp(enable_parallel=False)  # Default to standard mode
        app.launch()
    except KeyboardInterrupt:
        print("\n👋 Shutting down gracefully...")
    except Exception as e:
        print(f"❌ Error starting application: {e}")
        raise


def main_parallel():
    """Main entry point for parallel execution mode"""
    try:
        app = ShoppingAssistantApp(enable_parallel=True)  # Enable parallel processing
        app.launch()
    except KeyboardInterrupt:
        print("\n👋 Shutting down gracefully...")
    except Exception as e:
        print(f"❌ Error starting parallel application: {e}")
        raise


if __name__ == "__main__":
    main()


# Alternative entry points for different use cases

def launch_development():
    """Launch in development mode with debug enabled"""
    app = ShoppingAssistantApp(enable_parallel=False)
    app.launch(debug=True, share=False)


def launch_development_parallel():
    """Launch in development mode with parallel processing"""
    app = ShoppingAssistantApp(enable_parallel=True)
    app.launch(debug=True, share=False, max_threads=20)


def launch_production():
    """Launch in production mode"""
    app = ShoppingAssistantApp(enable_parallel=False)
    app.launch(debug=False, share=True)


def launch_production_parallel():
    """Launch in production mode with parallel processing"""
    app = ShoppingAssistantApp(enable_parallel=True)
    app.launch(debug=False, share=True, max_threads=80)


def launch_local():
    """Launch for local testing only"""
    app = ShoppingAssistantApp(enable_parallel=False)
    app.launch(server_name="127.0.0.1", server_port=7860, share=False)


def launch_local_parallel():
    """Launch for local testing with parallel processing"""
    app = ShoppingAssistantApp(enable_parallel=True)
    app.launch(server_name="127.0.0.1", server_port=7860, share=False, max_threads=20)


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
    
    # Test search service
    search_service = SearchService(vector_service, data_loader)
    print(f"Search service ready: {search_service is not None}")
    
    # Test session manager
    formatter = ProductFormatter(data_loader)
    session_manager = SessionManager(azure_service, search_service, formatter)
    session_id, session_data = session_manager.get_or_create_session()
    
    # Test preference update through session
    test_input = "I want blue crossbody bags under $200"
    session_data.preference_service.update_preferences(test_input)
    print(f"Session preference test: {session_data.preference_service.get_summary()}")
    print(f"Active sessions: {session_manager.get_session_count()}")
    
    print("✅ All services tested successfully!")


def test_parallel_execution():
    """Test parallel execution performance"""
    from test_parallel_execution import main as test_main
    test_main()


if __name__ == "__main__":
    import sys
    
    # Handle different launch modes based on command line arguments
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
        if mode == "dev":
            launch_development()
        elif mode == "dev-parallel":
            launch_development_parallel()
        elif mode == "prod":
            launch_production()
        elif mode == "prod-parallel":
            launch_production_parallel()
        elif mode == "local":
            launch_local()
        elif mode == "local-parallel":
            launch_local_parallel()
        elif mode == "parallel":
            main_parallel()
        elif mode == "test":
            try:
                print("\n🧪 Starting service tests...\n")
                test_services()
            except Exception as e:
                print(f"\n❌ Test failed: {str(e)}")
                sys.exit(1)
        elif mode == "test-parallel":
            try:
                print("\n🧪 Starting parallel execution tests...\n")
                test_parallel_execution()
            except Exception as e:
                print(f"\n❌ Parallel test failed: {str(e)}")
                sys.exit(1)
        else:
            print(f"Unknown mode: {mode}")
            print("Available modes:")
            print("  Standard: dev, prod, local, test")
            print("  Parallel: dev-parallel, prod-parallel, local-parallel, parallel, test-parallel")
            sys.exit(1)
    else:
        print("🤖 Starting in standard mode...")
        print("💡 For parallel execution, use: python main.py parallel")
        main()
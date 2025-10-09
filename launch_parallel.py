# launch_parallel.py
"""
Launch Shopping Assistant with Parallel Execution Support
Enables concurrent processing for multiple users without FIFO blocking
"""

import asyncio
from main import ShoppingAssistantApp
from parallel_execution_fix import ParallelGradioInterface
import gradio as gr

class ParallelShoppingAssistantApp(ShoppingAssistantApp):
    """Enhanced Shopping Assistant with parallel execution capabilities"""
    
    def __init__(self):
        super().__init__()
        # Replace the standard UI with the parallel version
        self.ui = ParallelGradioInterface(self.session_manager)
        print("🚀 Parallel execution mode enabled")
    
    def launch(self, **kwargs):
        """Launch with optimized settings for parallel processing"""
        if not self.ui:
            raise RuntimeError("UI not initialized")
        
        print("\n🌐 Launching parallel-enabled web interface...")
        print("👥 Multiple users can now chat simultaneously without waiting!")
        
        # Initialize health checker
        from health import get_health_checker
        health_checker = get_health_checker(self)
        
        # Optimized launch settings for parallel processing
        launch_settings = {
            "share": False,
            "debug": False,
            "server_name": "0.0.0.0",
            "server_port": 7860,
            "max_threads": 40,  # Allow more concurrent threads
            "show_error": True,
            "quiet": False,
            "favicon_path": None,
            "ssl_keyfile": None,
            "ssl_certfile": None,
            "ssl_keyfile_password": None,
        }
        
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
            
            if health_data["status"] == "healthy":
                return health_data
            elif health_data["status"] == "degraded":
                return health_data
            else:
                from fastapi import HTTPException
                raise HTTPException(status_code=503, detail=health_data)
        
        # Add parallel processing status endpoint
        @demo.app.get("/parallel-status")
        async def parallel_status():
            """Endpoint to check parallel processing status"""
            return {
                "parallel_processing": True,
                "active_sessions": self.session_manager.get_session_count(),
                "max_threads": launch_settings.get("max_threads", 40),
                "session_timeout_hours": self.session_manager.session_timeout_hours,
                "concurrent_support": True
            }
        
        print(f"\n🔧 Parallel Configuration:")
        print(f"   • Max concurrent threads: {launch_settings.get('max_threads', 40)}")
        print(f"   • Session isolation: ✅ Enabled")
        print(f"   • Async processing: ✅ Enabled")
        print(f"   • Concurrent users: ✅ Supported")
        print(f"   • Active sessions: {self.session_manager.get_session_count()}")
        
        # Launch with enhanced concurrency
        demo.launch(**launch_settings)


def main_parallel():
    """Main entry point for parallel execution mode"""
    try:
        # Use uvloop for better async performance if available
        try:
            import uvloop
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
            print("🏃‍♂️ Using uvloop for enhanced async performance")
        except ImportError:
            print("⚠️ uvloop not available, using default asyncio")
        
        app = ParallelShoppingAssistantApp()
        app.launch()
    except KeyboardInterrupt:
        print("\n👋 Shutting down gracefully...")
    except Exception as e:
        print(f"❌ Error starting parallel application: {e}")
        raise


def launch_parallel_development():
    """Launch parallel mode in development"""
    app = ParallelShoppingAssistantApp()
    app.launch(debug=True, share=False, max_threads=20)


def launch_parallel_production():
    """Launch parallel mode in production"""
    app = ParallelShoppingAssistantApp()
    app.launch(debug=False, share=True, max_threads=80)


def test_parallel_load():
    """Test parallel processing with simulated concurrent users"""
    import time
    import threading
    import requests
    from concurrent.futures import ThreadPoolExecutor
    
    print("🧪 Testing parallel execution with simulated users...")
    
    # Start the app in a separate thread
    app = ParallelShoppingAssistantApp()
    
    def start_app():
        app.launch(debug=False, share=False, server_port=7861, max_threads=10)
    
    app_thread = threading.Thread(target=start_app, daemon=True)
    app_thread.start()
    
    # Wait for app to start
    time.sleep(5)
    
    def simulate_user(user_id):
        """Simulate a user sending multiple messages"""
        start_time = time.time()
        print(f"👤 User {user_id} starting at {time.strftime('%H:%M:%S')}")
        
        # Create session and send messages
        session_data = app.session_manager.get_or_create_session()
        session_id = session_data[0]
        
        messages = [
            f"Hello from user {user_id}",
            f"I'm user {user_id} looking for bags",
            f"User {user_id} wants leather bags",
            f"Show me options for user {user_id}"
        ]
        
        for i, message in enumerate(messages):
            try:
                # Simulate processing through the session
                response = session_data[1].workflow.process_message(message, session_id)
                processing_time = time.time() - start_time
                print(f"✅ User {user_id} message {i+1} processed in {processing_time:.2f}s")
                time.sleep(0.5)  # Small delay between messages
            except Exception as e:
                print(f"❌ User {user_id} error: {e}")
        
        total_time = time.time() - start_time
        print(f"🏁 User {user_id} completed in {total_time:.2f}s")
        return user_id, total_time
    
    # Test with 5 concurrent users
    start_time = time.time()
    print(f"\n🏃‍♂️ Starting 5 concurrent users at {time.strftime('%H:%M:%S')}")
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(simulate_user, i+1) for i in range(5)]
        results = [future.result() for future in futures]
    
    total_test_time = time.time() - start_time
    print(f"\n📊 Test Results:")
    print(f"   • Total test time: {total_test_time:.2f}s")
    print(f"   • Average user time: {sum(r[1] for r in results) / len(results):.2f}s")
    print(f"   • Active sessions: {app.session_manager.get_session_count()}")
    print(f"   • Concurrent processing: {'✅ SUCCESS' if total_test_time < 15 else '❌ SLOW'}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
        if mode == "dev":
            launch_parallel_development()
        elif mode == "prod":
            launch_parallel_production()
        elif mode == "test":
            test_parallel_load()
        else:
            print(f"Unknown mode: {mode}")
            print("Available modes: dev, prod, test")
            sys.exit(1)
    else:
        main_parallel()
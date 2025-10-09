"""
True Parallel Execution Fix for Shopping Assistant

This implementation ensures that multiple users can truly process requests in parallel
without any FIFO queuing behavior by using concurrent.futures ThreadPoolExecutor
and proper async handling.
"""

import gradio as gr
import asyncio
import time
import base64
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from services.session_manager import SessionManager

class TrueParallelInterface:
    """Gradio interface with guaranteed parallel execution for multiple users"""
    
    def __init__(self, session_manager):
        self.session_manager = session_manager
        # Create dedicated thread pool for processing requests
        self.executor = ThreadPoolExecutor(max_workers=50, thread_name_prefix="shopping_assistant")
        self.processing_stats = {
            'total_requests': 0,
            'concurrent_requests': 0,
            'max_concurrent': 0
        }
        self.stats_lock = threading.Lock()
    
    def get_base64_image(self, image_path: str) -> str:
        """Convert image to base64 for embedding in HTML"""
        try:
            with open(image_path, "rb") as img_file:
                return base64.b64encode(img_file.read()).decode("utf-8")
        except FileNotFoundError:
            print(f"Warning: Logo file not found at {image_path}")
            return ""
    
    def _update_stats(self, start=True):
        """Update processing statistics"""
        with self.stats_lock:
            if start:
                self.processing_stats['total_requests'] += 1
                self.processing_stats['concurrent_requests'] += 1
                if self.processing_stats['concurrent_requests'] > self.processing_stats['max_concurrent']:
                    self.processing_stats['max_concurrent'] = self.processing_stats['concurrent_requests']
            else:
                self.processing_stats['concurrent_requests'] -= 1
    
    def _process_message_in_thread(self, user_input: str, session_id: str = None):
        """Process message in dedicated thread - this is the core processing function"""
        start_time = time.time()
        thread_id = threading.current_thread().name
        
        try:
            self._update_stats(start=True)
            
            # Get or create session
            session_id, session_data = self.session_manager.get_or_create_session(session_id)
            short_session = session_id[:8] if session_id else "new"
            
            print(f"🔄 [{thread_id}] Processing for session {short_session} at {time.strftime('%H:%M:%S.%f')[:-3]}")
            
            if user_input.strip().lower() in ["exit", "quit"]:
                session_data.chat_history_ui.append(("user", user_input))
                session_data.chat_history_ui.append(("assistant", "Have a great day!"))
                chat_history = [(session_data.chat_history_ui[i][1], session_data.chat_history_ui[i+1][1]) 
                               for i in range(0, len(session_data.chat_history_ui), 2)]
                return chat_history, session_id

            # Process the message through the workflow
            result = session_data.workflow.process_message(user_input, session_id)
            session_data.chat_history_ui.append(("user", user_input))
            session_data.chat_history_ui.append(("assistant", result))
            
            processing_time = time.time() - start_time
            print(f"✅ [{thread_id}] Completed session {short_session} in {processing_time:.2f}s")
            
        except Exception as e:
            print(f"❌ [{thread_id}] Error processing session {short_session}: {e}")
            error_msg = "I apologize, but I'm experiencing some technical difficulties. Please try again."
            session_data.chat_history_ui.append(("user", user_input))
            session_data.chat_history_ui.append(("assistant", error_msg))
        finally:
            self._update_stats(start=False)

        chat_history = [(session_data.chat_history_ui[i][1], session_data.chat_history_ui[i+1][1]) 
                       for i in range(0, len(session_data.chat_history_ui), 2)]
        return chat_history, session_id
    
    async def chat_interface_parallel(self, user_input: str, session_id: str = None) -> Tuple[List[Tuple[str, str]], str]:
        """True parallel chat interface using dedicated thread pool"""
        if not user_input.strip():
            return [], session_id or ""
        
        # Submit to thread pool and await result
        loop = asyncio.get_event_loop()
        chat_history, new_session_id = await loop.run_in_executor(
            self.executor,
            self._process_message_in_thread,
            user_input,
            session_id
        )
        
        return chat_history, new_session_id
    
    async def clear_chat_parallel(self, session_id: str = None) -> Tuple[List, str]:
        """Clear chat in parallel mode"""
        def _clear_in_thread(session_id):
            session_id, session_data = self.session_manager.get_or_create_session(session_id)
            session_data.chat_history_ui = []
            session_data.preference_service.clear_preferences()
            session_data.workflow.clear_memory()
            return [], session_id
        
        loop = asyncio.get_event_loop()
        result, new_session_id = await loop.run_in_executor(
            self.executor,
            _clear_in_thread,
            session_id
        )
        
        return result, new_session_id
    
    async def show_preferences_parallel(self, session_id: str = None) -> Tuple[str, str]:
        """Show preferences in parallel mode"""
        def _get_prefs_in_thread(session_id):
            session_id, session_data = self.session_manager.get_or_create_session(session_id)
            summary = session_data.preference_service.get_summary()
            return f"**Current Preferences:** {summary}", session_id
        
        loop = asyncio.get_event_loop()
        prefs, new_session_id = await loop.run_in_executor(
            self.executor,
            _get_prefs_in_thread,
            session_id
        )
        
        return prefs, new_session_id
    
    def get_stats_display(self) -> str:
        """Get current processing statistics for display"""
        with self.stats_lock:
            return f"""**System Status:**
• Total Requests: {self.processing_stats['total_requests']}
• Currently Processing: {self.processing_stats['concurrent_requests']}
• Max Concurrent: {self.processing_stats['max_concurrent']}
• Thread Pool Size: {self.executor._max_workers}"""
    
    def build_ui(self) -> gr.Blocks:
        """Build Gradio interface with true parallel processing"""
        custom_css = """
        .gradio-container {
            max-width: none !important;
            width: 90vw !important;
            margin: 0 auto !important;
        }
        
        .chatbot {
            height: 60vh !important;
            min-height: 400px !important;
            width: 100% !important;
        }
        
        .parallel-indicator {
            background: linear-gradient(45deg, #4CAF50, #45a049);
            color: white;
            padding: 15px;
            border-radius: 8px;
            margin: 10px 0;
            text-align: center;
            font-weight: bold;
            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
        }
        
        .stats-display {
            background-color: #f8f9fa;
            border: 1px solid #dee2e6;
            padding: 12px;
            border-radius: 6px;
            margin: 10px 0;
            font-family: monospace;
            font-size: 12px;
        }
        
        .test-section {
            background-color: #fff3cd;
            border: 1px solid #ffeaa7;
            padding: 15px;
            border-radius: 8px;
            margin: 10px 0;
        }
        """

        with gr.Blocks(
            title="Shopping Assistant - True Parallel Mode", 
            css=custom_css,
            analytics_enabled=False
        ) as demo:
            
            # Hidden session state
            session_state = gr.State(value=None)
            
            # Header with logo and parallel indicator
            logo_base64 = self.get_base64_image("assets/xponent_logo_white_on_orange.jpg")
            if logo_base64:
                gr.HTML(f"""
                <div style="text-align: center; padding: 20px; background: #F15F2E; border-radius: 0 0 12px 12px; margin-bottom: 20px;">
                    <img src="data:image/jpeg;base64,{logo_base64}" alt="Logo" style="width: 150px; border-radius: 8px; margin-bottom: 15px;" />
                    <h1 style="color: white; margin: 10px 0;">Smart Shopping Assistant</h1>
                    <div class="parallel-indicator">
                        ⚡ TRUE PARALLEL PROCESSING ENABLED ⚡<br>
                        Multiple users process simultaneously - No waiting!
                    </div>
                </div>
                """)
            else:
                gr.HTML("""
                <div style="text-align: center; padding: 20px; background: #F15F2E; border-radius: 0 0 12px 12px; margin-bottom: 20px;">
                    <h1 style="color: white;">Smart Shopping Assistant</h1>
                    <div class="parallel-indicator">
                        ⚡ TRUE PARALLEL PROCESSING ENABLED ⚡<br>
                        Multiple users process simultaneously - No waiting!
                    </div>
                </div>
                """)

            # System stats display
            stats_display = gr.Markdown(
                self.get_stats_display(),
                elem_classes=["stats-display"]
            )
            
            # Test instructions
            gr.HTML("""
            <div class="test-section">
                <h3>🧪 How to Test Parallel Processing:</h3>
                <ol>
                    <li><strong>Open multiple browser tabs</strong> to this same URL</li>
                    <li><strong>Send messages simultaneously</strong> from different tabs</li>
                    <li><strong>Watch the timestamps</strong> - they should process at the same time, not in sequence</li>
                    <li><strong>Check statistics</strong> above to see concurrent processing count</li>
                </ol>
                <p><strong>Example:</strong> Tab 1 sends "leather bags", Tab 2 sends "tote bags", Tab 3 sends "crossbody bags" - all at the same time!</p>
            </div>
            """)
            
            # Preferences display  
            preferences_display = gr.Markdown(
                "**Current Preferences:** None", 
                label="Current Preferences"
            )
            
            # Main chatbot interface
            chatbot = gr.Chatbot(
                render_markdown=False, 
                elem_classes=["chatbot"],
                placeholder="🚀 Ready for parallel processing! Open multiple tabs and chat simultaneously to test."
            )
            
            # Input area
            with gr.Row():
                msg = gr.Textbox(
                    placeholder="Type your message here... (Try: 'leather crossbody bags under $150')",
                    show_label=False,
                    container=True,
                    scale=4
                )
            
            # Control buttons
            with gr.Row():
                send_btn = gr.Button("🚀 Send", variant="primary", scale=1)
                show_more_btn = gr.Button("📋 Show More", variant="secondary", scale=1, visible=False)
                clear_btn = gr.Button("🗑️ Clear Chat", scale=1)
                prefs_btn = gr.Button("👀 Show Preferences", scale=1)
                refresh_stats_btn = gr.Button("📊 Refresh Stats", scale=1)

            # Event handlers with true parallel processing
            async def handle_send_parallel(user_input, session_id):
                """Handle sending message with true parallel processing"""
                if not user_input.strip():
                    return [], "", "**Current Preferences:** None", None, gr.update(visible=False), self.get_stats_display()
                
                # Process in parallel
                chat_history, new_session_id = await self.chat_interface_parallel(user_input, session_id)
                prefs, _ = await self.show_preferences_parallel(new_session_id)
                
                # Check show more visibility
                show_more_visible = False
                try:
                    _, session_data = self.session_manager.get_or_create_session(new_session_id)
                    if session_data and hasattr(session_data, 'can_show_more'):
                        show_more_visible = session_data.can_show_more()
                except:
                    pass
                
                return (
                    chat_history, 
                    "", 
                    prefs, 
                    new_session_id, 
                    gr.update(visible=show_more_visible),
                    self.get_stats_display()
                )
            
            async def handle_show_more_parallel(session_id):
                """Handle show more with parallel processing"""
                if not session_id:
                    return [], "**Current Preferences:** None", None, gr.update(visible=False), self.get_stats_display()
                
                chat_history, new_session_id = await self.chat_interface_parallel("show more", session_id)
                prefs, _ = await self.show_preferences_parallel(new_session_id)
                
                # Update button visibility
                show_more_visible = False
                try:
                    _, session_data = self.session_manager.get_or_create_session(new_session_id)
                    if session_data and hasattr(session_data, 'can_show_more'):
                        show_more_visible = session_data.can_show_more()
                except:
                    pass
                
                return (
                    chat_history, 
                    prefs, 
                    new_session_id, 
                    gr.update(visible=show_more_visible),
                    self.get_stats_display()
                )
            
            async def handle_clear_parallel(session_id):
                """Handle clear with parallel processing"""
                chat_result, new_session_id = await self.clear_chat_parallel(session_id)
                prefs, _ = await self.show_preferences_parallel(new_session_id)
                return (
                    chat_result, 
                    prefs, 
                    new_session_id, 
                    gr.update(visible=False),
                    self.get_stats_display()
                )

            async def handle_prefs_parallel(session_id):
                """Handle preferences with parallel processing"""
                prefs, session_id = await self.show_preferences_parallel(session_id)
                return prefs, session_id, self.get_stats_display()
            
            def refresh_stats():
                """Refresh statistics display"""
                return self.get_stats_display()

            # Bind event handlers
            send_btn.click(
                fn=handle_send_parallel, 
                inputs=[msg, session_state], 
                outputs=[chatbot, msg, preferences_display, session_state, show_more_btn, stats_display]
            )
            
            msg.submit(
                fn=handle_send_parallel, 
                inputs=[msg, session_state], 
                outputs=[chatbot, msg, preferences_display, session_state, show_more_btn, stats_display]
            )
            
            show_more_btn.click(
                fn=handle_show_more_parallel,
                inputs=[session_state],
                outputs=[chatbot, preferences_display, session_state, show_more_btn, stats_display]
            )
            
            clear_btn.click(
                fn=handle_clear_parallel,
                inputs=[session_state],
                outputs=[chatbot, preferences_display, session_state, show_more_btn, stats_display]
            )
            
            prefs_btn.click(
                fn=handle_prefs_parallel, 
                inputs=[session_state],
                outputs=[preferences_display, session_state, stats_display]
            )
            
            refresh_stats_btn.click(
                fn=refresh_stats,
                outputs=[stats_display]
            )

        return demo
    
    def launch(self, **kwargs):
        """Launch with optimal settings for parallel processing"""
        demo = self.build_ui()
        
        # Optimal settings for true parallel processing
        launch_settings = {
            "share": False,
            "debug": False,
            "server_name": "0.0.0.0",
            "server_port": 7860,
            "max_threads": 100,  # High thread count
            "show_error": True,
            "quiet": False,
            "prevent_thread_lock": True  # Prevent thread locking
        }
        
        # Override with provided kwargs
        launch_settings.update(kwargs)
        
        print("🚀 True Parallel Processing Configuration:")
        print(f"   • ThreadPoolExecutor workers: {self.executor._max_workers}")
        print(f"   • Gradio max_threads: {launch_settings.get('max_threads', 100)}")
        print(f"   • Prevent thread lock: ✅")
        print(f"   • Dedicated thread pool: ✅")
        print("\n📋 Test Instructions:")
        print("   1. Open multiple browser tabs to the same URL")
        print("   2. Send messages simultaneously from different tabs")
        print("   3. Watch timestamps - they should process concurrently!")
        print("   4. Check the statistics to see concurrent processing\n")
        
        try:
            demo.launch(**launch_settings)
        except Exception as e:
            print(f"❌ Error launching: {e}")
            raise
        finally:
            print("🧹 Shutting down thread pool...")
            self.executor.shutdown(wait=True)


def main():
    """Demo the true parallel interface"""
    # Mock session manager for demo
    class MockSessionManager:
        def __init__(self):
            self.sessions = {}
            
        def get_or_create_session(self, session_id=None):
            if not session_id:
                session_id = f"demo_session_{len(self.sessions)}"
            
            if session_id not in self.sessions:
                self.sessions[session_id] = MockSessionData()
            
            return session_id, self.sessions[session_id]
    
    class MockSessionData:
        def __init__(self):
            self.chat_history_ui = []
            self.workflow = MockWorkflow()
            self.preference_service = MockPreferenceService()
    
    class MockWorkflow:
        def process_message(self, message, session_id):
            # Simulate processing time
            import time
            import random
            time.sleep(random.uniform(0.5, 2.0))
            return f"Processed: {message} (took {random.uniform(0.5, 2.0):.1f}s)"
    
    class MockPreferenceService:
        def __init__(self):
            self.prefs = []
            
        def get_summary(self):
            return "Demo preferences"
    
    # Create and launch
    session_manager = MockSessionManager()
    interface = TrueParallelInterface(session_manager)
    interface.launch()


if __name__ == "__main__":
    main()
import gradio as gr
import base64
import asyncio
import time
from typing import List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from workflows.conversation_flow import ConversationWorkflow
    from services.preference_service import PreferenceService
    from ui.formatters import ProductFormatter
    from services.session_manager import SessionManager

class GradioInterface:
    """Manages the Gradio web interface with session support and parallel execution"""
    
    def __init__(self, session_manager, enable_parallel=True):
        self.session_manager = session_manager
        self.enable_parallel = enable_parallel
    
    def get_base64_image(self, image_path: str) -> str:
        """Convert image to base64 for embedding in HTML"""
        try:
            with open(image_path, "rb") as img_file:
                return base64.b64encode(img_file.read()).decode("utf-8")
        except FileNotFoundError:
            print(f"Warning: Logo file not found at {image_path}")
            return ""
    
    def chat_interface(self, user_input: str, session_id: str = None) -> Tuple[List[Tuple[str, str]], str]:
        """Handle chat interaction with session management"""
        # Get or create session
        session_id, session_data = self.session_manager.get_or_create_session(session_id)
        
        # Add timestamp to user query for UI display
        timestamp = time.strftime("%H:%M:%S")
        user_input_with_timestamp = f"{user_input}\n\n<small style='color: #666; font-size: 0.8em;'>🕒 {timestamp}</small>"
        
        if user_input.strip().lower() in ["exit", "quit"]:
            session_data.chat_history_ui.append(("user", user_input_with_timestamp))
            session_data.chat_history_ui.append(("assistant", "Have a great day!"))
            chat_history = [(session_data.chat_history_ui[i][1], session_data.chat_history_ui[i+1][1]) 
                           for i in range(0, len(session_data.chat_history_ui), 2)]
            return chat_history, session_id

        try:
            result, metrics = session_data.workflow.process_message(user_input, session_id)
            session_data.chat_history_ui.append(("user", user_input_with_timestamp))
            
            # Use clean response without footer (metrics now shown in dedicated display)
            response_text = result
            
            session_data.chat_history_ui.append(("assistant", response_text))
            
            # Return metrics for handler to log
            self._last_metrics = metrics
        except Exception as e:
            print(f"Error processing message: {e}")
            error_msg = "I apologize, but I'm experiencing some technical difficulties. Please try again."
            session_data.chat_history_ui.append(("user", user_input_with_timestamp))
            session_data.chat_history_ui.append(("assistant", error_msg))

        # Safely create chat history pairs
        chat_history = []
        for i in range(0, len(session_data.chat_history_ui) - 1, 2):
            if i + 1 < len(session_data.chat_history_ui):
                chat_history.append((session_data.chat_history_ui[i][1], session_data.chat_history_ui[i+1][1]))
        
        return chat_history, session_id

    async def chat_interface_async(self, user_input: str, session_id: str = None) -> Tuple[List[Tuple[str, str]], str]:
        """ASYNC chat interface for parallel processing"""
        start_time = time.time()
        
        # Get or create session
        session_id, session_data = self.session_manager.get_or_create_session(session_id)
        
        # Add timestamp to user query for UI display
        timestamp = time.strftime("%H:%M:%S")
        user_input_with_timestamp = f"{user_input}\n\n<small style='color: #666; font-size: 0.8em;'>🕒 {timestamp}</small>"
        
        print(f"🔄 Processing request for session {session_id[:8]}... at {timestamp}")
        
        if user_input.strip().lower() in ["exit", "quit"]:
            session_data.chat_history_ui.append(("user", user_input_with_timestamp))
            session_data.chat_history_ui.append(("assistant", "Have a great day!"))
            chat_history = [(session_data.chat_history_ui[i][1], session_data.chat_history_ui[i+1][1]) 
                           for i in range(0, len(session_data.chat_history_ui), 2)]
            return chat_history, session_id

        try:
            # Run the workflow processing in a thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            result, metrics = await loop.run_in_executor(
                None, 
                session_data.workflow.process_message, 
                user_input, 
                session_id
            )
            
            session_data.chat_history_ui.append(("user", user_input_with_timestamp))
            
            # Use clean response without footer (metrics now shown in dedicated display)
            response_text = result
            
            session_data.chat_history_ui.append(("assistant", response_text))
            
            # Return metrics for handler to log
            self._last_metrics = metrics
            
            processing_time = time.time() - start_time
            print(f"✅ Completed request for session {session_id[:8]} in {processing_time:.2f}s")
            
        except Exception as e:
            print(f"❌ Error processing message for session {session_id[:8]}: {e}")
            error_msg = "I apologize, but I'm experiencing some technical difficulties. Please try again."
            session_data.chat_history_ui.append(("user", user_input_with_timestamp))
            session_data.chat_history_ui.append(("assistant", error_msg))

        # Safely create chat history pairs
        chat_history = []
        for i in range(0, len(session_data.chat_history_ui) - 1, 2):
            if i + 1 < len(session_data.chat_history_ui):
                chat_history.append((session_data.chat_history_ui[i][1], session_data.chat_history_ui[i+1][1]))
        
        return chat_history, session_id

    def clear_chat(self, session_id: str = None) -> Tuple[List, str]:
        """Clear chat history and reset preferences for a session"""
        session_id, session_data = self.session_manager.get_or_create_session(session_id)
        
        session_data.chat_history_ui = []
        session_data.preference_service.clear_preferences()
        session_data.workflow.clear_memory()
        
        return [], session_id

    async def clear_chat_async(self, session_id: str = None) -> Tuple[List, str]:
        """ASYNC clear chat for parallel processing"""
        session_id, session_data = self.session_manager.get_or_create_session(session_id)
        
        # Run clearing operations in thread pool
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._clear_session_data, session_data)
        
        return [], session_id
    
    def _clear_session_data(self, session_data):
        """Helper method to clear session data (runs in thread pool)"""
        session_data.chat_history_ui = []
        session_data.preference_service.clear_preferences()
        session_data.workflow.clear_memory()
    
    def format_metrics_display(self, metrics: dict = None) -> str:
        """Format metrics for the dedicated metrics display"""
        if not metrics:
            return """<div style='background-color: #f8f9fa; border: 1px solid #dee2e6; border-radius: 5px; padding: 10px; margin: 10px 0; font-size: 0.85em; color: #6c757d;'>
                📊 <strong>Performance Metrics:</strong> Ready to track your queries
            </div>"""
        
        tokens = metrics.get('tokens', 'N/A')
        latency = metrics.get('latency', 0)
        cost = metrics.get('cost', 0)
        timestamp = metrics.get('timestamp', 'N/A')
        
        return f"""<div style='background-color: #e8f5e8; border: 1px solid #28a745; border-radius: 5px; padding: 10px; margin: 10px 0; font-size: 0.85em; color: #155724;'>
            📊 <strong>Latest Query Metrics:</strong><br>
            ⚡ <strong>Tokens:</strong> {tokens} | 
            ⏱️ <strong>Response Time:</strong> {latency:.2f}s | 
            💰 <strong>Cost:</strong> ${cost:.4f} | 
            🕐 <strong>Time:</strong> {timestamp}
        </div>"""

    def show_current_preferences(self, session_id: str = None) -> Tuple[str, str]:
        """Display current user preferences for a session"""
        session_id, session_data = self.session_manager.get_or_create_session(session_id)
        preferences = f"**Current Preferences:** {session_data.preference_service.get_summary()}"
        return preferences, session_id

    async def show_current_preferences_async(self, session_id: str = None) -> Tuple[str, str]:
        """ASYNC show preferences for parallel processing"""
        session_id, session_data = self.session_manager.get_or_create_session(session_id)
        
        # Run preference retrieval in thread pool
        loop = asyncio.get_event_loop()
        summary = await loop.run_in_executor(
            None, 
            session_data.preference_service.get_summary
        )
        
        preferences = f"**Current Preferences:** {summary}"
        return preferences, session_id
    
    def build_ui(self) -> gr.Blocks:
        """Build and return the complete Gradio interface"""
        # Custom CSS for styling
        custom_css = """
        /* Force full width container */
        .gradio-container {
            max-width: none !important;
            width: 90vw !important;
            margin: 0 auto !important;
            padding: 0 !important;
        }
        
        /* Remove any width constraints from main content area */
        .main {
            max-width: none !important;
            width: 100% !important;
            padding: 0 !important;
        }
        
        /* Full width for all blocks and rows */
        .block, .gradio-row, .gradio-column {
            max-width: none !important;
            width: 100% !important;
        }
        
        /* Chatbot full width and proper height */
        .chatbot {
            height: 60vh !important;
            min-height: 400px !important;
            max-height: 700px !important;
            width: 100% !important;
        }
        
        /* Banner styling */
        .banner-container {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 20px 0;
            background-color: #F15F2E;
            border-radius: 0 0 12px 12px;
            margin-bottom: 20px;
        }
        
        .banner-img {
            width: clamp(120px, 6vw, 180px);
            height: auto;
            border-radius: 8px;
            margin-bottom: 15px;
        }
        
        .banner-title {
            color: white;
            margin: 10px 0 5px;
            font-weight: bold;
            font-size: clamp(1.5rem, 3vw, 2.5rem);
        }
        
        /* Input and button styling */
        .gradio-textbox input, .gradio-textbox textarea {
            width: 100% !important;
        }
        
        /* Timestamp styling in chat messages */
        .timestamp {
            color: #666 !important;
            font-size: 0.75em !important;
            font-style: italic !important;
            margin-top: 5px !important;
            opacity: 0.8 !important;
        }
        
        /* Chat message styling to accommodate timestamps */
        .chatbot .message {
            margin-bottom: 8px !important;
        }
        
        /* Show More button styling */
        .show-more-btn {
            background-color: #F15F2E !important;
            color: white !important;
            border: none !important;
            padding: 12px 24px !important;
            border-radius: 6px !important;
            cursor: pointer !important;
            font-size: 14px !important;
            font-weight: 500 !important;
            transition: background-color 0.2s ease !important;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1) !important;
        }
        
        .show-more-btn:hover {
            background-color: #d54d1e !important;
            transform: translateY(-1px) !important;
            box-shadow: 0 4px 8px rgba(0,0,0,0.15) !important;
        }
        
        .show-more-btn:active {
            transform: translateY(0) !important;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1) !important;
        }
        
        /* Mobile responsive */
        @media (max-width: 768px) {
            .gradio-container {
                width: 94vw !important;
            }
            
            .banner-container {
                padding: 15px 0;
            }
            
            .banner-img {
                width: clamp(100px, 15vw, 150px);
            }
            
            .chatbot {
                height: 50vh !important;
                min-height: 300px !important;
            }
        }
        
        /* Performance metrics styling */
        .metrics-info {
            color: #888 !important;
            font-size: 0.75em !important;
            border-top: 1px solid #eee !important;
            padding-top: 5px !important;
            margin-top: 8px !important;
            font-family: 'Courier New', monospace !important;
            background: #f8f9fa !important;
            padding: 6px 8px !important;
            border-radius: 4px !important;
            display: inline-block !important;
        }
        """

        with gr.Blocks(title="Smart Shopping Assistant", css=custom_css) as demo:
            
            # Hidden session state
            session_state = gr.State(value=None)
            
            # Header with logo and parallel processing indicator
            logo_base64 = self.get_base64_image("assets/xponent_logo_white_on_orange.jpg")
            if logo_base64:
                if self.enable_parallel:
                    gr.HTML(f"""
                    <div class="banner-container">
                        <img src="data:image/jpeg;base64,{logo_base64}" alt="Xponent.ai Logo" class="banner-img" />
                        <h1 class="banner-title">Smart Shopping Assistant</h1>
                        <div style="background-color: #e3f2fd; border-left: 4px solid #2196f3; padding: 10px; margin: 10px 0; border-radius: 4px; font-size: 14px; color: #1565c0;">
                            🚀 Parallel Processing Enabled - Multiple users can chat simultaneously
                        </div>
                    </div>
                    """)
                else:
                    gr.HTML(f"""
                    <div class="banner-container">
                        <img src="data:image/jpeg;base64,{logo_base64}" alt="Xponent.ai Logo" class="banner-img" />
                        <h1 class="banner-title">Smart Shopping Assistant</h1>
                    </div>
                    """)
            else:
                if self.enable_parallel:
                    gr.HTML("""
                    <div class="banner-container">
                        <h1 class="banner-title">Smart Shopping Assistant</h1>
                        <p style="color: white; opacity: 0.9;">Find the perfect bag for your needs</p>
                        <div style="background-color: #e3f2fd; border-left: 4px solid #2196f3; padding: 10px; margin: 10px 0; border-radius: 4px; font-size: 14px; color: #1565c0;">
                            🚀 Parallel Processing Enabled - Multiple users can chat simultaneously
                        </div>
                    </div>
                    """)
                else:
                    gr.HTML("""
                    <div class="banner-container">
                        <h1 class="banner-title">Smart Shopping Assistant</h1>
                        <p style="color: white; opacity: 0.9;">Find the perfect bag for your needs</p>
                    </div>
                    """)

            # Preferences display  
            preferences_display = gr.Markdown(
                "**Current Preferences:** None", 
                label="Current Preferences"
            )
            
            # Metrics display for better visibility on Render
            metrics_display = gr.HTML(
                """<div style='background-color: #f8f9fa; border: 1px solid #dee2e6; border-radius: 5px; padding: 10px; margin: 10px 0; font-size: 0.85em; color: #6c757d;'>
                    📊 <strong>Performance Metrics:</strong> Ready to track your queries
                </div>""",
                label="Performance Metrics"
            )
            
            # Main chatbot interface with parallel processing info
            if self.enable_parallel:
                placeholder_text = "Welcome! Multiple users can chat simultaneously. Each session is isolated."
                input_placeholder = "Try: 'leather bags under $100' - Your session is isolated from other users"
            else:
                placeholder_text = "Welcome! Ask me to find bags, set preferences, or browse products."
                input_placeholder = "Try: 'leather bags under $100' or 'show me crossbody bags'"
                
            chatbot = gr.Chatbot(
                render_markdown=False, 
                elem_classes=["chatbot"],
                placeholder=placeholder_text
            )
            
            # Input area
            with gr.Row():
                msg = gr.Textbox(
                    placeholder=input_placeholder,
                    show_label=False,
                    container=True,
                    scale=4
                )
            
            # Control buttons
            with gr.Row():
                send_btn = gr.Button("Send", variant="primary", scale=1)
                show_more_btn = gr.Button("Show More Results", variant="secondary", scale=1, visible=False)
                clear_btn = gr.Button("Clear Chat & Preferences", scale=1)
                prefs_btn = gr.Button("Show Preferences", scale=1)

            # Event handlers - Enhanced with async support
            async def handle_send_async(user_input, session_id):
                """ASYNC handler for sending messages (enables parallel processing)"""
                if not user_input.strip():
                    empty_metrics = self.format_metrics_display()
                    return [], "", "**Current Preferences:** None", None, gr.update(visible=False), empty_metrics
                
                chat_history, new_session_id = await self.chat_interface_async(user_input, session_id)
                prefs, _ = await self.show_current_preferences_async(new_session_id)
                
                # Log user query for analytics (single count per interaction)
                metrics = getattr(self, '_last_metrics', None)
                self.session_manager.log_user_query(new_session_id, user_input, "chat_interaction", metrics)
                
                # Format metrics display
                metrics_html = self.format_metrics_display(metrics)
                
                # Check if show more button should be visible
                show_more_visible = False
                try:
                    _, session_data = self.session_manager.get_or_create_session(new_session_id)
                    if session_data and hasattr(session_data, 'can_show_more'):
                        show_more_visible = session_data.can_show_more()
                except Exception as e:
                    show_more_visible = False
                
                return chat_history, "", prefs, new_session_id, gr.update(visible=show_more_visible), metrics_html

            def handle_send(user_input, session_id):
                """Handle sending a message (fallback sync handler)"""
                if not user_input.strip():
                    empty_metrics = self.format_metrics_display()
                    return [], "", "**Current Preferences:** None", None, gr.update(visible=False), empty_metrics
                
                chat_history, new_session_id = self.chat_interface(user_input, session_id)
                prefs, _ = self.show_current_preferences(new_session_id)
                
                # Log user query for analytics (single count per interaction)
                metrics = getattr(self, '_last_metrics', None)
                self.session_manager.log_user_query(new_session_id, user_input, "chat_interaction", metrics)
                
                # Format metrics display
                metrics_html = self.format_metrics_display(metrics)
                
                # Check if show more button should be visible
                show_more_visible = False
                try:
                    _, session_data = self.session_manager.get_or_create_session(new_session_id)
                    if session_data and hasattr(session_data, 'can_show_more'):
                        show_more_visible = session_data.can_show_more()
                except Exception as e:
                    show_more_visible = False
                
                return chat_history, "", prefs, new_session_id, gr.update(visible=show_more_visible), metrics_html
            
            async def handle_show_more_async(session_id):
                """ASYNC handler for show more button"""
                if not session_id:
                    empty_metrics = self.format_metrics_display()
                    return [], "**Current Preferences:** None", None, gr.update(visible=False), empty_metrics
                
                # LOG SHOW MORE ACTION
                self.session_manager.log_user_query(session_id, "show more", "show_more_action")
                
                chat_history, new_session_id = await self.chat_interface_async("show more", session_id)
                prefs, _ = await self.show_current_preferences_async(new_session_id)
                
                # Format metrics display
                metrics = getattr(self, '_last_metrics', None)
                metrics_html = self.format_metrics_display(metrics)
                
                # Update button visibility based on remaining results
                show_more_visible = False
                try:
                    _, session_data = self.session_manager.get_or_create_session(new_session_id)
                    if session_data and hasattr(session_data, 'can_show_more'):
                        show_more_visible = session_data.can_show_more()
                except Exception as e:
                    show_more_visible = False
                
                return chat_history, prefs, new_session_id, gr.update(visible=show_more_visible), metrics_html

            def handle_show_more(session_id):
                """Handle show more button click (fallback sync handler)"""
                if not session_id:
                    empty_metrics = self.format_metrics_display()
                    return [], "**Current Preferences:** None", None, gr.update(visible=False), empty_metrics
                
                # LOG SHOW MORE ACTION
                self.session_manager.log_user_query(session_id, "show more", "show_more_action")
                
                # Simulate "show more" message
                chat_history, new_session_id = self.chat_interface("show more", session_id)
                prefs, _ = self.show_current_preferences(new_session_id)
                
                # Format metrics display
                metrics = getattr(self, '_last_metrics', None)
                metrics_html = self.format_metrics_display(metrics)
                
                # Update button visibility based on remaining results
                show_more_visible = False
                try:
                    _, session_data = self.session_manager.get_or_create_session(new_session_id)
                    if session_data and hasattr(session_data, 'can_show_more'):
                        show_more_visible = session_data.can_show_more()
                except Exception as e:
                    show_more_visible = False
                
                return chat_history, prefs, new_session_id, gr.update(visible=show_more_visible), metrics_html
            
            async def handle_clear_async(session_id):
                """ASYNC handler for clearing chat"""
                chat_result, new_session_id = await self.clear_chat_async(session_id)
                prefs, _ = await self.show_current_preferences_async(new_session_id)
                empty_metrics = self.format_metrics_display()
                return chat_result, prefs, new_session_id, gr.update(visible=False), empty_metrics

            def handle_clear(session_id):
                """Handle clearing chat and preferences (fallback sync handler)"""
                chat_result, new_session_id = self.clear_chat(session_id)
                prefs, _ = self.show_current_preferences(new_session_id)
                empty_metrics = self.format_metrics_display()
                return chat_result, prefs, new_session_id, gr.update(visible=False), empty_metrics

            async def handle_show_prefs_async(session_id):
                """ASYNC handler for showing preferences"""
                prefs, session_id = await self.show_current_preferences_async(session_id)
                # Keep existing metrics display when showing preferences
                current_metrics = getattr(self, '_last_metrics', None)
                metrics_html = self.format_metrics_display(current_metrics)
                return prefs, session_id, metrics_html

            def handle_show_prefs(session_id):
                """Handle showing preferences (fallback sync handler)"""
                prefs, session_id = self.show_current_preferences(session_id)
                # Keep existing metrics display when showing preferences
                current_metrics = getattr(self, '_last_metrics', None)
                metrics_html = self.format_metrics_display(current_metrics)
                return prefs, session_id, metrics_html

            # Bind event handlers (choose async or sync based on parallel mode)
            if self.enable_parallel:
                # Use ASYNC handlers for parallel processing
                send_btn.click(
                    fn=handle_send_async, 
                    inputs=[msg, session_state], 
                    outputs=[chatbot, msg, preferences_display, session_state, show_more_btn, metrics_display]
                )
                
                msg.submit(
                    fn=handle_send_async, 
                    inputs=[msg, session_state], 
                    outputs=[chatbot, msg, preferences_display, session_state, show_more_btn, metrics_display]
                )
                
                show_more_btn.click(
                    fn=handle_show_more_async,
                    inputs=[session_state],
                    outputs=[chatbot, preferences_display, session_state, show_more_btn, metrics_display]
                )
                
                clear_btn.click(
                    fn=handle_clear_async,
                    inputs=[session_state],
                    outputs=[chatbot, preferences_display, session_state, show_more_btn, metrics_display]
                )
                
                prefs_btn.click(
                    fn=handle_show_prefs_async, 
                    inputs=[session_state],
                    outputs=[preferences_display, session_state, metrics_display]
                )
            else:
                # Use synchronous handlers (original behavior)
                send_btn.click(
                    fn=handle_send, 
                    inputs=[msg, session_state], 
                    outputs=[chatbot, msg, preferences_display, session_state, show_more_btn, metrics_display]
                )
                
                msg.submit(
                    fn=handle_send, 
                    inputs=[msg, session_state], 
                    outputs=[chatbot, msg, preferences_display, session_state, show_more_btn, metrics_display]
                )
                
                show_more_btn.click(
                    fn=handle_show_more,
                    inputs=[session_state],
                    outputs=[chatbot, preferences_display, session_state, show_more_btn, metrics_display]
                )
                
                clear_btn.click(
                    fn=handle_clear,
                    inputs=[session_state],
                    outputs=[chatbot, preferences_display, session_state, show_more_btn, metrics_display]
                )
                
                prefs_btn.click(
                    fn=handle_show_prefs, 
                    inputs=[session_state],
                    outputs=[preferences_display, session_state, metrics_display]
                )

        return demo
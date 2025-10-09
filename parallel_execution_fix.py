# parallel_execution_fix.py
"""
Solution for enabling parallel execution for multiple users in the Shopping Assistant

The issue: Gradio processes requests synchronously by default, causing FIFO behavior
The solution: Enable concurrency in Gradio and make handlers async
"""

import gradio as gr
import asyncio
from typing import List, Tuple, TYPE_CHECKING
import base64
import time

if TYPE_CHECKING:
    from workflows.conversation_flow import ConversationWorkflow
    from services.preference_service import PreferenceService
    from ui.formatters import ProductFormatter
    from services.session_manager import SessionManager

class ParallelGradioInterface:
    """Enhanced Gradio interface with parallel execution support"""
    
    def __init__(self, session_manager):
        self.session_manager = session_manager
    
    def get_base64_image(self, image_path: str) -> str:
        """Convert image to base64 for embedding in HTML"""
        try:
            with open(image_path, "rb") as img_file:
                return base64.b64encode(img_file.read()).decode("utf-8")
        except FileNotFoundError:
            print(f"Warning: Logo file not found at {image_path}")
            return ""
    
    async def chat_interface_async(self, user_input: str, session_id: str = None) -> Tuple[List[Tuple[str, str]], str]:
        """ASYNC chat interface for parallel processing"""
        start_time = time.time()
        
        # Get or create session
        session_id, session_data = self.session_manager.get_or_create_session(session_id)
        print(f"🔄 Processing request for session {session_id[:8]}... at {time.strftime('%H:%M:%S')}")
        
        if user_input.strip().lower() in ["exit", "quit"]:
            session_data.chat_history_ui.append(("user", user_input))
            session_data.chat_history_ui.append(("assistant", "Have a great day!"))
            chat_history = [(session_data.chat_history_ui[i][1], session_data.chat_history_ui[i+1][1]) 
                           for i in range(0, len(session_data.chat_history_ui), 2)]
            return chat_history, session_id

        try:
            # Run the workflow processing in a thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, 
                session_data.workflow.process_message, 
                user_input, 
                session_id
            )
            
            session_data.chat_history_ui.append(("user", user_input))
            session_data.chat_history_ui.append(("assistant", result))
            
            processing_time = time.time() - start_time
            print(f"✅ Completed request for session {session_id[:8]} in {processing_time:.2f}s")
            
        except Exception as e:
            print(f"❌ Error processing message for session {session_id[:8]}: {e}")
            error_msg = "I apologize, but I'm experiencing some technical difficulties. Please try again."
            session_data.chat_history_ui.append(("user", user_input))
            session_data.chat_history_ui.append(("assistant", error_msg))

        chat_history = [(session_data.chat_history_ui[i][1], session_data.chat_history_ui[i+1][1]) 
                       for i in range(0, len(session_data.chat_history_ui), 2)]
        return chat_history, session_id

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
        """Build Gradio interface with concurrency support"""
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
        
        /* Parallel processing indicator */
        .processing-indicator {
            background-color: #e3f2fd;
            border-left: 4px solid #2196f3;
            padding: 10px;
            margin: 10px 0;
            border-radius: 4px;
            font-size: 14px;
            color: #1565c0;
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
        """

        # Configure Gradio for concurrent processing
        with gr.Blocks(
            title="Smart Shopping Assistant - Parallel Mode", 
            css=custom_css,
            analytics_enabled=False  # Disable analytics for better performance
        ) as demo:
            
            # Hidden session state
            session_state = gr.State(value=None)
            
            # Header with logo and parallel processing indicator
            logo_base64 = self.get_base64_image("assets/xponent_logo_white_on_orange.jpg")
            if logo_base64:
                gr.HTML(f"""
                <div class="banner-container">
                    <img src="data:image/jpeg;base64,{logo_base64}" alt="Xponent.ai Logo" class="banner-img" />
                    <h1 class="banner-title">Smart Shopping Assistant</h1>
                    <div class="processing-indicator">
                        🚀 Parallel Processing Enabled - Multiple users can chat simultaneously
                    </div>
                </div>
                """)
            else:
                gr.HTML("""
                <div class="banner-container">
                    <h1 class="banner-title">Smart Shopping Assistant</h1>
                    <p style="color: white; opacity: 0.9;">Find the perfect bag for your needs</p>
                    <div class="processing-indicator">
                        🚀 Parallel Processing Enabled - Multiple users can chat simultaneously
                    </div>
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
                placeholder="Welcome! Multiple users can chat simultaneously. Each user has isolated sessions."
            )
            
            # Input area
            with gr.Row():
                msg = gr.Textbox(
                    placeholder="Try: 'leather bags under $100' - Your session is isolated from other users",
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

            # ASYNC Event handlers
            async def handle_send_async(user_input, session_id):
                """ASYNC handler for sending messages"""
                if not user_input.strip():
                    return [], "", "**Current Preferences:** None", None, gr.update(visible=False)
                
                chat_history, new_session_id = await self.chat_interface_async(user_input, session_id)
                prefs, _ = await self.show_current_preferences_async(new_session_id)
                
                # Check if show more button should be visible
                show_more_visible = False
                try:
                    _, session_data = self.session_manager.get_or_create_session(new_session_id)
                    if session_data and hasattr(session_data, 'can_show_more'):
                        show_more_visible = session_data.can_show_more()
                except Exception as e:
                    show_more_visible = False
                
                return chat_history, "", prefs, new_session_id, gr.update(visible=show_more_visible)
            
            async def handle_show_more_async(session_id):
                """ASYNC handler for show more button"""
                if not session_id:
                    return [], "**Current Preferences:** None", None, gr.update(visible=False)
                
                chat_history, new_session_id = await self.chat_interface_async("show more", session_id)
                prefs, _ = await self.show_current_preferences_async(new_session_id)
                
                # Update button visibility based on remaining results
                show_more_visible = False
                try:
                    _, session_data = self.session_manager.get_or_create_session(new_session_id)
                    if session_data and hasattr(session_data, 'can_show_more'):
                        show_more_visible = session_data.can_show_more()
                except Exception as e:
                    show_more_visible = False
                
                return chat_history, prefs, new_session_id, gr.update(visible=show_more_visible)
            
            async def handle_clear_async(session_id):
                """ASYNC handler for clearing chat"""
                chat_result, new_session_id = await self.clear_chat_async(session_id)
                prefs, _ = await self.show_current_preferences_async(new_session_id)
                return chat_result, prefs, new_session_id, gr.update(visible=False)

            async def handle_show_prefs_async(session_id):
                """ASYNC handler for showing preferences"""
                prefs, session_id = await self.show_current_preferences_async(session_id)
                return prefs, session_id

            # Bind ASYNC event handlers
            send_btn.click(
                fn=handle_send_async, 
                inputs=[msg, session_state], 
                outputs=[chatbot, msg, preferences_display, session_state, show_more_btn],
                api_name="send_message"
            )
            
            msg.submit(
                fn=handle_send_async, 
                inputs=[msg, session_state], 
                outputs=[chatbot, msg, preferences_display, session_state, show_more_btn],
                api_name="submit_message"
            )
            
            show_more_btn.click(
                fn=handle_show_more_async,
                inputs=[session_state],
                outputs=[chatbot, preferences_display, session_state, show_more_btn],
                api_name="show_more"
            )
            
            clear_btn.click(
                fn=handle_clear_async,
                inputs=[session_state],
                outputs=[chatbot, preferences_display, session_state, show_more_btn],
                api_name="clear_chat"
            )
            
            prefs_btn.click(
                fn=handle_show_prefs_async, 
                inputs=[session_state],
                outputs=[preferences_display, session_state],
                api_name="show_preferences"
            )

        return demo
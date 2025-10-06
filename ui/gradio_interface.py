import gradio as gr
import base64
from typing import List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from workflows.conversation_flow import ConversationWorkflow
    from services.preference_service import PreferenceService
    from ui.formatters import ProductFormatter
    from services.session_manager import SessionManager

class GradioInterface:
    """Manages the Gradio web interface with session support"""
    
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
    
    def chat_interface(self, user_input: str, session_id: str = None) -> Tuple[List[Tuple[str, str]], str]:
        """Handle chat interaction with session management"""
        # Get or create session
        session_id, session_data = self.session_manager.get_or_create_session(session_id)
        
        if user_input.strip().lower() in ["exit", "quit"]:
            session_data.chat_history_ui.append(("user", user_input))
            session_data.chat_history_ui.append(("assistant", "Have a great day!"))
            chat_history = [(session_data.chat_history_ui[i][1], session_data.chat_history_ui[i+1][1]) 
                           for i in range(0, len(session_data.chat_history_ui), 2)]
            return chat_history, session_id

        try:
            result = session_data.workflow.process_message(user_input)
            session_data.chat_history_ui.append(("user", user_input))
            session_data.chat_history_ui.append(("assistant", result))
        except Exception as e:
            print(f"Error processing message: {e}")
            error_msg = "I apologize, but I'm experiencing some technical difficulties. Please try again."
            session_data.chat_history_ui.append(("user", user_input))
            session_data.chat_history_ui.append(("assistant", error_msg))

        chat_history = [(session_data.chat_history_ui[i][1], session_data.chat_history_ui[i+1][1]) 
                       for i in range(0, len(session_data.chat_history_ui), 2)]
        return chat_history, session_id

    def clear_chat(self, session_id: str = None) -> Tuple[List, str]:
        """Clear chat history and reset preferences for a session"""
        session_id, session_data = self.session_manager.get_or_create_session(session_id)
        
        session_data.chat_history_ui = []
        session_data.preference_service.clear_preferences()
        session_data.workflow.clear_memory()
        
        return [], session_id

    def show_current_preferences(self, session_id: str = None) -> Tuple[str, str]:
        """Display current user preferences for a session"""
        session_id, session_data = self.session_manager.get_or_create_session(session_id)
        preferences = f"**Current Preferences:** {session_data.preference_service.get_summary()}"
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

        with gr.Blocks(title="Smart Shopping Assistant", css=custom_css) as demo:
            
            # Hidden session state
            session_state = gr.State(value=None)
            
            # Header with logo
            logo_base64 = self.get_base64_image("assets/xponent_logo_white_on_orange.jpg")
            if logo_base64:
                gr.HTML(f"""
                <div class="banner-container">
                    <img src="data:image/jpeg;base64,{logo_base64}" alt="Xponent.ai Logo" class="banner-img" />
                    <h1 class="banner-title">Smart Shopping Assistant</h1>
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
            
            # Main chatbot interface
            chatbot = gr.Chatbot(
                render_markdown=False, 
                elem_classes=["chatbot"],
                placeholder="Welcome! Ask me to find bags, set preferences, or browse products."
            )
            
            # Input area
            with gr.Row():
                msg = gr.Textbox(
                    placeholder="Try: 'leather bags under $100' or 'show me crossbody bags'",
                    show_label=False,
                    container=True,
                    scale=4
                )
            
            # Control buttons
            with gr.Row():
                send_btn = gr.Button("Send", variant="primary", scale=1)
                clear_btn = gr.Button("Clear Chat & Preferences", scale=1)
                prefs_btn = gr.Button("Show Preferences", scale=1)

            # Event handlers
            def handle_send(user_input, session_id):
                """Handle sending a message"""
                if not user_input.strip():
                    # Just return current state without changes
                    if session_id:
                        _, session_data = self.session_manager.get_or_create_session(session_id)
                        chat_history = [(session_data.chat_history_ui[i][1], session_data.chat_history_ui[i+1][1]) 
                                       for i in range(0, len(session_data.chat_history_ui), 2)]
                        prefs, _ = self.show_current_preferences(session_id)
                        return chat_history, "", prefs, session_id
                    return [], "", "**Current Preferences:** None", None
                
                chat_history, new_session_id = self.chat_interface(user_input, session_id)
                prefs, _ = self.show_current_preferences(new_session_id)
                return chat_history, "", prefs, new_session_id
            def handle_clear(session_id):
                """Handle clearing chat and preferences"""
                chat_result, new_session_id = self.clear_chat(session_id)
                prefs, _ = self.show_current_preferences(new_session_id)
                return chat_result, prefs, new_session_id

            def handle_show_prefs(session_id):
                """Handle showing preferences"""
                prefs, session_id = self.show_current_preferences(session_id)
                return prefs, session_id

            # Bind event handlers
            send_btn.click(
                fn=handle_send, 
                inputs=[msg, session_state], 
                outputs=[chatbot, msg, preferences_display, session_state]
            )
            
            msg.submit(
                fn=handle_send, 
                inputs=[msg, session_state], 
                outputs=[chatbot, msg, preferences_display, session_state]
            )
            
            clear_btn.click(
                fn=handle_clear,
                inputs=[session_state],
                outputs=[chatbot, preferences_display, session_state]
            )
            
            prefs_btn.click(
                fn=handle_show_prefs, 
                inputs=[session_state],
                outputs=[preferences_display, session_state]
            )

        return demo
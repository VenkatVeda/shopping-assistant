import gradio as gr
import base64
from typing import List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from workflows.conversation_flow import ConversationWorkflow
    from services.preference_service import PreferenceService
    from ui.formatters import ProductFormatter

class GradioInterface:
    """Manages the Gradio web interface"""
    
    def __init__(self, conversation_workflow, preference_service, formatter):
        self.workflow = conversation_workflow
        self.preference_service = preference_service
        self.formatter = formatter
        self.chat_history_ui = []
    
    def get_base64_image(self, image_path: str) -> str:
        """Convert image to base64 for embedding in HTML"""
        try:
            with open(image_path, "rb") as img_file:
                return base64.b64encode(img_file.read()).decode("utf-8")
        except FileNotFoundError:
            print(f"Warning: Logo file not found at {image_path}")
            return ""
    
    def chat_interface(self, user_input: str) -> List[Tuple[str, str]]:
        """Handle chat interaction and return formatted history"""
        if user_input.strip().lower() in ["exit", "quit"]:
            self.chat_history_ui.append(("user", user_input))
            self.chat_history_ui.append(("assistant", "Have a great day!"))
            return [(self.chat_history_ui[i][1], self.chat_history_ui[i+1][1]) 
                   for i in range(0, len(self.chat_history_ui), 2)]

        try:
            result = self.workflow.process_message(user_input)
            self.chat_history_ui.append(("user", user_input))
            self.chat_history_ui.append(("assistant", result))
        except Exception as e:
            print(f"Error processing message: {e}")
            error_msg = "I apologize, but I'm experiencing some technical difficulties. Please try again."
            self.chat_history_ui.append(("user", user_input))
            self.chat_history_ui.append(("assistant", error_msg))

        return [(self.chat_history_ui[i][1], self.chat_history_ui[i+1][1]) 
               for i in range(0, len(self.chat_history_ui), 2)]

    def clear_chat(self):
        """Clear chat history and reset preferences"""
        self.chat_history_ui = []
        self.preference_service.clear_preferences()
        self.workflow.clear_memory()
        return []

    def show_current_preferences(self) -> str:
        """Display current user preferences"""
        return f"**Current Preferences:** {self.preference_service.get_summary()}"
    
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
                self.show_current_preferences(), 
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
            def handle_send(user_input):
                """Handle sending a message"""
                if not user_input.strip():
                    return self.chat_history_ui, "", self.show_current_preferences()
                
                result = self.chat_interface(user_input)
                updated_prefs = self.show_current_preferences()
                return result, "", updated_prefs

            def handle_clear():
                """Handle clearing chat and preferences"""
                chat_result = self.clear_chat()
                prefs_result = self.show_current_preferences()
                return chat_result, prefs_result

            # Bind event handlers
            send_btn.click(
                fn=handle_send, 
                inputs=msg, 
                outputs=[chatbot, msg, preferences_display]
            )
            
            msg.submit(
                fn=handle_send, 
                inputs=msg, 
                outputs=[chatbot, msg, preferences_display]
            )
            
            clear_btn.click(
                fn=handle_clear,
                outputs=[chatbot, preferences_display]
            )
            
            prefs_btn.click(
                fn=self.show_current_preferences, 
                outputs=preferences_display
            )

        return demo
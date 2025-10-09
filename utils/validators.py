# utils/validators.py
from langchain_core.documents import Document
from models.preferences import UserPreferences

def matches_preferences(doc: Document, preferences: UserPreferences) -> bool:
    meta = doc.metadata
    content_lower = doc.page_content.lower()
    doc_name = meta.get('name', '').lower()
    searchable_text = f"{doc_name} {content_lower}"
    
    # Check price constraints
    if preferences.price_min is not None or preferences.price_max is not None:
        try:
            doc_price = meta.get('price', 0)
            if isinstance(doc_price, str):
                doc_price = float(doc_price.replace('$', '').replace(',', ''))
            else:
                doc_price = float(doc_price)
            
            if preferences.price_max is not None and doc_price > preferences.price_max:
                return False
            if preferences.price_min is not None and doc_price < preferences.price_min:
                return False
        except (ValueError, TypeError):
            return False

    # Check brand constraints
    if preferences.brands:
        doc_brand = meta.get('brand', '').lower()
        if not any(brand.lower() in doc_brand for brand in preferences.brands):
            return False
    
    # Check excluded brands - if any excluded brand is found, reject the product
    if preferences.excluded_brands:
        doc_brand = meta.get('brand', '').lower()
        for excluded_brand in preferences.excluded_brands:
            if excluded_brand.lower() in doc_brand:
                return False

    # Check color constraints
    if preferences.colors:
        color_match = any(color.lower() in searchable_text for color in preferences.colors)
        if not color_match:
            return False
    
    # Check excluded colors - if any excluded color is found, reject the product
    if preferences.excluded_colors:
        for excluded_color in preferences.excluded_colors:
            if excluded_color.lower() in searchable_text:
                return False
    
    # Check category constraints
    if preferences.categories:
        category_match = False
        for category in preferences.categories:
            category_lower = category.lower()
            
            # Check for exact match first
            if category_lower in searchable_text:
                category_match = True
                break
                
            # Check for variations - remove 's' from plural categories
            if category_lower.endswith('s'):
                singular_category = category_lower[:-1]  # "tote bags" -> "tote bag"
                if singular_category in searchable_text:
                    category_match = True
                    break
                    
                # Also check just the base word without "bag"
                if singular_category.endswith(' bag'):
                    base_word = singular_category.replace(' bag', '')  # "tote bag" -> "tote"
                    if base_word in searchable_text:
                        category_match = True
                        break
                        
                # For categories ending in 's' (like 'clutches'), also check the singular form
                # "clutches" -> "clutch"
                base_singular = category_lower[:-2] if category_lower.endswith('es') else category_lower[:-1]
                if base_singular in searchable_text:
                    category_match = True
                    break
            
            # Handle hyphenated variations (crossbody -> cross-body)
            # Check if we can convert unhyphenated to hyphenated format
            if 'crossbody' in category_lower:
                hyphenated_version = category_lower.replace('crossbody', 'cross-body')
                if hyphenated_version in searchable_text:
                    category_match = True
                    break
                # Also check the reverse (cross-body -> crossbody)
                unhyphenated_version = category_lower.replace('cross-body', 'crossbody')
                if unhyphenated_version in searchable_text:
                    category_match = True
                    break
            
            # Special handling for cross-body variations in document text
            # If searching for 'crossbody bags' and doc contains 'Cross-body Bag'
            if category_lower == 'crossbody bags':
                # Check for hyphenated version with various capitalizations and bag/bags variations
                cross_body_variations = [
                    'cross-body bag', 'cross-body bags', 
                    'Cross-body bag', 'Cross-body Bag', 'Cross-body bags',
                    'Cross-Body bag', 'Cross-Body Bag', 'Cross-Body bags'
                ]
                if any(variation in searchable_text for variation in cross_body_variations):
                    category_match = True
                    break
            
            # Check if category is a single word that might appear in product names
            category_words = category_lower.split()
            if len(category_words) == 1 or (len(category_words) == 2 and category_words[1] == 'bags'):
                base_word = category_words[0]  # "tote" from "tote bags"
                if base_word in searchable_text:
                    category_match = True
                    break
                    
        if not category_match:
            return False
    
    # Check excluded categories - if any excluded category is found, reject the product
    if preferences.excluded_categories:
        for excluded_category in preferences.excluded_categories:
            excluded_category_lower = excluded_category.lower()
            
            # Use the same matching logic as category matching for exclusions
            if excluded_category_lower in searchable_text:
                return False
                
            # Check singular forms
            if excluded_category_lower.endswith('s'):
                singular_excluded = excluded_category_lower[:-1]
                if singular_excluded in searchable_text:
                    return False
                    
            # Handle hyphenated variations for exclusions too
            if 'crossbody' in excluded_category_lower:
                hyphenated_version = excluded_category_lower.replace('crossbody', 'cross-body')
                if hyphenated_version in searchable_text:
                    return False
    
    return True

def is_relevant_to_shopping(user_input: str) -> bool:
    user_input_lower = user_input.lower()
    
    shopping_terms = [
        'bag', 'handbag', 'purse', 'tote', 'backpack', 'clutch', 'wallet',
        'shopping', 'buy', 'purchase', 'price', 'cost', 'delivery', 'shipping',
        'brand', 'leather', 'canvas', 'product', 'item', 'store'
    ]
    
    common_phrases = ['hi', 'hello', 'thank', 'bye', 'help', 'clear', 'reset']
    
    return any(term in user_input_lower for term in shopping_terms + common_phrases)

# ui/formatters.py
from langchain.schema import Document

class ProductFormatter:
    def __init__(self, data_loader):
        self.data_loader = data_loader
    
    def format_product_doc(self, doc: Document) -> str:
        meta = doc.metadata
        product_url = meta.get("url", "")
        image_url = self.data_loader.url_to_image.get(product_url, "")
        
        # Format price properly
        price = meta.get('price', 'N/A')
        if isinstance(price, (int, float)):
            price_display = f"${price:.2f}"
        else:
            price_display = f"${price}" if price != 'N/A' else 'N/A'
        
        brand = meta.get('brand', 'N/A')
        
        return f"""
        <div style="
            flex: 1;
            min-width: 250px;
            max-width: 300px;
            margin: 10px;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 10px;
            box-shadow: 2px 2px 5px rgba(0,0,0,0.1);
            text-align: center;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            height: 100%;
        ">
            <div style="flex-grow: 1;">
                <h4 style="margin: 0 0 10px 0; font-size: 1em;">{meta.get('name', 'Product')}</h4>
                <p style="margin: 0 0 10px 0;"><strong>Brand:</strong> {brand}<br>
                <strong>Price:</strong> {price_display}</p>
                {f'<img src="{image_url}" style="max-width: 100%; height: auto; margin: 10px auto;">' if image_url else ''}
            </div>
            <div style="margin-top: 10px;">
                👉 <a href="{product_url}" target="_blank">View Product</a>
            </div>
        </div>
        """

# ui/gradio_interface.py
import gradio as gr
import base64
from typing import List, Tuple

class GradioInterface:
    def __init__(self, conversation_workflow, preference_service, formatter):
        self.workflow = conversation_workflow
        self.preference_service = preference_service
        self.formatter = formatter
        self.chat_history_ui = []
    
    def get_base64_image(self, image_path: str) -> str:
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode("utf-8")
    
    def chat_interface(self, user_input: str) -> List[Tuple[str, str]]:
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
        self.chat_history_ui = []
        self.preference_service.clear_preferences()
        self.workflow.clear_memory()
        return []

    def show_current_preferences(self) -> str:
        return f"**Current Preferences:** {self.preference_service.get_summary()}"
    
    def build_ui(self) -> gr.Blocks:
        custom_css = """
        .gradio-container {
            max-width: none !important;
            width: 90vw !important;
            margin: 0 auto !important;
            padding: 0 !important;
        }
        
        .chatbot {
            height: 60vh !important;
            min-height: 400px !important;
            max-height: 700px !important;
            width: 100% !important;
        }
        
        .full-width-banner {
            width: 100vw !important;
            margin-left: calc(-45vw + 50%) !important;
            margin-right: calc(-45vw + 50%) !important;
            background-color: #F15F2E;
            padding: 30px 5vw;
            text-align: center;
            border-radius: 0 0 20px 20px;
            margin-bottom: 20px;
        }
        """

        with gr.Blocks(title="Smart Shopping Assistant", css=custom_css) as demo:
            # Header
            logo_base64 = self.get_base64_image("assets/xponent_logo_white_on_orange.jpg")
            gr.HTML(f"""
            <div style="display:flex; flex-direction:column; align-items:center; 
                 justify-content:center; padding: 20px 0; 
                 background-color:#F15F2E; border-radius: 0 0 12px 12px;">
                <img src="data:image/jpeg;base64,{logo_base64}" alt="Xponent.ai Logo" 
                     style="width: clamp(120px, 6vw, 180px); height: auto; border-radius: 8px; margin-bottom: 15px;" />
                <h1 style="color: white; margin: 10px 0 5px; font-weight: bold; font-size: clamp(1.5rem, 3vw, 2.5rem);">
                    Smart Shopping Assistant
                </h1>
            </div>
            """)

            # Preferences display
            preferences_display = gr.Markdown(self.show_current_preferences(), label="Current Preferences")
            
            # Chatbot
            chatbot = gr.Chatbot(render_markdown=False, elem_classes=["chatbot"])
            
            # Input
            with gr.Row():
                msg = gr.Textbox(
                    placeholder="Try: 'leather bags under $100' or 'show me crossbody bags'",
                    show_label=False,
                    container=True
                )
            
            # Buttons
            with gr.Row():
                send_btn = gr.Button("Send", variant="primary")
                clear_btn = gr.Button("Clear Chat & Preferences")
                prefs_btn = gr.Button("Show Preferences")

            # Event handlers
            def handle_send(user_input):
                if not user_input.strip():
                    return self.chat_history_ui, "", self.show_current_preferences()
                
                result = self.chat_interface(user_input)
                return result, "", self.show_current_preferences()

            # Bind actions
            send_btn.click(fn=handle_send, inputs=msg, outputs=[chatbot, msg, preferences_display])
            msg.submit(fn=handle_send, inputs=msg, outputs=[chatbot, msg, preferences_display])
            clear_btn.click(fn=self.clear_chat, outputs=chatbot)
            clear_btn.click(fn=self.show_current_preferences, outputs=preferences_display)
            prefs_btn.click(fn=self.show_current_preferences, outputs=preferences_display)

        return demo
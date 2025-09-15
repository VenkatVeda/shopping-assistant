# workflows/conversation_flow.py
from langchain.memory import ConversationBufferMemory
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph
from models.state import BotState
from utils.validators import is_relevant_to_shopping

class ConversationWorkflow:
    def __init__(self, preference_service, search_service, azure_service, formatter):
        self.preference_service = preference_service
        self.search_service = search_service
        self.azure_service = azure_service
        self.formatter = formatter
        
        # Memory setup
        self.memory = ConversationBufferMemory(
            memory_key='chat_history',
            return_messages=True,
            output_key='answer'
        )
        
        # Build workflow graph
        self.agent = self._build_workflow()
    
    def _build_workflow(self):
        graph = StateGraph(BotState)
        graph.add_node("process_and_route", self._process_input_and_route)
        graph.add_node("search_or_respond", self._execute_search_or_respond)
        
        graph.set_entry_point("process_and_route")
        graph.add_edge("process_and_route", "search_or_respond")
        graph.set_finish_point("search_or_respond")
        
        return graph.compile()
    
    def _process_input_and_route(self, state: BotState) -> BotState:
        user_input = state["question"]
        
        # Check relevance
        if not is_relevant_to_shopping(user_input):
            state["answer"] = (
                "I'm here to help with shopping-related questions like products, prices, or bags. "
                "Let me know how I can assist you with that!"
            )
            state["should_retrieve"] = False
            return state
        
        # Update preferences
        self.preference_service.update_preferences(user_input)
        
        # Determine if we should search
        state["should_retrieve"] = self.search_service.should_search_products(
            user_input, 
            self.preference_service.current_preferences.has_active_preferences()
        )
        
        return state
    
    def _execute_search_or_respond(self, state: BotState) -> BotState:
        user_question = state["question"]
        
        # Check for preference changes first
        preference_updated = self._handle_preference_update(user_question, state)
        if preference_updated:
            return state
        
        if state["should_retrieve"]:
            self._handle_product_search(user_question, state)
        else:
            self._handle_general_conversation(user_question, state)
        
        # Update memory
        self.memory.chat_memory.add_user_message(state["question"])
        self.memory.chat_memory.add_ai_message(state["answer"])
        
        return state
    
    def _handle_preference_update(self, user_question: str, state: BotState) -> bool:
        """Handle preference updates and immediate product display"""
        try:
            previous_prefs = self.preference_service.current_preferences.to_dict().copy()
            updated_prefs = self.preference_service.update_preferences(user_question)
            
            # Check if preferences actually changed
            preference_updated = any(
                previous_prefs[key] != updated_prefs.to_dict()[key] 
                for key in previous_prefs
            )
            
            if preference_updated:
                docs = self.search_service.search_products(
                    user_question, 
                    self.preference_service.current_preferences
                )
                
                if docs:
                    product_displays = [self.formatter.format_product_doc(doc) for doc in docs]
                    state["answer"] = f"""Updated preferences to: {self.preference_service.get_summary()}
                    
Here are products matching your updated criteria:
<div style="display: flex; flex-wrap: wrap; gap: 20px; justify-content: center;">
{''.join(product_displays)}
</div>"""
                else:
                    state["answer"] = f"""Updated preferences to: {self.preference_service.get_summary()}
                    
I couldn't find any products matching these exact criteria. Try adjusting your preferences."""
                
                return True
                
        except Exception as e:
            print(f"Error updating preferences: {e}")
            
        return False
    
    def _handle_product_search(self, user_question: str, state: BotState):
        """Handle product search requests"""
        try:
            docs = self.search_service.search_products(
                user_question, 
                self.preference_service.current_preferences
            )
            
            if not docs:
                state["answer"] = "I couldn't find any products matching your criteria. Try adjusting your preferences."
            else:
                product_displays = [self.formatter.format_product_doc(doc) for doc in docs]
                state["answer"] = f"""Here are some products that match your criteria, sorted by price (highest to lowest):
<div style="display: flex; flex-wrap: wrap; gap: 20px; justify-content: center;">
{''.join(product_displays)}
</div>"""
                
        except Exception as e:
            print(f"Search error: {e}")
            state["answer"] = "Sorry, I encountered an error while searching. Please try again."
    
    def _handle_general_conversation(self, user_question: str, state: BotState):
        """Handle general conversation requests"""
        if any(phrase in user_question.lower() for phrase in ["clear preferences", "reset preferences", "start over"]):
            self.preference_service.clear_preferences()
            state["answer"] = "Your preferences have been cleared! Feel free to set new ones."
            return
        
        if self.azure_service.conversation_chain:
            try:
                recent_messages = self.memory.chat_memory.messages[-6:] if len(self.memory.chat_memory.messages) > 6 else self.memory.chat_memory.messages
                recent_chat_str = "\n".join([
                    f"{'User' if isinstance(msg, HumanMessage) else 'Assistant'}: {msg.content}"
                    for msg in recent_messages
                ])
                
                result = self.azure_service.conversation_chain.invoke({
                    "preferences": self.preference_service.get_summary(),
                    "recent_chat_history": recent_chat_str,
                    "question": user_question
                })
                state["answer"] = result["text"]
            except Exception as e:
                print(f"LLM error: {e}")
                state["answer"] = "I'm here to help you find bags and accessories! What are you looking for?"
        else:
            state["answer"] = "I'm here to help you find bags and accessories! What are you looking for?"
    
    def process_message(self, user_input: str) -> str:
        """Process a user message and return the response"""
        state = {
            "chat_history": self.memory.chat_memory.messages,
            "question": user_input,
            "answer": "",
            "should_retrieve": False,
        }
        
        try:
            result = self.agent.invoke(state)
            return result["answer"]
        except Exception as e:
            print(f"Error processing message: {e}")
            return "I apologize, but I'm experiencing some technical difficulties. Please try again."
    
    def clear_memory(self):
        """Clear conversation memory"""
        self.memory.clear()
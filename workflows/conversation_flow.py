# workflows/conversation_flow.py
import json
import time
from langchain.memory import ConversationBufferMemory
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph
from langsmith import traceable
from models.state import BotState
from utils.validators import is_relevant_to_shopping

class ConversationWorkflow:
    def __init__(self, preference_service, search_service, azure_service, formatter, session_manager=None):
        self.preference_service = preference_service
        self.search_service = search_service
        self.azure_service = azure_service
        self.formatter = formatter
        self.session_manager = session_manager
        
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
            session_id = state.get("session_id")
            previous_prefs = self.preference_service.current_preferences.to_dict().copy()
            updated_prefs = self.preference_service.update_preferences(user_question)
            
            # Check if preferences actually changed
            preference_updated = any(
                previous_prefs[key] != updated_prefs.to_dict()[key] 
                for key in previous_prefs
            )
            
            if preference_updated:
                # Clear search pagination state when preferences change
                if session_id and self.session_manager:
                    session_data = self.session_manager.get_session(session_id)
                    if session_data:
                        session_data.clear_search_state()
                
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
        """Handle product search requests with pagination support"""
        try:
            session_id = state.get("session_id") or getattr(self, 'current_session_id', None)
            
            # Track preference extraction with Azure service for metrics
            if self.azure_service.preference_chain:
                try:
                    # Get current preferences for the prompt
                    current_prefs_json = json.dumps(self.preference_service.current_preferences.to_dict(), indent=2)
                    
                    # Use Azure service to extract preferences (gets us metrics)
                    result, metrics = self.azure_service.run_with_tracking(
                        self.azure_service.preference_chain,
                        {
                            "user_input": user_question,
                            "previous_prefs": current_prefs_json
                        }
                    )
                    # Store metrics in state
                    state["metrics"] = metrics
                except Exception as e:
                    print(f"Preference extraction error: {e}")
            
            # Get all matching results for pagination
            all_docs = self.search_service.search_all_products(
                user_question, 
                self.preference_service.current_preferences,
                max_results=50  # Get more results for pagination
            )
            
            if not all_docs:
                # Clear session search state
                if session_id and self.session_manager:
                    session_data = self.session_manager.get_session(session_id)
                    if session_data:
                        session_data.clear_search_state()
                
                state["answer"] = "I couldn't find any products matching your criteria. Try adjusting your preferences."
            else:
                # Show first batch (6 products)
                batch_size = 6
                first_batch = all_docs[:batch_size]
                product_displays = [self.formatter.format_product_doc(doc) for doc in first_batch]
                
                # Update session state for pagination
                if session_id and self.session_manager:
                    session_data = self.session_manager.get_session(session_id)
                    if session_data:
                        session_data.update_search_state(
                            user_question, 
                            self.preference_service.current_preferences,
                            all_docs, 
                            batch_size
                        )
                
                # Add show more indicator if there are more results
                show_more_indicator = ""
                if len(all_docs) > batch_size:
                    remaining_count = len(all_docs) - batch_size
                    show_more_indicator = f"""
<div style="text-align: center; margin: 20px 0; padding: 10px; background-color: #f0f0f0; border-radius: 5px;">
    <p style="margin: 0; color: #666;">📦 {remaining_count} more products available. Click "Show More Results" button below to see them.</p>
</div>"""
                
                state["answer"] = f"""Here are {len(first_batch)} products that match your criteria, sorted by price (highest to lowest):
<div style="display: flex; flex-wrap: wrap; gap: 20px; justify-content: center;">
{''.join(product_displays)}
</div>
{show_more_indicator}"""
                
        except Exception as e:
            print(f"Search error: {e}")
            state["answer"] = "Sorry, I encountered an error while searching. Please try again."
    
    def _handle_general_conversation(self, user_question: str, state: BotState):
        """Handle general conversation requests"""
        if any(phrase in user_question.lower() for phrase in ["clear preferences", "reset preferences", "start over"]):
            session_id = state.get("session_id")
            
            # Clear preferences
            self.preference_service.clear_preferences()
            
            # Clear search pagination state
            if session_id and self.session_manager:
                session_data = self.session_manager.get_session(session_id)
                if session_data:
                    session_data.clear_search_state()
            
            state["answer"] = "Your preferences have been cleared! Feel free to set new ones."
            return
        
        if self.azure_service.conversation_chain:
            try:
                recent_messages = self.memory.chat_memory.messages[-6:] if len(self.memory.chat_memory.messages) > 6 else self.memory.chat_memory.messages
                recent_chat_str = "\n".join([
                    f"{'User' if isinstance(msg, HumanMessage) else 'Assistant'}: {msg.content}"
                    for msg in recent_messages
                ])
                
                # Use tracking method to get both LangSmith tracing AND local metrics
                result, metrics = self.azure_service.run_with_tracking(
                    self.azure_service.conversation_chain,
                    {
                        "preferences": self.preference_service.get_summary(),
                        "recent_chat_history": recent_chat_str,
                        "question": user_question
                    }
                )
                
                if result:
                    # Handle both string and dict responses
                    if isinstance(result, dict) and 'text' in result:
                        state["answer"] = result["text"]
                    else:
                        state["answer"] = str(result)
                    # Store metrics in state for workflow result
                    state["metrics"] = metrics
                else:
                    state["answer"] = "I'm here to help you find bags and accessories! What are you looking for?"
                    
            except Exception as e:
                print(f"LLM error: {e}")
                state["answer"] = "I'm here to help you find bags and accessories! What are you looking for?"
        else:
            state["answer"] = "I'm here to help you find bags and accessories! What are you looking for?"
    
    @traceable(name="process_user_message", project_name="shopping-assistant")
    def process_message(self, user_input: str, session_id: str = None) -> tuple:
        """Process a user message and return the response with metrics (LangSmith + local tracking)"""
        # Store session_id as instance variable for use in handlers
        self.current_session_id = session_id
        
        # Check if this is a "show more" request
        if self._is_show_more_request(user_input):
            return self._handle_show_more_request(session_id), None

        state = {
            "chat_history": self.memory.chat_memory.messages,
            "question": user_input,
            "answer": "",
            "should_retrieve": False,
            "session_id": session_id,
            "metrics": None
        }

        try:
            result = self.agent.invoke(state)
            
            # Get metrics from state or from Azure service
            metrics = result.get("metrics")
            if not metrics and hasattr(self.azure_service, 'last_metrics') and self.azure_service.last_metrics:
                metrics = self.azure_service.last_metrics
                
            return result["answer"], metrics
        except Exception as e:
            print(f"Error processing message: {e}")
            return "I apologize, but I'm experiencing some technical difficulties. Please try again.", None

    def clear_memory(self):
        """Clear conversation memory"""
        self.memory.clear()
    
    def _is_show_more_request(self, user_input: str) -> bool:
        """Check if user is requesting to show more results"""
        show_more_patterns = [
            "show more", "more results", "more options", "see more", 
            "show me more", "load more", "more products", "next",
            "continue", "more items", "additional results"
        ]
        user_input_lower = user_input.lower().strip()
        return any(pattern in user_input_lower for pattern in show_more_patterns)
    
    def _handle_show_more_request(self, session_id: str = None) -> str:
        """Handle request to show more results"""
        if not session_id or not hasattr(self, 'session_manager'):
            return "I don't have any additional results to show."
        
        # Get session data
        session_data = self.session_manager.get_session(session_id)
        if not session_data or not session_data.can_show_more():
            return "I don't have any additional results to show. Try searching for products first."
        
        # Get next batch of results
        next_results = session_data.get_next_results()
        if not next_results:
            return "No more results available."
        
        # Format the results
        product_displays = [self.formatter.format_product_doc(doc) for doc in next_results]
        
        # Create response with indicator if there are still more results
        show_more_indicator = ""
        if session_data.can_show_more():
            remaining_count = len(session_data.last_search_results) - session_data.displayed_count
            show_more_indicator = f"""
<div style="text-align: center; margin: 20px 0; padding: 10px; background-color: #f0f0f0; border-radius: 5px;">
    <p style="margin: 0; color: #666;">📦 {remaining_count} more products available. Click "Show More Results" button below to see them.</p>
</div>"""
        
        return f"""Here are {len(next_results)} more products:
<div style="display: flex; flex-wrap: wrap; gap: 20px; justify-content: center;">
{''.join(product_displays)}
</div>
{show_more_indicator}"""
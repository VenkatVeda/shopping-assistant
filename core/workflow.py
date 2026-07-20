"""
LangGraph Workflow for Shopping Assistant with Vector Search
Architecture: Input Guardrail -> Intent Classifier -> Product Search -> RAG Enhancement
"""

import os
import sys
import time
from typing import TypedDict, List, Optional
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from databricks_langchain import DatabricksEmbeddings, ChatDatabricks
from .models import SearchPreferences
from .memory_manager import MemoryManager
from .nodes import ResultValidator, Reranker, ResponseGenerator
from .guardrails import OutputGuardrail
from .prompt_loader import load_prompt
from .performance import track_time, get_tracker
from .observability import NodeTracer, RequestTrace, metrics_store
from .audit_logger import log_guardrail
from .evals import EvalRunner
from .semantic_cache import build_cache

class GraphState(TypedDict):
    """State definition for the graph"""
    query: str
    intent: str
    user_id: Optional[str]  # User identifier for personalization
    preferences: Optional[SearchPreferences]
    new_preferences: Optional[SearchPreferences]  # Current query preferences (before merge)
    previous_preferences: Optional[SearchPreferences]  # Previous state preferences
    results: List[dict]
    error: Optional[str]
    history: List[str]  # Recent conversation turns
    summary: Optional[str]  # Summarized older context
    
    # Clarification Fields
    needs_clarification: Optional[bool]  # Whether to ask clarifying questions
    clarification_question: Optional[str]  # The question to ask
    clarification_asked_last_turn: Optional[bool]  # Flag to prevent re-asking same clarification
    user_action_choice: Optional[str]  # User's choice: START_FRESH, MERGE, or REPLACE
    
    # Personalization Fields
    personalization_context: Optional[str]  # LLM-generated personalized context
    personalization_session: Optional[dict]  # Session state for personalization engine
    
    # RAG Enhancement Fields
    generated_response: Optional[str]  # LLM-generated conversational response
    reranked_results: Optional[List[dict]]  # Products after reranking
    result_count_status: Optional[str]  # "zero", "optimal", "good", "too_many"
    relaxation_level: Optional[int]  # 0-4, tracks constraint relaxation
    relaxation_message: Optional[str]  # User-friendly message about relaxation
    
    # Output Guardrail Fields
    guardrail_status: Optional[str]  # "pass", "warning", "fail"
    guardrail_issues: Optional[List[str]]  # Issues found by guardrail
    safe_response: Optional[str]  # Guardrail-validated safe response
    
    # Product Discussion Fields
    selected_product_id: Optional[str]  # ID of product user wants to discuss
    product_discussion_mode: Optional[bool]  # Whether in single-product discussion mode
    product_context: Optional[dict]  # Details of the selected product
    last_discussed_product: Optional[dict]  # Last product discussed (id, name, brand)
    trace_id: Optional[str]  # Unique ID linking all audit logs for this request

class ShoppingAssistantWorkflow:
    def __init__(self):
        print("Initializing Shopping Assistant Workflow...")
        
        # Initialize components
        from .pref_intent_normalizer import IntentClassifier
        self.intent_classifier = IntentClassifier()
        
        # Initialize Databricks Embeddings
        self.embeddings = DatabricksEmbeddings(
            endpoint=os.getenv("DATABRICKS_EMBEDDING_ENDPOINT", "databricks-bge-large-en")
        )
        
        # Initialize Databricks Chat Model for RAG components
        chat_endpoint = os.getenv("DATABRICKS_CHAT_ENDPOINT", "databricks-meta-llama-3-1-8b-instruct")
        self.chat_model = ChatDatabricks(
            endpoint=chat_endpoint,
            temperature=0.7,
            max_tokens=800
        )
        print(f"✓ Initialized Databricks Chat Model: {chat_endpoint}")
        
        # Initialize Vector Search Client
        # Check if running in Databricks Apps (where MCP subprocess spawning doesn't work)
        in_databricks_app = os.getenv("DATABRICKS_RUNTIME_VERSION") or os.path.exists("/databricks/spark")
        use_mcp = os.getenv("USE_MCP_VECTOR_SEARCH", "true").lower() == "true" and not in_databricks_app
        
        if use_mcp:
            try:
                # Use MCP client for local development
                from .mcp_client import VectorSearchClient
                self.vector_client = VectorSearchClient()
                stats = self.vector_client.get_stats()
                print(f"✓ Connected to Vector Search via MCP: {stats.get('index_name', 'unknown')} ({stats.get('provider', 'unknown')})")
            except Exception as e:
                print(f"⚠ MCP connection failed: {e}")
                print("⚠ Falling back to direct adapter mode...")
                use_mcp = False
        
        if not use_mcp:
            # Use direct adapter (for Databricks Apps or when MCP fails)
            try:
                from .vector_store.direct_client import DirectVectorClient
                self.vector_client = DirectVectorClient()
                stats = self.vector_client.get_stats()
                print(f"✓ Connected to Vector Search (Direct): {stats.get('index_name', 'unknown')}")
            except Exception as e:
                print(f"⚠ Direct adapter failed: {e}")
                # Last resort: Legacy Pinecone mode
                from pinecone import Pinecone
                pinecone_api_key = os.getenv("PINECONE_API_KEY")
                if not pinecone_api_key:
                    raise ValueError("PINECONE_API_KEY environment variable is required")
                pc = Pinecone(api_key=pinecone_api_key)
                pinecone_index_name = os.getenv("PINECONE_INDEX_NAME", "bags-index")
                self.vector_client = pc.Index(pinecone_index_name)
                print(f"✓ Connected to Pinecone index (legacy mode): {pinecone_index_name}")
        
        # Initialize Memory
        self.memory = MemorySaver()
        self.memory_manager = MemoryManager(chat_endpoint=chat_endpoint)
        
        # Initialize RAG Nodes
        self.result_validator = ResultValidator()
        # Constraint relaxation is handled directly in constraint_relaxer_node using rag_utils
        self.reranker = Reranker(self.chat_model)
        self.response_generator = ResponseGenerator(self.chat_model)
        print("✓ Initialized RAG components: Validator, Reranker, Response Generator")
        
        # Initialize Output Guardrail
        self.output_guardrail = OutputGuardrail(self.chat_model)
        print("✓ Initialized Output Guardrail: Content Safety, Factual Validation")
        
        # Initialize Personalization Engine (if enabled)
        self.personalization_enabled = os.getenv("ENABLE_PERSONALIZATION", "false").lower() == "true"
        if self.personalization_enabled:
            try:
                from databricks.sdk import WorkspaceClient
                from .personalization.engine import PersonalizationEngine, RateLimitExceeded
                from .personalization.extractor import PreferenceExtractor
                from .personalization.storage import ProfileStorage
                
                # Initialize Databricks client for personalization
                workspace_client = WorkspaceClient()
                warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID")
                
                # Initialize preference extractor
                self.preference_extractor = PreferenceExtractor(
                    llm_client=workspace_client,
                    model_name=chat_endpoint
                )
                
                # Initialize personalization engine
                self.personalization_engine = PersonalizationEngine(
                    self.preference_extractor,
                    enable_rate_limiting=True
                )
                
                # Initialize profile storage with SQL Warehouse for persistence
                self.profile_storage = ProfileStorage(
                    workspace_client=workspace_client,
                    warehouse_id=warehouse_id,
                    table_name="sandbox.venkat.user_profiles"
                )
                
                # Determine storage mode
                if workspace_client and warehouse_id:
                    storage_mode = "SQL Warehouse (persistent)"
                else:
                    storage_mode = "in-memory (session only)"
                
                print(f"✓ Initialized Personalization Engine with Profile Learning")
                print(f"ℹ Profile Storage: {storage_mode}")
            except Exception as e:
                print(f"⚠ Personalization disabled due to error: {e}")
                self.personalization_enabled = False
        else:
            print("ℹ Personalization Engine: Disabled (set ENABLE_PERSONALIZATION=true to enable)")
        
        # Observability — node tracer and eval runner
        self.tracer = NodeTracer()
        self.eval_runner = EvalRunner(chat_model=self.chat_model)
        print("✓ Observability: NodeTracer and EvalRunner initialised")

        # Semantic cache — share the workspace_client already created for personalisation
        _wc = getattr(self, '_workspace_client_for_cache', None)
        _wh = os.getenv("DATABRICKS_WAREHOUSE_ID")
        if self.personalization_enabled:
            # reuse the workspace client already initialised above
            try:
                from databricks.sdk import WorkspaceClient as _WC
                _wc = _WC()
            except Exception:
                pass
        self.semantic_cache = build_cache(workspace_client=_wc, warehouse_id=_wh)
        print(f"✓ Semantic Cache: {self.semantic_cache.stats().get('backend', 'unknown')} backend")

        # ── Audit Trail ───────────────────────────────────────────────────────
        try:
            from audit_wrapper import AuditWrapper
            self.audit_wrapper = AuditWrapper(
                catalog = os.getenv("AUDIT_CATALOG", "shopping_assistant"),
            )
        except Exception as _e:
            print(f"[AUDIT] AuditWrapper init failed (non-blocking): {_e}")
            self.audit_wrapper = None
        # ─────────────────────────────────────────────────────────────────────

        # Build Graph
        self.app = self._build_graph()
        print("✓ Workflow Ready with Pinecone + RAG Enhancement!")

    def _build_graph(self):
        """Build the LangGraph workflow with RAG enhancement and product discussion"""
        workflow = StateGraph(GraphState)
        
        # Add Nodes — each wrapped with NodeTracer for MLflow spans + metrics
        t = self.tracer.wrap  # shorthand
        workflow.add_node("input_guardrail",    t("input_guardrail",    self.input_guardrail))
        workflow.add_node("intent_classifier",  t("intent_classifier",  self.intent_classifier_node))
        workflow.add_node("clarification",           t("clarification",           self.clarification_node))
        workflow.add_node("personalization",         t("personalization",         self.personalization_node))
        workflow.add_node("product_search",          t("product_search",          self.product_search_node))
        workflow.add_node("result_validator",        t("result_validator",        self.result_validator_node))
        workflow.add_node("constraint_relaxer",      t("constraint_relaxer",      self.constraint_relaxer_node))
        workflow.add_node("reranker",                t("reranker",                self.reranker_node))
        workflow.add_node("response_generator",      t("response_generator",      self.response_generator_node))
        workflow.add_node("output_guardrail",        t("output_guardrail",        self.output_guardrail_node))
        
        # Set Entry Point
        workflow.set_entry_point("input_guardrail")
        
        # Add Edges
        workflow.add_conditional_edges(
            "input_guardrail",
            self.check_guardrail,
            {
                "pass": "intent_classifier",
                "fail": END
            }
        )

        workflow.add_conditional_edges(
            "intent_classifier",
            self.check_intent,
            {
                "shopping":          "personalization",     # happy path — no conflict
                "shopping_conflict": "clarification",       # pause: ask START_FRESH/MERGE/REPLACE
                "chat":              "response_generator",  # LLM chat reply → guardrail → END
                "product_detail":    "response_generator",  # skip search pipeline entirely
            }
        )

        # Clarification is a deliberate pause node — it always stops for user input.
        # The "continue" branch is a safety valve; in normal operation the node
        # always sets needs_clarification=True before returning.
        workflow.add_conditional_edges(
            "clarification",
            self.check_clarification_needed,
            {
                "clarify":   END,              # wait for user
                "continue":  "personalization" # safety: if somehow needs_clarification=False
            }
        )
        
        workflow.add_edge("personalization", "product_search")
        
        # RAG Enhancement Flow
        workflow.add_edge("product_search", "result_validator")
        
        workflow.add_conditional_edges(
            "result_validator",
            self.check_result_count,
            {
                "zero":     "constraint_relaxer",  # 0 results   → relax filters and re-search
                "optimal":  "reranker",             # 1–10        → rerank
                "good":     "reranker",             # 11–50       → rerank
                "too_many": "clarification",        # 50+         → ask user to narrow down
            }
        )
        
        # Constraint relaxer: if max level reached, skip straight to reranker; otherwise re-search
        workflow.add_conditional_edges(
            "constraint_relaxer",
            self.check_relaxation_done,
            {
                "continue": "product_search",
                "done": "reranker"
            }
        )
        
        workflow.add_edge("reranker", "response_generator")
        workflow.add_edge("response_generator", "output_guardrail")
        workflow.add_edge("output_guardrail", END)
        
        # Compile with memory
        app = workflow.compile(checkpointer=self.memory)
        # Set recursion limit to prevent infinite loops in constraint relaxation
        app.recursion_limit = 50
        return app

    # --- Nodes ---
    
    def input_guardrail(self, state: GraphState):
        """Node 1: Input Guardrail with Safety & Relevance Checks + Memory Management"""
        _t0 = time.time()

        with track_time("node_input_guardrail"):
            query = state.get("query", "").strip()
            history = state.get("history", [])
            summary = state.get("summary", "")

            # Audit context — links this log row to the request via session_id
            # (trace_id is set only after graph completes, so we use session_id
            # as the in-graph correlation key)
            _audit_ctx = {
                "request_id": state.get("session_id", ""),
                "user_id":    state.get("user_id", "anon") or "anon",
                "session_id": str(state.get("session_id", "")),
            }

            if not query:
                return {"error": "Please ask me something! I'm here to help you find bags, wallets, and accessories.", 
                        "history": history, "summary": summary}
            
            # Length check
            if len(query) > 500:
                log_guardrail(
                    guardrail_type="input",
                    status="fail",
                    issues=["Query too long — exceeds 500 characters"],
                    corrections_made=False,
                    latency_ms=(time.time() - _t0) * 1000,
                    query_preview=query[:120],
                    **_audit_ctx,
                )
                return {"error": "That query is too long. Could you please rephrase it more concisely?", 
                        "history": history, "summary": summary}
            
            # Skip safety check for very short/common queries to save tokens
            skip_safety_check = (
                len(query) < 15 or 
                query.lower() in ['hi', 'hello', 'hey', 'thanks', 'thank you', 'yes', 'no', 'ok', 'okay']
            )
            
            if not skip_safety_check:
                # Content safety & relevance check
                with track_time("input_safety_check"):
                    safety_result = self._check_input_safety(query)
                
                if safety_result["status"] == "UNSAFE":
                    print(f"[INPUT GUARDRAIL] Blocked {safety_result['category']}: {safety_result['reason']}")
                    log_guardrail(
                        guardrail_type="input",
                        status="fail",
                        issues=[f"{safety_result['category']}: {safety_result['reason']}"],
                        corrections_made=False,
                        latency_ms=(time.time() - _t0) * 1000,
                        query_preview=query[:120],
                        **_audit_ctx,
                    )
                    return {
                        "error": safety_result["decline_message"],
                        "history": history,
                        "summary": summary
                    }
                
                print(f"[INPUT GUARDRAIL] ✓ Query passed safety check")
            
            # Add to history
            history.append(f"User: {query}")
            
            # Manage memory (summarize if needed)
            with track_time("memory_management"):
                memory_result = self.memory_manager.manage_memory(history, summary)
            
            if memory_result["was_summarized"]:
                print(f"[MEMORY] Summarization triggered - kept last {len(memory_result['history'])} turns")

            # Log pass — every pass must be logged as proof the check ran
            log_guardrail(
                guardrail_type="input",
                status="pass",
                issues=[],
                corrections_made=False,
                latency_ms=(time.time() - _t0) * 1000,
                query_preview=query[:120],
                **_audit_ctx,
            )

            # ── AUDIT: node execution log ──
            _node_latency = (time.time() - _t0) * 1000
            if self.audit_wrapper:
                self.audit_wrapper.log_node_execution(
                    trace_id   = getattr(self, "_current_trace_id", ""),
                    node_name  = "input_guardrail",
                    status     = "success",
                    node_input = query[:500],
                    node_output= "pass",
                    latency_ms = _node_latency,
                    node_type  = "guardrail",
                    node_order = 1,
                )

            return {
                "query": query,
                "error": None,
                "history": memory_result["history"],
                "summary": memory_result["summary"]
            }

    def intent_classifier_node(self, state: GraphState):
        """Node 2: Intent Classifier with Preference Merging and Product-Detail Detection.

        Routing responsibilities (replaces the former product_selection node):
          • Explicit (ID: xxx) in query  → intent = "product_detail"  (UI button path)
          • Follow-up while in discussion mode, LLM says chat → intent = "product_detail"
          • User switches to new search from discussion mode → clears discussion state, falls
            through to normal shopping / chat classification
          • Everything else → normal LLM classification (shopping / preference_update / chat)
        """
        with track_time("node_intent_classifier"):
            query = state["query"]
            history = state.get("history", [])
            summary = state.get("summary", "")
            previous_preferences = state.get("preferences")
            clarification_asked_last_turn = state.get("clarification_asked_last_turn", False)

            # ── 1. Explicit product ID injected by the UI "Ask about this" button ──────
            #    Format: "Tell me more about 'Name' (ID: abc123) by Brand"
            #    This is a deterministic check — no LLM call needed.
            product_result = self._detect_product_intent(state)
            if product_result == "exit_discussion":
                # User explicitly started a new search while in discussion mode.
                # Clear discussion state and fall through to normal classification below.
                state = {
                    **state,
                    "product_discussion_mode": False,
                    "selected_product_id": None,
                    "product_context": None,
                }
            elif product_result is not None:
                # Explicit product selection or confirmed follow-up in discussion mode.
                return product_result
            
            # Format context for intent classifier
            context = self.memory_manager.format_context_for_llm(history, summary)
            
            # Check if user is responding to clarification with a choice (1, 2, 3, or keywords)
            if clarification_asked_last_turn:
                action = self._detect_clarification_response(query, previous_preferences)
                free_text_response = False
                # If user typed free text that doesn't match 1/2/3 or keywords, treat as REPLACE
                # (they're expressing a new preference, implicitly wanting to update the old one)
                if not action and len(query.split()) >= 2:
                    print(f"[WORKFLOW] Clarification response unrecognised, defaulting to REPLACE for: {query}")
                    action = "REPLACE"
                    free_text_response = True
                if action:
                    print(f"[WORKFLOW] User chose action: {action} - bypassing intent classifier")

                    # For free-text responses, extract preferences from the current query (user expressed new intent inline)
                    # For numeric/keyword responses, use the saved pending preferences
                    if free_text_response:
                        extracted = self.intent_classifier.process_query(query, context)
                        pending_new_preferences = extracted.preferences
                        print(f"[WORKFLOW] Free-text REPLACE: extracted preferences from query: {pending_new_preferences}")
                    else:
                        # Retrieve the pending new_preferences from state (saved when clarification was asked)
                        pending_new_preferences = state.get("new_preferences")
                    
                    # If no pending preferences (shouldn't happen, but safety check)
                    if not pending_new_preferences:
                        print("[WORKFLOW] Warning: No pending new_preferences, extracting from history")
                        # Try to extract from history
                        if history and len(history) >= 2:
                            for i in range(len(history) - 2, -1, -1):
                                if history[i].startswith("User: "):
                                    potential_query = history[i].replace("User: ", "").strip()
                                    if potential_query not in ["1", "2", "3", "1️⃣", "2️⃣", "3️⃣", "one", "two", "three", "option 1", "option 2", "option 3", "first", "second", "third", "start fresh", "replace", "replace the filter", "let make it a new fresh start"]:
                                        # Extract preferences from this query
                                        temp_query = self.intent_classifier.process_query(potential_query, "")
                                        pending_new_preferences = temp_query.preferences
                                        break
                    
                    # Retrieve the original query that triggered clarification
                    # For free-text responses the current query IS the intent; for option picks, recover from history
                    original_query = query
                    if not free_text_response and history and len(history) >= 2:
                        for i in range(len(history) - 2, -1, -1):
                            if history[i].startswith("User: "):
                                potential_query = history[i].replace("User: ", "").strip()
                                # Make sure it's not another choice response
                                if potential_query not in ["1", "2", "3", "1️⃣", "2️⃣", "3️⃣", "one", "two", "three", "option 1", "option 2", "option 3", "first", "second", "third", "start fresh", "replace", "replace the filter", "let make it a new fresh start"]:
                                    original_query = potential_query
                                    print(f"[WORKFLOW] Restored original query: {original_query}")
                                    break
                    
                    # Apply the action immediately based on choice
                    if action == "START_FRESH":
                        print(f"[WORKFLOW] START_FRESH - using only new preferences: {pending_new_preferences}")
                        # If new preferences couldn't be recovered, fall back to an empty search rather than crashing
                        final_preferences = pending_new_preferences or SearchPreferences()
                    elif action == "REPLACE":
                        print(f"[WORKFLOW] REPLACE - updating colors/categories only")
                        final_preferences = self._merge_preferences(previous_preferences, pending_new_preferences, "preference_update")
                    else:  # MERGE
                        print(f"[WORKFLOW] MERGE - combining all preferences")
                        final_preferences = self._merge_preferences(previous_preferences, pending_new_preferences, "shopping")
                    
                    # Return with the action APPLIED - no need to go through clarification node again
                    return {
                        "intent": "shopping",
                        "query": original_query,
                        "preferences": final_preferences,  # Already applied the choice
                        "new_preferences": None,  # Clear to prevent re-triggering
                        "previous_preferences": final_preferences,  # Update so next query doesn't conflict
                        "user_action_choice": None,  # Clear since we already applied it
                        "clarification_asked_last_turn": False  # Clear flag
                    }
            
            # Process query with context
            with track_time("intent_classification"):
                user_query = self.intent_classifier.process_query(query, context)
            
            print(f"[WORKFLOW] Intent: {user_query.intent}")
        print(f"[WORKFLOW] Preferences: {user_query.preferences}")
        
        # Store new preferences before merging (for clarification node to compare)
        new_preferences = user_query.preferences
        
        # Determine merge mode
        merge_mode = user_query.intent
        
        # Merge preferences with previous state
        merged_preferences = self._merge_preferences(
            previous_preferences, 
            user_query.preferences,
            merge_mode
        )
        
        print(f"[WORKFLOW] Merged preferences: {merged_preferences}")

        # If the LLM classified this as a new shopping query while discussion mode was
        # active, clear the discussion state so the search pipeline starts fresh.
        clear_discussion = (
            state.get("product_discussion_mode")
            and user_query.intent in ("shopping", "preference_update")
        )

        return {
            "intent":               user_query.intent,
            "preferences":          merged_preferences,
            "new_preferences":      new_preferences,
            "previous_preferences": previous_preferences,
            **(
                {
                    "product_discussion_mode": False,
                    "selected_product_id":     None,
                    "product_context":         None,
                    "personalization_context": None,
                }
                if clear_discussion else {}
            ),
        }
    
    def _detect_clarification_response(self, query: str, previous_preferences) -> str:
        """Detect if user is responding with START_FRESH, MERGE, or REPLACE choice"""
        query_lower = query.lower().strip()
        
        # Check for numeric choices
        if query_lower in ["1", "1️⃣", "one", "option 1", "first"]:
            return "START_FRESH"
        elif query_lower in ["2", "2️⃣", "two", "option 2", "second"]:
            return "MERGE"
        elif query_lower in ["3", "3️⃣", "three", "option 3", "third"]:
            return "REPLACE"
        
        # Check for keyword-based choices — use whole-word matching to avoid false positives on shopping queries
        import re as _re
        def _has_keyword(text, keywords):
            for kw in keywords:
                if _re.search(r'\b' + _re.escape(kw) + r'\b', text):
                    return True
            return False

        start_fresh_keywords = ["start fresh", "new search", "start over", "clear all", "reset", "forget everything", "fresh start"]
        merge_keywords = ["merge", "combine", "both", "keep both", "include both", "add both"]
        replace_keywords = ["replace", "swap", "switch", "instead of", "not the previous"]

        if _has_keyword(query_lower, start_fresh_keywords):
            return "START_FRESH"
        if _has_keyword(query_lower, merge_keywords):
            return "MERGE"
        if _has_keyword(query_lower, replace_keywords):
            return "REPLACE"
        
        # No clear choice detected
        return None

    def product_search_node(self, state: GraphState):
        """Node 3: Product Search using Vector Search (MCP)"""
        with track_time("node_product_search"):
            preferences = state["preferences"]
            query = state["query"]
            
            try:
                # Build search query embedding
                with track_time("embedding_generation"):
                    search_query = self._build_search_query(preferences)
                    query_embedding = self.embeddings.embed_query(search_query)
                
                # Build metadata filters
                filters = self._build_pinecone_filters(preferences)
                
                print(f"[SEARCH] Query: {search_query}")
                print(f"[SEARCH] Filters: {filters}")
                print(f"[SEARCH] Vector dim: {len(query_embedding)}")
                
                # Search via semantic cache → vector index
                with track_time("vector_search"):
                    try:
                        print("[SEARCH] Calling vector_client.search()...", file=sys.stderr)

                        _audit_ctx = {
                            "request_id": state.get("session_id", ""),
                            "user_id": state.get("user_id", "") or "anon",
                            "session_id": str(state.get("session_id", "")),
                        }

                        def _raw_search(vec, k, f):
                            return self.vector_client.search(
                                vector=vec, top_k=k, filters=f,
                                audit_context=_audit_ctx,
                            )

                        if filters:
                            search_results, cache_hit = self.semantic_cache.search(
                                query_text   = search_query,
                                query_vector = query_embedding,
                                filters      = filters,
                                top_k        = 50,
                                search_fn    = _raw_search,
                            )
                            print(
                                f"[SEARCH] Got {len(search_results)} results with filters "
                                f"({'cache' if cache_hit else 'index'})",
                                file=sys.stderr,
                            )
                            # Zero results with filters → let result_validator route to
                            # constraint_relaxer_node, which handles progressive relaxation.
                        else:
                            search_results, cache_hit = self.semantic_cache.search(
                                query_text   = search_query,
                                query_vector = query_embedding,
                                filters      = None,
                                top_k        = 50,
                                search_fn    = _raw_search,
                            )
                            print(
                                f"[SEARCH] Got {len(search_results)} results (no filters, "
                                f"{'cache' if cache_hit else 'index'})",
                                file=sys.stderr,
                            )
                    except Exception as search_error:
                        print(f"[SEARCH ERROR] Vector search failed: {search_error}", file=sys.stderr)
                        import traceback
                        traceback.print_exc(file=sys.stderr)
                        raise
                
                # Format results with deduplication
                results = []
                seen_ids = set()  # Track unique product IDs
                filtered_by_price = 0
                
                print(f"[PRICE FILTER] price_min={preferences.price_min}, price_max={preferences.price_max}", file=sys.stderr)
                
                for match in search_results:
                    product_id = match.get('id')
                    
                    # Skip duplicates
                    if product_id in seen_ids:
                        continue
                    
                    metadata = match.get('metadata', {})
                    
                    # Post-filter by price
                    if preferences.price_min is not None or preferences.price_max is not None:
                        try:
                            price_value = metadata.get('price') or metadata.get('price_from')
                            
                            # Handle different price formats
                            if isinstance(price_value, (int, float)):
                                price = float(price_value)
                            elif isinstance(price_value, str):
                                # Remove currency symbols and convert to float
                                price = float(price_value.replace('$', '').replace(',', '').strip())
                            else:
                                # If no price available, include the product (don't skip)
                                price = None
                            
                            # Check price range only if price is available
                            if price is not None:
                                product_name = metadata.get('name', 'Unknown')
                                # Allow a $10 tolerance below price_min to avoid excluding products like $399.95 when min is $400
                                price_min_tolerance = 10.0
                                effective_price_min = preferences.price_min - price_min_tolerance if preferences.price_min else None
                                
                                if effective_price_min and price < effective_price_min:
                                    print(f"[PRICE FILTER] Filtered out: {product_name} (${price} < ${effective_price_min})", file=sys.stderr)
                                    filtered_by_price += 1
                                    continue
                                if preferences.price_max and price > preferences.price_max:
                                    print(f"[PRICE FILTER] Filtered out: {product_name} (${price} > ${preferences.price_max})", file=sys.stderr)
                                    filtered_by_price += 1
                                    continue
                                print(f"[PRICE FILTER] Keeping: {product_name} (${price})", file=sys.stderr)
                        except (ValueError, AttributeError, TypeError) as e:
                            # If price conversion fails, include the product (don't skip)
                            print(f"[SEARCH] Price conversion warning for {product_id}: {e}", file=sys.stderr)
                            pass
                    
                    # Add to results and mark as seen
                    results.append({
                        'id': product_id,
                        'score': match.get('score', 0),
                        'metadata': metadata
                    })
                    seen_ids.add(product_id)
                
                print(f"[SEARCH] Found {len(results)} unique products (after post-filtering and deduplication)")
                print(f"[PRICE FILTER] Total filtered by price: {filtered_by_price}/{len(search_results)}", file=sys.stderr)

                # ── AUDIT: node execution log ──
                if self.audit_wrapper:
                    self.audit_wrapper.log_node_execution(
                        trace_id   = getattr(self, "_current_trace_id", ""),
                        node_name  = "product_search_node",
                        status     = "success",
                        node_input = str(preferences)[:500] if preferences else "",
                        node_output= f"{len(results)} products found",
                        node_type  = "retriever",
                        node_order = 3,
                    )
                return {"results": results}
                
            except Exception as e:
                print(f"[ERROR] Vector search failed: {e}")
                # ── AUDIT: node execution log (error path) ──
                if self.audit_wrapper:
                    self.audit_wrapper.log_node_execution(
                        trace_id   = getattr(self, "_current_trace_id", ""),
                        node_name  = "product_search_node",
                        status     = "error",
                        error_msg  = str(e)[:500],
                        node_type  = "retriever",
                        node_order = 3,
                    )
                return {"results": [], "error": str(e)}

    # --- Conditional Logic ---
    
    def check_guardrail(self, state: GraphState):
        """Edge: Check guardrail result"""
        if state.get("error"):
            return "fail"
        return "pass"

    def check_intent(self, state: GraphState):
        """Edge: Route after intent classification.

        product_detail    — user selected / is following up on a specific product
        shopping          — new search, no preference conflict → go straight to personalization
        shopping_conflict — new search with a detected preference conflict → pause at clarification
        chat              — general conversation, no search needed

        Conflict detection is deterministic (no LLM) so it adds zero latency to the
        happy path. The clarification node is only visited when genuinely needed.
        """
        intent = state.get("intent")
        if intent == "product_detail":
            return "product_detail"
        if intent in ("shopping", "preference_update"):
            prev = state.get("preferences")
            new  = state.get("new_preferences")
            if prev and new and self._detect_preference_conflicts(prev, new):
                return "shopping_conflict"
            return "shopping"
        return "chat"
    
    def check_result_count(self, state: GraphState):
        """Edge: Route based on result count status"""
        status = state.get("result_count_status", "zero")
        return status
    
    def check_clarification_needed(self, state: GraphState):
        """Edge: Check if clarification is needed"""
        if state.get("needs_clarification"):
            return "clarify"
        return "continue"
    
    def check_relaxation_done(self, state: GraphState):
        """Edge: Skip re-searching once max relaxation level is reached"""
        if state.get("relaxation_level", 0) >= 4:
            return "done"
        return "continue"

    # --- Enhancement Nodes ---
    
    def clarification_node(self, state: GraphState):
        """Deliberate pause node — only reached when a pause is genuinely warranted.

        Two entry paths:
          • result_validator  → too_many (50+ results): ask user to narrow filters
          • check_intent      → shopping_conflict:      ask START_FRESH / MERGE / REPLACE

        The node always sets needs_clarification=True so check_clarification_needed
        routes to END and the graph waits for the next user turn.  The "continue"
        branch on that edge is a safety valve only.

        On the next turn, intent_classifier_node handles the user's response
        (clarification_asked_last_turn=True path) and applies the chosen action
        before routing back to personalization — the clarification node is NOT
        re-entered on the response turn.
        """
        previous_preferences = state.get("previous_preferences")
        new_preferences      = state.get("new_preferences")

        # ── Path 1: too_many — question already built by ResultValidator ──────
        if state.get("result_count_status") == "too_many":
            question = state.get(
                "clarification_question",
                "I found a lot of matching bags. Could you help me narrow it down? "
                "Do you have a preferred colour, brand, or budget in mind?"
            )
            print("[CLARIFICATION] too_many path — asking refinement question")
            return {
                "needs_clarification":           True,
                "clarification_question":        question,
                "clarification_asked_last_turn": True,
                "result_count_status":           None,  # clear so next turn routes normally
            }

        # ── Path 2: preference conflict — build START_FRESH / MERGE / REPLACE ─
        # check_intent already confirmed a conflict exists before routing here,
        # so we go straight to building the message.
        if previous_preferences and new_preferences:
            conflict_message  = self._format_conflict_message(previous_preferences, new_preferences)
            enhanced_message  = self._format_clarification_options(
                conflict_message, previous_preferences, new_preferences, "REPLACE"
            )
            print("[CLARIFICATION] Preference conflict — asking START_FRESH / MERGE / REPLACE")
            return {
                "needs_clarification":           True,
                "clarification_question":        enhanced_message,
                "clarification_asked_last_turn": True,
                "preferences":                   previous_preferences,  # hold old prefs while waiting
                "new_preferences":               new_preferences,       # saved for when user responds
            }

        # ── Safety: should not be reached in normal operation ─────────────────
        print("[CLARIFICATION] Warning: reached with no actionable state — passing through")
        return {
            "needs_clarification": False,
            "clarification_question": None,
            "clarification_asked_last_turn": False,
        }
    
    def _classify_preference_action(self, previous_preferences, new_preferences, query: str) -> dict:
        """Use LLM to determine if user wants to START_FRESH, MERGE, or REPLACE"""
        import json
        import re
        
        try:
            # Format preferences for the prompt
            prev_prefs_str = self._format_preferences_for_prompt(previous_preferences)
            new_prefs_str = self._format_preferences_for_prompt(new_preferences)
            
            # Load prompt template
            prompt = load_prompt("preference_action_classifier", {
                "previous_preferences": prev_prefs_str,
                "new_request": query,
                "new_preferences": new_prefs_str
            })
            
            # Get LLM decision
            response = self.chat_model.invoke(prompt)
            content = response.content if hasattr(response, 'content') else str(response)
            
            # Parse JSON from response
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group(0))
                return result
            
            # Fallback if parsing fails
            return {
                "action": "MERGE",
                "confidence": "low",
                "clarification_needed": True,
                "clarification_message": "I'm not sure if you want to add these filters or start a new search. What would you prefer?"
            }
            
        except Exception as e:
            print(f"[CLARIFICATION] Error in action classification: {e}")
            # Safe fallback: ask for clarification
            return {
                "action": "MERGE",
                "confidence": "low",
                "clarification_needed": True,
                "clarification_message": "How would you like to proceed with your search?"
            }
    
    def _detect_preference_conflicts(self, prev_prefs, new_prefs) -> bool:
        """Detect if new preferences conflict with previous ones"""
        # Check for color conflicts (changing from one color to another)
        if prev_prefs.colors and new_prefs.colors:
            if set(prev_prefs.colors) != set(new_prefs.colors):
                # Colors are different - this is a conflict
                return True

        # Check for brand conflicts (switching to a different brand)
        # e.g. "MIMCO bags" → "Armani bags" must ask: START_FRESH or MERGE?
        if prev_prefs.brands and new_prefs.brands:
            if set(prev_prefs.brands) != set(new_prefs.brands):
                return True

        # Check for category conflicts
        if prev_prefs.categories and new_prefs.categories:
            if set(prev_prefs.categories) != set(new_prefs.categories):
                return True

        # Check for material conflicts
        if prev_prefs.materials and new_prefs.materials:
            if set(prev_prefs.materials) != set(new_prefs.materials):
                return True
        
        # NEW: Check if user is adding a NEW dimension to their search
        # E.g., "leather bags" (material) → "brown bags" (color)
        # This is ambiguous: do they want "brown leather" or just "brown"?
        has_significant_previous = bool(prev_prefs.colors or prev_prefs.categories or prev_prefs.materials)
        has_significant_new = bool(new_prefs.colors or new_prefs.categories or new_prefs.materials)
        
        if has_significant_previous and has_significant_new:
            # Check if they're adding a new dimension (e.g., had material, now adding color)
            prev_dimensions = set()
            new_dimensions = set()
            
            if prev_prefs.colors: prev_dimensions.add('colors')
            if prev_prefs.categories: prev_dimensions.add('categories')
            if prev_prefs.materials: prev_dimensions.add('materials')
            
            if new_prefs.colors: new_dimensions.add('colors')
            if new_prefs.categories: new_dimensions.add('categories')
            if new_prefs.materials: new_dimensions.add('materials')
            
            # If they're introducing a new dimension, this needs clarification
            # E.g., prev had only 'materials', now adding 'colors'
            if new_dimensions - prev_dimensions:
                print(f"[CONFLICT DETECTION] New dimension detected: {new_dimensions - prev_dimensions}")
                return True
        
        return False
    
    def _format_conflict_message(self, prev_prefs, new_prefs) -> str:
        """Format a message explaining the conflict"""
        conflicts = []

        if prev_prefs.colors and new_prefs.colors and set(prev_prefs.colors) != set(new_prefs.colors):
            conflicts.append(f"You previously searched for {', '.join(prev_prefs.colors)} bags, now you want {', '.join(new_prefs.colors)}")

        if prev_prefs.brands and new_prefs.brands and set(prev_prefs.brands) != set(new_prefs.brands):
            conflicts.append(f"You previously searched for {', '.join(prev_prefs.brands)}, now you want {', '.join(new_prefs.brands)}")

        if prev_prefs.categories and new_prefs.categories and set(prev_prefs.categories) != set(new_prefs.categories):
            conflicts.append(f"Category changed from {', '.join(prev_prefs.categories)} to {', '.join(new_prefs.categories)}")

        if prev_prefs.materials and new_prefs.materials and set(prev_prefs.materials) != set(new_prefs.materials):
            conflicts.append(f"Material changed from {', '.join(prev_prefs.materials)} to {', '.join(new_prefs.materials)}")
        
        # Check for new dimension being added
        if not conflicts:
            # Build readable description of what was added
            prev_parts = []
            new_parts = []
            
            if prev_prefs.colors: prev_parts.append(f"{', '.join(prev_prefs.colors)} color")
            if prev_prefs.categories: prev_parts.append(f"{', '.join(prev_prefs.categories)} style")
            if prev_prefs.materials: prev_parts.append(f"{', '.join(prev_prefs.materials)} material")
            
            if new_prefs.colors: new_parts.append(f"{', '.join(new_prefs.colors)} color")
            if new_prefs.categories: new_parts.append(f"{', '.join(new_prefs.categories)} style")
            if new_prefs.materials: new_parts.append(f"{', '.join(new_prefs.materials)} material")
            
            prev_desc = " + ".join(prev_parts) if prev_parts else "bags"
            new_desc = " + ".join(new_parts) if new_parts else "bags"
            
            base_message = f"You previously searched for **{prev_desc}**, now you're asking for **{new_desc}**."
        else:
            base_message = "I noticed you're changing your search criteria. " + "\n".join(conflicts) + "."
        
        return base_message
    
    def _format_preferences_for_prompt(self, prefs) -> str:
        """Format preferences as human-readable text for LLM"""
        if not prefs:
            return "None"
        
        parts = []
        if prefs.categories:
            parts.append(f"Style: {', '.join(prefs.categories)}")
        if prefs.colors:
            parts.append(f"Colors: {', '.join(prefs.colors)}")
        if prefs.brands:
            parts.append(f"Brands: {', '.join(prefs.brands)}")
        if prefs.price_min or prefs.price_max:
            price_range = []
            if prefs.price_min:
                price_range.append(f"${prefs.price_min}+")
            if prefs.price_max:
                price_range.append(f"under ${prefs.price_max}")
            parts.append(f"Price: {' '.join(price_range)}")
        if prefs.materials:
            parts.append(f"Material: {', '.join(prefs.materials)}")
        
        return " | ".join(parts) if parts else "No specific preferences"
    
    def _format_clarification_options(self, message: str, prev_prefs, new_prefs, suggested_action: str) -> str:
        """Format clarification message with clear options"""
        base_message = message if message else "I see you have a new request."
        
        # Add clear options
        options = "\n\nPlease choose:\n"
        options += "1️⃣ START FRESH - Clear all previous filters and search only for this\n"
        options += "2️⃣ MERGE - Keep previous filters AND add these new ones\n"
        options += "3️⃣ REPLACE - Update specific filters while keeping others\n\n"
        options += "Just reply with the number or tell me what you'd prefer!"
        
        return base_message + options
    
    def _apply_preference_action(self, action: str, previous_preferences, new_preferences):
        """Apply the determined action to preferences"""
        if action == "START_FRESH":
            print("[CLARIFICATION] Applying START_FRESH - using only new preferences")
            # Return only the new preferences — never carry forward anything from previous.
            # Fall back to empty SearchPreferences() (not previous) so we don't accidentally
            # keep old filters when new_preferences is None.
            return new_preferences or SearchPreferences()
        
        elif action == "REPLACE":
            print("[CLARIFICATION] Applying REPLACE - updating specific attributes")
            # Smart replace: only update non-empty fields from new preferences
            return self._merge_preferences(previous_preferences, new_preferences, "preference_update")
        
        else:  # MERGE (default)
            print("[CLARIFICATION] Applying MERGE - combining preferences")
            return self._merge_preferences(previous_preferences, new_preferences, "shopping")
    
    def personalization_node(self, state: GraphState):
        """Node: Generate personalized context from history and user profile"""
        
        # If personalization engine is not enabled, use legacy simple personalization
        if not self.personalization_enabled:
            return self._legacy_personalization(state)
        
        # New personalization with profile learning
        user_id = state.get("user_id")
        query = state.get("query", "")
        history = state.get("history", [])
        summary = state.get("summary", "")
        preferences = state.get("preferences")
        last_discussed_product = state.get("last_discussed_product")
        personalization_session = state.get("personalization_session")
        
        # If no user_id, fall back to legacy
        if not user_id:
            print("[PERSONALIZATION] No user_id provided, using legacy personalization")
            return self._legacy_personalization(state)
        
        try:
            from .personalization.models import UserProfile, SessionState
            from .personalization.engine import RateLimitExceeded
            
            # Load user profile from storage
            current_profile = self.profile_storage.load_profile(user_id)
            
            # Deserialize session state if exists
            current_session = None
            if personalization_session:
                current_session = SessionState.from_dict(personalization_session)
            
            # Process message with personalization engine
            try:
                updated_profile, updated_session, context = self.personalization_engine.process_message(
                    user_id=user_id,
                    user_message=query,
                    current_profile=current_profile,
                    current_session=current_session
                )
                
                # Save updated profile
                self.profile_storage.save_profile(updated_profile)
                
                # Build personalization context for LLM
                personalization_context = context.get("profile_summary", "")
                
                # Enhance context with product interests
                if last_discussed_product:
                    personalization_context += f". Recently discussed: {last_discussed_product.get('name')} by {last_discussed_product.get('brand')}"
                
                # Merge personalization insights with existing preferences
                merged_preferences = self._merge_personalization_with_preferences(
                    current_preferences=preferences,
                    personalization_context=context,
                    updated_profile=updated_profile
                )
                
                print(f"[PERSONALIZATION] Profile updated for user {user_id}")
                print(f"[PERSONALIZATION] Context: {personalization_context}")
                
                return {
                    "personalization_context": personalization_context,
                    "personalization_session": updated_session.to_dict(),
                    "preferences": merged_preferences or preferences  # Use merged if available
                }
                
            except RateLimitExceeded as e:
                print(f"[PERSONALIZATION] Rate limit exceeded for user {user_id}: {e}")
                # Don't update profile, but continue with existing state
                return {
                    "personalization_context": "Rate limit reached. Using previous preferences.",
                    "personalization_session": personalization_session
                }
                
        except Exception as e:
            print(f"[PERSONALIZATION] Error: {e}")
            import traceback
            print(f"[PERSONALIZATION] Traceback: {traceback.format_exc()}")
            # Fall back to legacy on error
            return self._legacy_personalization(state)
    
    def _legacy_personalization(self, state: GraphState):
        """Legacy personalization implementation (simple history-based)"""
        history = state.get("history", [])
        summary = state.get("summary", "")
        preferences = state.get("preferences")
        last_discussed_product = state.get("last_discussed_product")
        
        # Generate personalization context if we have history
        personalization_context = None
        
        if history or summary:
            try:
                # Build context from history
                history_text = summary if summary else "\\n".join(history[-5:])
                
                # Extract product interests from history
                context_parts = []
                
                # Check for product selections in history
                selected_products = []
                for entry in history:
                    if "[Selected Product]" in entry:
                        # Extract product name and brand
                        import re
                        match = re.search(r'\[Selected Product\] (.+?) \(ID: (.+?)\) by (.+?)$', entry)
                        if match:
                            selected_products.append({
                                'name': match.group(1),
                                'id': match.group(2),
                                'brand': match.group(3)
                            })
                
                if selected_products:
                    brands = [p['brand'] for p in selected_products]
                    context_parts.append(f"User has shown interest in: {', '.join([p['name'] for p in selected_products[-3:]])}")
                    if brands:
                        context_parts.append(f"Preferred brands: {', '.join(set(brands))}")
                
                # Add current preferences
                if preferences:
                    # Track user's style preferences
                    if preferences.colors:
                        context_parts.append(f"User prefers {', '.join(preferences.colors[:2])} colors")
                    if preferences.materials:
                        context_parts.append(f"Interested in {', '.join(preferences.materials[:2])} materials")
                    if preferences.categories:
                        context_parts.append(f"Looking for {', '.join(preferences.categories[:2])}")
                    if preferences.price_max:
                        context_parts.append(f"Budget-conscious: up to ${preferences.price_max}")
                
                # Add last discussed product context
                if last_discussed_product:
                    context_parts.append(f"Recently discussed: {last_discussed_product.get('name')} by {last_discussed_product.get('brand')}")
                
                if context_parts:
                    personalization_context = ". ".join(context_parts)
                    print(f"[PERSONALIZATION] Legacy Context: {personalization_context}")
            
            except Exception as e:
                print(f"[PERSONALIZATION] Legacy Error: {e}")
        
        return {"personalization_context": personalization_context}
    
    def _merge_personalization_with_preferences(self, current_preferences, personalization_context: dict, updated_profile):
        """Merge personalization insights with current search preferences"""
        if not current_preferences:
            return None
        
        try:
            # Extract learned preferences from profile
            learned_brands = []
            learned_colors = []
            learned_materials = []
            
            # Get top weighted preferences from profile (access via preferences dict)
            # Using standardized keys that match SearchPreferences
            brands_category = updated_profile.preferences.get("brands")
            if brands_category and brands_category.items:
                learned_brands = [
                    pref.value for pref in 
                    sorted(brands_category.items.values(), key=lambda x: x.weight, reverse=True)[:3]
                ]
            
            colors_category = updated_profile.preferences.get("colors")
            if colors_category and colors_category.items:
                learned_colors = [
                    pref.value for pref in 
                    sorted(colors_category.items.values(), key=lambda x: x.weight, reverse=True)[:3]
                ]
            
            materials_category = updated_profile.preferences.get("materials")
            if materials_category and materials_category.items:
                learned_materials = [
                    pref.value for pref in 
                    sorted(materials_category.items.values(), key=lambda x: x.weight, reverse=True)[:3]
                ]
            
            # Also get categories (bag types)
            learned_categories = []
            categories_category = updated_profile.preferences.get("categories")
            if categories_category and categories_category.items:
                learned_categories = [
                    pref.value for pref in 
                    sorted(categories_category.items.values(), key=lambda x: x.weight, reverse=True)[:3]
                ]
            
            # Get excluded items
            excluded_colors_set = set(colors_category.disliked_items.keys()) if colors_category else set()
            excluded_brands_set = set(brands_category.disliked_items.keys()) if brands_category else set()
            excluded_materials_set = set(materials_category.disliked_items.keys()) if materials_category else set()
            
            # Filter learned preferences to remove any that are in excluded lists
            learned_colors = [c for c in learned_colors if c not in excluded_colors_set]
            learned_brands = [b for b in learned_brands if b not in excluded_brands_set]
            learned_materials = [m for m in learned_materials if m not in excluded_materials_set]
            
            # For current preferences, also check conflicts with excluded items
            current_colors = current_preferences.colors if current_preferences.colors else []
            current_brands = current_preferences.brands if current_preferences.brands else []
            current_materials = current_preferences.materials if current_preferences.materials else []
            
            # Remove any current preferences that conflict with excluded items
            current_colors = [c for c in current_colors if c not in excluded_colors_set]
            current_brands = [b for b in current_brands if b not in excluded_brands_set]
            current_materials = [m for m in current_materials if m not in excluded_materials_set]
            
            # Merge: Current query preferences take priority, learned preferences fill gaps
            merged = SearchPreferences(
                price_min=current_preferences.price_min,
                price_max=current_preferences.price_max,
                brands=current_brands if current_brands else learned_brands,
                categories=current_preferences.categories if current_preferences.categories else learned_categories,
                colors=current_colors if current_colors else learned_colors,
                materials=current_materials if current_materials else learned_materials,
                features=current_preferences.features,
                closure_types=current_preferences.closure_types,
                strap_types=current_preferences.strap_types,
                sizes=current_preferences.sizes,
                has_zipper=current_preferences.has_zipper,
                excluded_colors=list(excluded_colors_set),
                excluded_brands=list(excluded_brands_set),
                excluded_categories=current_preferences.excluded_categories,
                excluded_materials=list(excluded_materials_set)
            )
            
            return merged
            
        except Exception as e:
            print(f"[PERSONALIZATION] Error merging preferences: {e}")
            return None

    # --- RAG Enhancement Nodes ---
    
    def result_validator_node(self, state: GraphState):
        """Node: Validate search results"""
        return self.result_validator.process(state)
    
    def constraint_relaxer_node(self, state: GraphState):
        """Node: Relax search constraints for better results"""
        from .rag_utils import progressive_filter_relaxation, format_relaxation_message
        
        # Get current state
        results = state.get("results", [])
        preferences = state.get("preferences")
        relaxation_level = state.get("relaxation_level", 0)
        
        print(f"\n[CONSTRAINT RELAXER] Current level: {relaxation_level}, Results: {len(results)}")
        
        # If we already have results, no further relaxation needed
        if len(results) > 0:
            print(f"[CONSTRAINT RELAXER] Already have {len(results)} results, stopping relaxation")
            return {"result_count_status": "optimal"}

        # Max level: routing function (check_relaxation_done) will bypass product_search when level >= 4
        if relaxation_level >= 4:
            print(f"[CONSTRAINT RELAXER] Max level reached, giving up")
            # Build a specific message listing what was tried and failed
            tried = []
            if preferences:
                if preferences.brands:
                    tried.append(f"brand ({', '.join(preferences.brands)})")
                if preferences.colors:
                    tried.append(f"colour ({', '.join(preferences.colors)})")
                if preferences.materials:
                    tried.append(f"material ({', '.join(preferences.materials)})")
                if preferences.categories:
                    tried.append(f"category ({', '.join(preferences.categories)})")
            tried_text = ", ".join(tried) if tried else "your criteria"
            return {
                "relaxation_message": (
                    f"I searched broadly but couldn't find any products matching {tried_text}. "
                    f"Try a different combination — for example, a different category or price range."
                )
            }

        # Increment relaxation level
        new_level = relaxation_level + 1
        print(f"[CONSTRAINT RELAXER] Applying relaxation level {new_level}")

        # Relax the preferences — pass original preferences to format_relaxation_message
        # so it can name exactly which filters are being dropped
        if preferences:
            try:
                relaxed_preferences = progressive_filter_relaxation(preferences, new_level)
                relaxation_msg = format_relaxation_message(new_level, 0, 0, preferences=preferences)

                return {
                    "preferences": relaxed_preferences,
                    "relaxation_level": new_level,
                    "relaxation_message": relaxation_msg,
                    "result_count_status": "zero"  # Will trigger another validation
                }
            except Exception as e:
                print(f"[CONSTRAINT RELAXER] Error: {e}")

        # Fallback: just increment level
        return {
            "relaxation_level": new_level,
            "result_count_status": "zero",
            "relaxation_message": "Broadening search criteria..."
        }
    
    def reranker_node(self, state: GraphState):
        """Node: Rerank products using LLM"""
        with track_time("node_reranker"):
            return self.reranker.process(state)
    
    def response_generator_node(self, state: GraphState):
        """Node: Generate conversational response"""
        with track_time("node_response_generator"):
            return self.response_generator.process(state)
    
    def output_guardrail_node(self, state: GraphState):
        """Node: Validate output response for safety and accuracy"""
        with track_time("node_output_guardrail"):
            generated_response = state.get("generated_response", "")
            query = state.get("query", "")
            results = state.get("reranked_results") or state.get("results", [])
            preferences = state.get("preferences")
            product_discussion_mode = state.get("product_discussion_mode", False)
            product_context = state.get("product_context")
        
        # Determine which products to validate against
        if product_discussion_mode and product_context:
            # In product discussion mode, validate against only the selected product
            products_described = [product_context]
            print(f"\n[OUTPUT GUARDRAIL NODE] Validating response ({len(generated_response)} chars)...")
            print(f"[OUTPUT GUARDRAIL NODE] Product discussion mode - validating against single product")
        else:
            # Normal mode: response generator only describes top 3 products
            # So we should only validate against those 3, not all results
            products_described = results[:3] if results else []
            print(f"\n[OUTPUT GUARDRAIL NODE] Validating response ({len(generated_response)} chars)...")
            print(f"[OUTPUT GUARDRAIL NODE] Validating against {len(products_described)} products (top 3 described)")
        
        if not generated_response:
            # No response to validate, use fallback
            print("[OUTPUT GUARDRAIL NODE] No generated response, using fallback")
            return {
                "safe_response": "I'm here to help you find products. What are you looking for?",
                "guardrail_status": "pass",
                "guardrail_issues": []
            }
        
        # Run guardrail validation
        validation_result = self.output_guardrail.validate_response(
            response=generated_response,
            query=query,
            products=products_described,  # Only validate the products actually described
            preferences=preferences,
            audit_context={
                "request_id": state.get("session_id", ""),
                "user_id": state.get("user_id", "") or "anon",
                "session_id": str(state.get("session_id", "")),
            },
        )
        
        print(f"[OUTPUT GUARDRAIL NODE] Status: {validation_result['status']}")
        if validation_result['issues']:
            print(f"[OUTPUT GUARDRAIL NODE] Issues found: {validation_result['issues']}")
        if validation_result['corrections_made']:
            print("[OUTPUT GUARDRAIL NODE] Response was corrected ✓")

        # ── AUDIT: node execution log ──
        if self.audit_wrapper:
            self.audit_wrapper.log_node_execution(
                trace_id   = getattr(self, "_current_trace_id", ""),
                node_name  = "output_guardrail_node",
                status     = validation_result["status"],
                node_input = generated_response[:500] if generated_response else "",
                node_output= str(validation_result.get("issues", [])),
                node_type  = "guardrail",
                node_order = 6,
            )

        return {
            "safe_response": validation_result["safe_response"],
            "guardrail_status": validation_result["status"],
            "guardrail_issues": validation_result["issues"],
            "generated_response": validation_result["safe_response"]  # Update with safe version
        }
    
    def _detect_product_intent(self, state: GraphState):
        """Determine whether this turn is a product-detail interaction.

        Called at the top of intent_classifier_node — replaces the former
        product_selection_node so that product-routing logic is co-located with
        intent routing and does not run as an unconditional pass-through node.

        Returns
        -------
        dict
            State patch with ``intent = "product_detail"`` — return this
            directly from intent_classifier_node.
        "exit_discussion"
            The user is switching away from discussion mode (new search).
            Caller should clear discussion state and fall through to normal
            LLM classification.
        None
            No product-detail signal detected — run normal classification.
        """
        import re as _re
        query = state.get("query", "").lower().strip()
        results = state.get("reranked_results") or state.get("results", [])
        product_discussion_mode = state.get("product_discussion_mode", False)
        product_context = state.get("product_context")

        # ── Helper ─────────────────────────────────────────────────────────────
        def _get_info(p):
            """Normalise both flat and metadata-wrapped product dicts."""
            if not isinstance(p, dict):
                return None
            if 'metadata' in p:
                m = p['metadata']
                return {'name': m.get('name', 'Unknown'), 'brand': m.get('brand', 'Unknown'),
                        'price': m.get('price', '0'), 'id': p.get('id', ''), 'full': p}
            return {'name': p.get('name', 'Unknown'), 'brand': p.get('brand', 'Unknown'),
                    'price': p.get('price', '0'), 'id': p.get('id', ''), 'full': p}

        # ── 1. Explicit product ID injected by the UI "Ask about this" button ──
        #    Format: "Tell me more about "Name" (ID: abc123) by Brand"
        id_match = _re.search(r'\(ID:\s*([^\)]+)\)', query, _re.IGNORECASE)
        if id_match:
            product_id = id_match.group(1).strip()
            print(f"[INTENT CLASSIFIER] Explicit product ID detected: {product_id}")

            name_match  = _re.search(r'"([^"]+)"', query)
            brand_match = _re.search(r'by\s+([A-Z\s&]+)(?:\s*$)', query, _re.IGNORECASE)
            product_name  = name_match.group(1)  if name_match  else "the product"
            product_brand = brand_match.group(1).strip() if brand_match else "Unknown Brand"

            # Try cached results first, then vector store
            context = None
            for p in results:
                info = _get_info(p)
                if info and str(info['id']) == str(product_id):
                    from .rag_utils import get_product_by_id
                    context = get_product_by_id(product_id, vector_client=self.vector_client) or info['full']
                    print(f"[INTENT CLASSIFIER] Product matched in results: {info['name']}")
                    break

            if context is None:
                try:
                    from .rag_utils import get_product_by_id
                    context = get_product_by_id(product_id, vector_client=self.vector_client)
                    print(f"[INTENT CLASSIFIER] Product fetched from vector store: {product_id}")
                except Exception as exc:
                    print(f"[INTENT CLASSIFIER] Vector store fetch failed: {exc}")

            history = state.get("history", [])
            entry = f"User: [Selected Product] {product_name} (ID: {product_id}) by {product_brand}"
            return {
                "intent":                "product_detail",
                "product_discussion_mode": True,
                "selected_product_id":   product_id,
                "product_context":       context,
                "preferences":           state.get("preferences"),
                "new_preferences":       None,
                "history":               history + [entry],
            }

        # ── 2. Continuation of an active product discussion ───────────────────
        #    The LLM (step 4 in intent_classifier_node) will classify the query as
        #    "shopping" if the user is starting a new search — that naturally clears
        #    discussion mode via the caller's "exit_discussion" branch.
        #    We only need to handle the case where context has become invalid.
        if product_discussion_mode and product_context:
            if 'metadata' in product_context:
                pid  = product_context['metadata'].get('id', product_context['metadata'].get('product_id', ''))
                pname = product_context['metadata'].get('name', 'Unknown')
            else:
                pid  = product_context.get('id', product_context.get('product_id', ''))
                pname = product_context.get('name', 'Unknown')

            if pid in ('', 'unknown', 'not found', None) or pname == 'Unknown':
                # Stale/corrupt context — exit discussion mode gracefully
                print("[INTENT CLASSIFIER] Stale product context, exiting discussion mode")
                return "exit_discussion"

            # Valid context + no explicit ID in query → treat as follow-up
            print(f"[INTENT CLASSIFIER] Product discussion follow-up → {pname} (ID: {pid})")
            return {
                "intent":                "product_detail",
                "product_discussion_mode": True,
                "product_context":       product_context,
                "selected_product_id":   state.get("selected_product_id"),
                "preferences":           state.get("preferences"),
                "new_preferences":       None,
            }

        # ── 3. Natural language product reference (LLM fallback) ─────────────
        #    Only runs when there are results on screen and no discussion mode.
        #    e.g. "tell me more about the Chanel bag" after a search result set.
        if results:
            products_info = [
                f"{i+1}. {inf['name']} by {inf['brand']} (${inf['price']}) - ID: {inf['id']}"
                for i, p in enumerate(results[:10])
                if (inf := _get_info(p)) is not None
            ]
            if products_info:
                products_list = "\n".join(products_info)
                prompt = load_prompt("product_selection_detection", {
                    "products_list": products_list,
                    "user_query":    query,
                })
                try:
                    import json
                    response      = self.chat_model.invoke(prompt)
                    response_text = response.content.strip() if hasattr(response, 'content') else str(response).strip()
                    json_match    = _re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response_text, _re.DOTALL)
                    if json_match:
                        sel = json.loads(json_match.group(0))
                        print(f"[INTENT CLASSIFIER] NL product detection: {sel}")
                        if sel.get("is_selecting_product"):
                            pid = sel.get("product_id")
                            for p in results:
                                info = _get_info(p)
                                if info and info['id'] == pid:
                                    history = state.get("history", [])
                                    entry   = f"User: [Selected Product] {info['name']} (ID: {info['id']}) by {info['brand']}"
                                    return {
                                        "intent":                "product_detail",
                                        "product_discussion_mode": True,
                                        "selected_product_id":   info['id'],
                                        "product_context":       info['full'],
                                        "preferences":           state.get("preferences"),
                                        "new_preferences":       None,
                                        "history":               history + [entry],
                                    }
                except Exception as exc:
                    print(f"[INTENT CLASSIFIER] NL product detection error: {exc}")

        # No product-detail signal — caller runs normal LLM classification
        return None
    
    # --- Helpers ---

    def _merge_preferences(
        self,
        previous: Optional[SearchPreferences], 
        new: Optional[SearchPreferences],
        intent: str
    ) -> Optional[SearchPreferences]:
        """Merge new preferences with previous preferences"""
        if not previous:
            return new
        if not new:
            return previous
        
        if intent == "preference_update":
            # Update mode: Replace fields that have new values
            merged = previous.model_copy(deep=True)
            
            if new.price_min is not None:
                merged.price_min = new.price_min
            if new.price_max is not None:
                merged.price_max = new.price_max
            
            if new.colors:
                merged.colors = new.colors
            if new.materials:
                merged.materials = new.materials
            if new.categories:
                merged.categories = new.categories
            if new.brands:
                merged.brands = new.brands
            if new.features:
                merged.features = list(set(merged.features + new.features))
            
            if new.excluded_colors:
                merged.excluded_colors = list(set(merged.excluded_colors + new.excluded_colors))
            if new.excluded_brands:
                merged.excluded_brands = list(set(merged.excluded_brands + new.excluded_brands))
            if new.excluded_categories:
                merged.excluded_categories = list(set(merged.excluded_categories + new.excluded_categories))
            if new.excluded_materials:
                merged.excluded_materials = list(set(merged.excluded_materials + new.excluded_materials))
                
        else:  # shopping intent
            # Smart-merge mode: for single-value fields (color, brand, category, material)
            # replace when the new value is non-empty and differs — avoids accumulating
            # contradictory filters like ['black','brown'] or ['MIMCO','Armani'].
            # For additive fields (features) keep accumulation as before.
            merged = previous.model_copy(deep=True)

            if new.price_min is not None:
                merged.price_min = max(merged.price_min or 0, new.price_min)
            if new.price_max is not None:
                if merged.price_max is None:
                    merged.price_max = new.price_max
                else:
                    merged.price_max = min(merged.price_max, new.price_max)

            # Replace if the new list is non-empty and different; otherwise keep old
            if new.colors and set(new.colors) != set(merged.colors):
                merged.colors = new.colors
            elif new.colors:
                merged.colors = list(set(merged.colors + new.colors))

            if new.brands and set(new.brands) != set(merged.brands):
                # Brand explicitly changed → replace entirely (no accumulation)
                merged.brands = new.brands
            elif new.brands:
                # Same brands again → keep (no change needed)
                merged.brands = list(set(merged.brands + new.brands))
            # If new.brands is empty, carry forward existing brand — user is refining
            # within the same brand context (e.g. "MIMCO bags" → "show me blue ones")

            if new.materials and set(new.materials) != set(merged.materials):
                merged.materials = new.materials
            elif new.materials:
                merged.materials = list(set(merged.materials + new.materials))

            if new.categories and set(new.categories) != set(merged.categories):
                merged.categories = new.categories
            elif new.categories:
                merged.categories = list(set(merged.categories + new.categories))

            merged.features = list(set(merged.features + new.features))
            
            merged.excluded_colors = list(set(merged.excluded_colors + new.excluded_colors))
            merged.excluded_brands = list(set(merged.excluded_brands + new.excluded_brands))
            merged.excluded_categories = list(set(merged.excluded_categories + new.excluded_categories))
            merged.excluded_materials = list(set(merged.excluded_materials + new.excluded_materials))
        
        return merged
    
    def _check_input_safety(self, query: str) -> dict:
        """
        Check input query for safety and relevance
        
        Returns:
            dict with keys: status (SAFE/UNSAFE), category, reason, decline_message
        """
        try:
            from .prompt_loader import load_prompt
            import re
            
            # Build safety check prompt
            prompt = load_prompt("input_safety_check", {"query": query})
            
            # Get LLM response
            response = self.chat_model.invoke(prompt)
            content = response.content if hasattr(response, 'content') else str(response)
            
            # Parse response
            status_match = re.search(r'STATUS:\s*(SAFE|UNSAFE)', content, re.IGNORECASE)
            category_match = re.search(r'CATEGORY:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
            reason_match = re.search(r'REASON:\s*(.+?)(?:\n|POLITE)', content, re.IGNORECASE | re.DOTALL)
            message_match = re.search(r'POLITE_DECLINE_MESSAGE:\s*(.+?)(?:\n\n|$)', content, re.IGNORECASE | re.DOTALL)
            
            status = status_match.group(1).upper() if status_match else "SAFE"
            category = category_match.group(1).strip() if category_match else "APPROPRIATE"
            reason = reason_match.group(1).strip() if reason_match else "No issues detected"
            decline_message = message_match.group(1).strip() if message_match else "N/A"
            
            # If decline message is N/A or empty, provide default
            if decline_message in ["N/A", "n/a", ""]:
                decline_message = "I'm here to help you find bags, wallets, and accessories. How can I assist you with shopping today?"
            
            return {
                "status": status,
                "category": category,
                "reason": reason,
                "decline_message": decline_message
            }
            
        except Exception as e:
            print(f"[INPUT GUARDRAIL] Safety check error: {e}")
            # On error, allow through (fail open for better UX)
            return {
                "status": "SAFE",
                "category": "ERROR",
                "reason": f"Safety check error: {str(e)}",
                "decline_message": "N/A"
            }
    
    def _build_search_query(self, preferences: SearchPreferences) -> str:
        """Helper: Build search string"""
        parts = []
        if preferences.categories: parts.append(f"{', '.join(preferences.categories)}")
        if preferences.materials: parts.append(f"{', '.join(preferences.materials)}")
        if preferences.colors: parts.append(f"{', '.join(preferences.colors)}")
        if preferences.brands: parts.append(f"{', '.join(preferences.brands)}")
        if preferences.features: parts.append(f"{', '.join(preferences.features)}")
        return " ".join(parts) if parts else "bag"

    def _build_pinecone_filters(self, preferences: SearchPreferences) -> dict:
        """Helper: Build Pinecone metadata filters matching actual index structure"""
        conditions = []
        
        # Note: Price is stored as string in Pinecone, so we skip numeric comparisons
        # You may need to filter results after retrieval or convert in Pinecone upload
        
        # Category filter (inclusion) - uses "category" field
        if preferences.categories:
            # Map friendly names and create variations for better matching
            category_variations = []
            for cat in preferences.categories:
                cat_lower = cat.lower()
                # Add original
                category_variations.append(cat)
                # Add lowercase
                category_variations.append(cat_lower)
                # Add capitalized
                category_variations.append(cat.title())
                # Add without 's' or with 's'
                if cat_lower.endswith('s'):
                    category_variations.append(cat_lower[:-1])  # "tote bags" -> "tote bag"
                    category_variations.append(cat_lower[:-1].title())  # "Tote Bag"
                else:
                    category_variations.append(cat_lower + 's')  # "tote" -> "totes"
                    category_variations.append((cat_lower + 's').title())  # "Totes"
                
                # Add common mappings
                if 'tote' in cat_lower:
                    category_variations.extend(['tote', 'Tote', 'tote bag', 'Tote Bag', 'tote bags', 'Tote Bags'])
                elif 'shoulder' in cat_lower:
                    category_variations.extend(['shoulder', 'Shoulder', 'shoulder bag', 'Shoulder Bag'])
                elif 'crossbody' in cat_lower or 'cross-body' in cat_lower:
                    category_variations.extend(['crossbody', 'Crossbody', 'cross-body', 'Cross-Body'])
                elif 'backpack' in cat_lower:
                    category_variations.extend(['backpack', 'Backpack', 'backpacks', 'Backpacks'])
            
            # Remove duplicates
            category_variations = list(set(category_variations))
            
            print(f"[FILTER DEBUG] Original categories: {preferences.categories}")
            print(f"[FILTER DEBUG] Category variations for matching: {category_variations[:10]}...")  # Show first 10
            
            # Always use $in for flexibility, even with one category
            conditions.append({"category": {"$in": category_variations}})
        
        # Category filter (exclusion)
        if preferences.excluded_categories:
            category_map = {
                "tote bags": "tote",
                "shoulder bags": "shoulder",
                "crossbody bags": "crossbody",
                "backpacks": "backpack",
                "clutches": "clutch"
            }
            mapped_excluded = [category_map.get(cat.lower(), cat) for cat in preferences.excluded_categories]
            
            if len(mapped_excluded) == 1:
                conditions.append({"category": {"$ne": mapped_excluded[0]}})
            else:
                conditions.append({"category": {"$nin": mapped_excluded}})
        
        # Color filter (inclusion) - uses "primary_color" field
        if preferences.colors:
            # Create color variations (lowercase, title case)
            color_variations = []
            for color in preferences.colors:
                color_variations.append(color.lower())
                color_variations.append(color.title())
                color_variations.append(color.upper())
            color_variations = list(set(color_variations))
            print(f"[FILTER DEBUG] Colors: {color_variations}")
            # Always use $in for flexibility
            conditions.append({"primary_color": {"$in": color_variations}})
        
        # Color filter (exclusion)
        if preferences.excluded_colors:
            if len(preferences.excluded_colors) == 1:
                conditions.append({"primary_color": {"$ne": preferences.excluded_colors[0]}})
            else:
                conditions.append({"primary_color": {"$nin": preferences.excluded_colors}})
        
        # Material filter (inclusion) - uses "material_type" field
        if preferences.materials:
            # Create material variations
            material_variations = []
            for mat in preferences.materials:
                material_variations.append(mat.lower())
                material_variations.append(mat.title())
                material_variations.append(mat.upper())
            material_variations = list(set(material_variations))
            conditions.append({"material_type": {"$in": material_variations}})
        
        # Material filter (exclusion)
        if preferences.excluded_materials:
            if len(preferences.excluded_materials) == 1:
                conditions.append({"material_type": {"$ne": preferences.excluded_materials[0]}})
            else:
                conditions.append({"material_type": {"$nin": preferences.excluded_materials}})
        
        # Brand filter (exclusion) - uses "brand" field
        if preferences.excluded_brands:
            if len(preferences.excluded_brands) == 1:
                conditions.append({"brand": {"$ne": preferences.excluded_brands[0]}})
            else:
                conditions.append({"brand": {"$nin": preferences.excluded_brands}})
        
        # Filter only available products (commented out for debugging)
        # conditions.append({"available": {"$eq": "true"}})
        
        # Combine all conditions
        if len(conditions) == 0:
            return {}
        elif len(conditions) == 1:
            return conditions[0]
        else:
            return {"$and": conditions}

    def process_query(self, query: str, session_id: str, user_id: str = None) -> dict:
        """Main entry point for the app"""
        config = {"configurable": {"thread_id": session_id}}
        
        # Retrieve previous state from the graph
        try:
            state_snapshot = self.app.get_state(config)
            if state_snapshot and state_snapshot.values:
                # Merge previous state with new query
                previous_state = state_snapshot.values
                initial_state = {
                    "query": query,
                    "user_id": user_id,  # Add user_id for personalization
                    "trace_id": None,  # Will be backfilled after MLflow trace opens
                    "history": previous_state.get("history", []),
                    "summary": previous_state.get("summary", ""),
                    "preferences": previous_state.get("preferences"),
                    "previous_preferences": previous_state.get("preferences"),  # Track previous preferences for conflict detection
                    "reranked_results": None,  # Always clear — cards must match current search only
                    "results": None,  # Always clear — prevents stale results from previous turn
                    "last_discussed_product": previous_state.get("last_discussed_product"),  # Preserve product interest
                    "product_discussion_mode": previous_state.get("product_discussion_mode"),  # Preserve discussion mode
                    "product_context": previous_state.get("product_context"),  # Preserve product context
                    "selected_product_id": previous_state.get("selected_product_id"),  # Preserve selection
                    "clarification_asked_last_turn": previous_state.get("needs_clarification", False),  # Track if we just asked clarification
                    "new_preferences": previous_state.get("new_preferences") if previous_state.get("needs_clarification", False) else None,  # Preserve when user is responding to clarification
                    "user_action_choice": None,  # Clear any previous choice
                    "personalization_session": previous_state.get("personalization_session")  # Preserve personalization session
                }
            else:
                # New session
                initial_state = {
                    "query": query,
                    "user_id": user_id,  # Add user_id for personalization
                    "trace_id": None,  # Will be backfilled after MLflow trace opens
                    "history": [],
                    "summary": "",
                }
        except Exception as e:
            print(f"[MEMORY] Warning: Could not retrieve previous state: {e}")
            initial_state = {
                "query": query,
                "user_id": user_id,  # Add user_id for personalization
                "trace_id": None,  # Will be backfilled after MLflow trace opens
                "history": [],
                "summary": "",
            }
        
        # ── AUDIT: generate trace_id before graph runs so nodes can log
        #    to node_executions_raw with a consistent trace_id ──
        import uuid as _uuid
        pre_trace_id = str(_uuid.uuid4())
        initial_state["trace_id"] = pre_trace_id
        self._current_trace_id = pre_trace_id

        # ── AUDIT: log session start once per new conversation ──
        _is_new_session = not (
            initial_state.get("history") or initial_state.get("summary")
        )
        if self.audit_wrapper and user_id and _is_new_session:
            try:
                self.audit_wrapper.log_session(
                    session_id  = session_id,
                    user_email  = user_id,
                    channel     = "web",
                    device_type = "unknown",
                )
            except Exception as _se:
                print(f"[AUDIT] Session logging failed (non-blocking): {_se}")

        # Run graph inside a root MLflow trace so all node spans are nested under it
        with RequestTrace(query=query, user_id=user_id, session_id=session_id) as rt:
            final_state = self.app.invoke(initial_state, config=config)
            rt.set_result(final_state)

        # Attach trace_id to response so the UI / API can surface it for feedback
        trace_id = rt.trace_id or pre_trace_id
        final_state["trace_id"] = trace_id

        # ── LOG TO AUDIT TRAIL ────────────────────────────────────────────
        if self.audit_wrapper and user_id:
            try:
                _output = (
                    final_state.get("safe_response") or
                    final_state.get("generated_response") or
                    final_state.get("clarification_question") or
                    final_state.get("error") or
                    "no_response"
                )
                _status = "success"
                if final_state.get("error"):
                    _status = "error"
                elif final_state.get("guardrail_status") == "fail":
                    _status = "guardrail_blocked"

                _user_country = ""
                try:
                    if hasattr(self, 'profile_storage') and user_id:
                        _profile = self.profile_storage.load_profile(user_id)
                        if _profile and hasattr(_profile, 'country'):
                            _user_country = _profile.country or ""
                except Exception:
                    pass
                if not _user_country:
                    _user_country = os.getenv("DEFAULT_USER_COUNTRY", "")

                _result = self.audit_wrapper.log_interaction(
                    user_email            = user_id,
                    user_input            = query,
                    model_output          = str(_output),
                    model_name            = os.getenv(
                        "DATABRICKS_CHAT_ENDPOINT",
                        "databricks-meta-llama-3-1-8b-instruct"
                    ),
                    status                = _status,
                    session_id            = session_id,
                    user_country          = _user_country,
                    system_prompt_version = "v1.0",
                    mlflow_trace_id       = trace_id,
                    final_state           = final_state,
                )

                if final_state.get("guardrail_status"):
                    self.audit_wrapper.log_guardrail(
                        trace_id        = _result.get("trace_id", ""),
                        policy_name     = "output_content_safety",
                        score           = 1.0 if final_state.get("guardrail_status") == "pass" else 0.0,
                        result          = final_state.get("guardrail_status", "pass"),
                        triggered_block = final_state.get("guardrail_status") == "fail",
                        subject_ref     = _result.get("subject_ref"),
                    )

            except Exception as _e:
                print(f"[AUDIT] Logging failed (non-blocking): {_e}")
        # ─────────────────────────────────────────────────────────────────

      
        return final_state
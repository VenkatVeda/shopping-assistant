"""
LangGraph Workflow for Shopping Assistant with Vector Search
Architecture: Input Guardrail -> Intent Classifier -> Product Search -> RAG Enhancement
"""

import os
import sys
import time
import threading
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
from .evals import EvalRunner
from .semantic_cache import build_cache

# ── ContextVars now live in audit_wrapper.py — import from there ──────────
# REMOVED: from contextvars import ContextVar
# REMOVED: _current_trace_id: ContextVar[str] = ContextVar('_current_trace_id', default='')
# REMOVED: _current_subject_ref: ContextVar[str] = ContextVar('_current_subject_ref', default='')
from audit_wrapper import AuditWrapper, AuditTrailCallback, _current_trace_id, _current_subject_ref

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
                print("⚠ No fallback available — vector_client set to None")
                self.vector_client = None
        
        # Initialize Memory
        self.memory = MemorySaver()
        self.memory_manager = MemoryManager(chat_endpoint=chat_endpoint)
        
        # Initialize RAG Nodes
        self.result_validator = ResultValidator()
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
                
                workspace_client = WorkspaceClient()
                warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID")
                
                self.preference_extractor = PreferenceExtractor(
                    llm_client=workspace_client,
                    model_name=chat_endpoint
                )
                self.personalization_engine = PersonalizationEngine(
                    self.preference_extractor,
                    enable_rate_limiting=True
                )
                self.profile_storage = ProfileStorage(
                    workspace_client=workspace_client,
                    warehouse_id=warehouse_id,
                    table_name="sandbox.venkat.user_profiles"
                )
                
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

        # Semantic cache
        _wc = getattr(self, '_workspace_client_for_cache', None)
        _wh = os.getenv("DATABRICKS_WAREHOUSE_ID")
        if self.personalization_enabled:
            try:
                from databricks.sdk import WorkspaceClient as _WC
                _wc = _WC()
            except Exception:
                pass
        self.semantic_cache = build_cache(workspace_client=_wc, warehouse_id=_wh)
        print(f"✓ Semantic Cache: {self.semantic_cache.stats().get('backend', 'unknown')} backend")

        # ── Audit Trail ───────────────────────────────────────────────────────
        # AuditWrapper: compliance logging to Delta tables
        # AuditTrailCallback: auto-logs all nodes/LLM calls via LangGraph callback system
        try:
            self.audit_wrapper = AuditWrapper(
                catalog = os.getenv("AUDIT_CATALOG", "shopping_assistant"),
            )
            # ── NEW: callback adapter wires audit logging automatically ───────
            self.audit_callback = AuditTrailCallback(self.audit_wrapper)
        except Exception as _e:
            print(f"[AUDIT] AuditWrapper init failed (non-blocking): {_e}")
            self.audit_wrapper  = None
            self.audit_callback = None
        # ─────────────────────────────────────────────────────────────────────

        # Build Graph
        self.app = self._build_graph()
        print("✓ Workflow Ready with Pinecone + RAG Enhancement!")

    def _build_graph(self):
        """Build the LangGraph workflow with RAG enhancement and product discussion"""
        workflow = StateGraph(GraphState)
        
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
        
        workflow.set_entry_point("input_guardrail")
        
        workflow.add_conditional_edges(
            "input_guardrail",
            self.check_guardrail,
            {"pass": "intent_classifier", "fail": END}
        )
        workflow.add_conditional_edges(
            "intent_classifier",
            self.check_intent,
            {
                "shopping":          "personalization",
                "shopping_conflict": "clarification",
                "chat":              "response_generator",
                "product_detail":    "response_generator",
            }
        )
        workflow.add_conditional_edges(
            "clarification",
            self.check_clarification_needed,
            {"clarify": END, "continue": "personalization"}
        )
        workflow.add_edge("personalization", "product_search")
        workflow.add_edge("product_search", "result_validator")
        workflow.add_conditional_edges(
            "result_validator",
            self.check_result_count,
            {
                "zero":     "constraint_relaxer",
                "optimal":  "reranker",
                "good":     "reranker",
                "too_many": "clarification",
            }
        )
        workflow.add_conditional_edges(
            "constraint_relaxer",
            self.check_relaxation_done,
            {"continue": "product_search", "done": "reranker"}
        )
        workflow.add_edge("reranker", "response_generator")
        workflow.add_edge("response_generator", "output_guardrail")
        workflow.add_edge("output_guardrail", END)
        
        app = workflow.compile(checkpointer=self.memory)

        # ── NEW: attach callback — one line wires audit logging for all nodes ─
        if getattr(self, 'audit_callback', None):
            app = app.with_config({"callbacks": [self.audit_callback]})
            print("[AUDIT] AuditTrailCallback attached to graph via with_config")
        else:
            print("[AUDIT] WARNING: audit_callback is None — callback not attached")

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

            # _audit_ctx = {
            #     "request_id": state.get("session_id", ""),
            #     "user_id":    state.get("user_id", "anon") or "anon",
            #     "session_id": str(state.get("session_id", "")),
            # }

            if not query:
                return {"error": "Please ask me something! I'm here to help you find bags, wallets, and accessories.", 
                        "history": history, "summary": summary}
            
            if len(query) > 500:
                # log_guardrail(
                #     guardrail_type="input",
                #     status="fail",
                #     issues=["Query too long — exceeds 500 characters"],
                #     corrections_made=False,
                #     latency_ms=(time.time() - _t0) * 1000,
                #     query_preview=query[:120],
                #     **_audit_ctx,
                # )
                # ── KEPT: guardrail_results_raw — callback doesn't cover this table ──
                self._log_input_guardrail(status="fail", score=0.0, triggered_block=True)
                return {"error": "That query is too long. Could you please rephrase it more concisely?",
                        "history": history, "summary": summary}
            
            skip_safety_check = (
                len(query) < 15 or 
                query.lower() in ['hi', 'hello', 'hey', 'thanks', 'thank you', 'yes', 'no', 'ok', 'okay']
            )
            
            if not skip_safety_check:
                with track_time("input_safety_check"):
                    safety_result = self._check_input_safety(query)
                
                if safety_result["status"] == "UNSAFE":
                    print(f"[INPUT GUARDRAIL] Blocked {safety_result['category']}: {safety_result['reason']}")
                    # log_guardrail(
                    #     guardrail_type="input",
                    #     status="fail",
                    #     issues=[f"{safety_result['category']}: {safety_result['reason']}"],
                    #     corrections_made=False,
                    #     latency_ms=(time.time() - _t0) * 1000,
                    #     query_preview=query[:120],
                    #     **_audit_ctx,
                    # )
                    # ── KEPT: guardrail_results_raw — callback doesn't cover this table ──
                    self._log_input_guardrail(status="fail", score=0.0, triggered_block=True)
                    return {
                        "error": safety_result["decline_message"],
                        "history": history,
                        "summary": summary
                    }
                
                print(f"[INPUT GUARDRAIL] ✓ Query passed safety check")
            
            history.append(f"User: {query}")
            
            with track_time("memory_management"):
                memory_result = self.memory_manager.manage_memory(history, summary)
            
            if memory_result["was_summarized"]:
                print(f"[MEMORY] Summarization triggered - kept last {len(memory_result['history'])} turns")

            # log_guardrail(
            #     guardrail_type="input",
            #     status="pass",
            #     issues=[],
            #     corrections_made=False,
            #     latency_ms=(time.time() - _t0) * 1000,
            #     query_preview=query[:120],
            #     **_audit_ctx,
            # )
            # ── KEPT: guardrail_results_raw — callback doesn't cover this table ──
            self._log_input_guardrail(status="pass", score=1.0, triggered_block=False)

            return {
                "query": query,
                "error": None,
                "history": memory_result["history"],
                "summary": memory_result["summary"]
            }

    def _log_input_guardrail(self, status: str, score: float, triggered_block: bool) -> None:
        """
        KEPT MANUAL — writes to guardrail_results_raw.
        Callback only handles node_executions_raw, model_outputs_raw, tool_calls_raw.
        Guardrail results must still be logged manually here.
        """
        if not self.audit_wrapper:
            return
        threading.Thread(
            target=self.audit_wrapper.log_guardrail,
            kwargs={
                "trace_id":        _current_trace_id.get(),
                "policy_name":     "input_content_safety",
                "score":           score,
                "result":          status,
                "triggered_block": triggered_block,
                "subject_ref":     _current_subject_ref.get() or None,
            },
            daemon=True,
        ).start()

    def intent_classifier_node(self, state: GraphState):
        """Node 2: Intent Classifier with Preference Merging and Product-Detail Detection."""
        _t0 = time.time()
        _trace_id = _current_trace_id.get()
        _subject_ref = _current_subject_ref.get() or None

        def _audit_return(return_val: dict) -> dict:
            # ── COMMENTED: now handled automatically by AuditTrailCallback.on_chain_end ──
            # if self.audit_wrapper:
            #     threading.Thread(
            #         target=self.audit_wrapper.log_node_execution,
            #         kwargs={
            #             "trace_id":    _trace_id,
            #             "node_name":   "intent_classifier",
            #             "node_type":   "router",
            #             "node_order":  2,
            #             "status":      "success",
            #             "node_output": return_val.get("intent"),
            #             "latency_ms":  (time.time() - _t0) * 1000,
            #             "subject_ref": _subject_ref,
            #         },
            #         daemon=True,
            #     ).start()
            return return_val

        with track_time("node_intent_classifier"):
            query = state["query"]
            history = state.get("history", [])
            summary = state.get("summary", "")
            previous_preferences = state.get("preferences")
            clarification_asked_last_turn = state.get("clarification_asked_last_turn", False)

            product_result = self._detect_product_intent(state)
            if product_result == "exit_discussion":
                state = {
                    **state,
                    "product_discussion_mode": False,
                    "selected_product_id": None,
                    "product_context": None,
                }
            elif product_result is not None:
                return _audit_return(product_result)
            
            context = self.memory_manager.format_context_for_llm(history, summary)
            
            if clarification_asked_last_turn:
                action = self._detect_clarification_response(query, previous_preferences)
                free_text_response = False
                if not action and len(query.split()) >= 2:
                    print(f"[WORKFLOW] Clarification response unrecognised, defaulting to REPLACE for: {query}")
                    action = "REPLACE"
                    free_text_response = True
                if action:
                    print(f"[WORKFLOW] User chose action: {action} - bypassing intent classifier")

                    if free_text_response:
                        extracted = self.intent_classifier.process_query(query, context)
                        pending_new_preferences = extracted.preferences
                        print(f"[WORKFLOW] Free-text REPLACE: extracted preferences from query: {pending_new_preferences}")
                    else:
                        pending_new_preferences = state.get("new_preferences")
                    
                    if not pending_new_preferences:
                        print("[WORKFLOW] Warning: No pending new_preferences, extracting from history")
                        if history and len(history) >= 2:
                            for i in range(len(history) - 2, -1, -1):
                                if history[i].startswith("User: "):
                                    potential_query = history[i].replace("User: ", "").strip()
                                    if potential_query not in ["1", "2", "3", "1️⃣", "2️⃣", "3️⃣", "one", "two", "three", "option 1", "option 2", "option 3", "first", "second", "third", "start fresh", "replace", "replace the filter", "let make it a new fresh start"]:
                                        temp_query = self.intent_classifier.process_query(potential_query, "")
                                        pending_new_preferences = temp_query.preferences
                                        break
                    
                    original_query = query
                    if not free_text_response and history and len(history) >= 2:
                        for i in range(len(history) - 2, -1, -1):
                            if history[i].startswith("User: "):
                                potential_query = history[i].replace("User: ", "").strip()
                                if potential_query not in ["1", "2", "3", "1️⃣", "2️⃣", "3️⃣", "one", "two", "three", "option 1", "option 2", "option 3", "first", "second", "third", "start fresh", "replace", "replace the filter", "let make it a new fresh start"]:
                                    original_query = potential_query
                                    print(f"[WORKFLOW] Restored original query: {original_query}")
                                    break
                    
                    if action == "START_FRESH":
                        print(f"[WORKFLOW] START_FRESH - using only new preferences: {pending_new_preferences}")
                        final_preferences = pending_new_preferences or SearchPreferences()
                    elif action == "REPLACE":
                        print(f"[WORKFLOW] REPLACE - updating colors/categories only")
                        final_preferences = self._merge_preferences(previous_preferences, pending_new_preferences, "preference_update")
                    else:
                        print(f"[WORKFLOW] MERGE - combining all preferences")
                        final_preferences = self._merge_preferences(previous_preferences, pending_new_preferences, "shopping")
                    
                    return _audit_return({
                        "intent": "shopping",
                        "query": original_query,
                        "preferences": final_preferences,
                        "new_preferences": None,
                        "previous_preferences": final_preferences,
                        "user_action_choice": None,
                        "clarification_asked_last_turn": False
                    })
            
            with track_time("intent_classification"):
                user_query = self.intent_classifier.process_query(query, context)
            
            print(f"[WORKFLOW] Intent: {user_query.intent}")
        print(f"[WORKFLOW] Preferences: {user_query.preferences}")
        
        new_preferences = user_query.preferences
        merge_mode = user_query.intent
        
        merged_preferences = self._merge_preferences(
            previous_preferences, 
            user_query.preferences,
            merge_mode
        )
        
        print(f"[WORKFLOW] Merged preferences: {merged_preferences}")

        clear_discussion = (
            state.get("product_discussion_mode")
            and user_query.intent in ("shopping", "preference_update")
        )

        return _audit_return({
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
        })
    
    def _detect_clarification_response(self, query: str, previous_preferences) -> str:
        """Detect if user is responding with START_FRESH, MERGE, or REPLACE choice"""
        query_lower = query.lower().strip()
        
        if query_lower in ["1", "1️⃣", "one", "option 1", "first"]:
            return "START_FRESH"
        elif query_lower in ["2", "2️⃣", "two", "option 2", "second"]:
            return "MERGE"
        elif query_lower in ["3", "3️⃣", "three", "option 3", "third"]:
            return "REPLACE"
        
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
        
        return None

    def product_search_node(self, state: GraphState):
        """Node 3: Product Search using Vector Search (MCP)"""
        if self.vector_client is None:
            print("[SEARCH] vector_client is None", file=sys.stderr)
            return {"results": [], "error": "Vector search unavailable", "result_count_status": "zero"}
        with track_time("node_product_search"):
            _t0 = time.time()
            preferences = state["preferences"]
            query = state["query"]

            try:
                with track_time("embedding_generation"):
                    search_query = self._build_search_query(preferences)
                    query_embedding = self.embeddings.embed_query(search_query)
                
                filters = self._build_pinecone_filters(preferences)
                
                print(f"[SEARCH] Query: {search_query}")
                print(f"[SEARCH] Filters: {filters}")
                print(f"[SEARCH] Vector dim: {len(query_embedding)}")
                
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
                
                results = []
                seen_ids = set()
                filtered_by_price = 0
                
                print(f"[PRICE FILTER] price_min={preferences.price_min}, price_max={preferences.price_max}", file=sys.stderr)
                
                for match in search_results:
                    product_id = match.get('id')
                    
                    if product_id in seen_ids:
                        continue
                    
                    metadata = match.get('metadata', {})
                    
                    if preferences.price_min is not None or preferences.price_max is not None:
                        try:
                            price_value = metadata.get('price') or metadata.get('price_from')
                            
                            if isinstance(price_value, (int, float)):
                                price = float(price_value)
                            elif isinstance(price_value, str):
                                price = float(price_value.replace('$', '').replace(',', '').strip())
                            else:
                                price = None
                            
                            if price is not None:
                                product_name = metadata.get('name', 'Unknown')
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
                            print(f"[SEARCH] Price conversion warning for {product_id}: {e}", file=sys.stderr)
                            pass

                    category_clean = metadata.get('category', '')
                    if preferences.categories and not self._category_matches(category_clean, preferences.categories):
                        continue
                    if preferences.excluded_categories and self._category_matches(category_clean, preferences.excluded_categories):
                        continue

                    results.append({
                        'id': product_id,
                        'score': match.get('score', 0),
                        'metadata': metadata
                    })
                    seen_ids.add(product_id)
                
                print(f"[SEARCH] Found {len(results)} unique products (after post-filtering and deduplication)")
                print(f"[PRICE FILTER] Total filtered by price: {filtered_by_price}/{len(search_results)}", file=sys.stderr)

                # ── KEPT MANUAL: custom vector search — not a LangChain tool,
                #    so callback's on_tool_end won't catch it ──────────────────
                if self.audit_wrapper:
                    threading.Thread(
                        target=self.audit_wrapper.log_tool_call,
                        kwargs={
                            "trace_id":    _current_trace_id.get(),
                            "tool_name":   "product_search",
                            "tool_inputs": {
                                "query_length": len(search_query),
                                "filters":      filters,
                                "top_k":        50,
                                "cache_hit":    cache_hit,
                            },
                            "tool_outputs": {
                                "result_count": len(results),
                            },
                            "status":      "success",
                            "latency_ms":  (time.time() - _t0) * 1000,
                            "subject_ref": _current_subject_ref.get() or None,
                        },
                        daemon=True,
                    ).start()
                return {"results": results}

            except Exception as e:
                print(f"[ERROR] Vector search failed: {e}")
                # ── KEPT MANUAL: error path tool call ─────────────────────────
                if self.audit_wrapper:
                    threading.Thread(
                        target=self.audit_wrapper.log_tool_call,
                        kwargs={
                            "trace_id":      _current_trace_id.get(),
                            "tool_name":     "product_search",
                            "tool_inputs":   {"query_length": len(query) if query else 0},
                            "status":        "error",
                            "error_message": str(e)[:500],
                            "latency_ms":    (time.time() - _t0) * 1000,
                            "subject_ref":   _current_subject_ref.get() or None,
                        },
                        daemon=True,
                    ).start()
                return {"results": [], "error": str(e)}

    # --- Conditional Logic ---
    
    def check_guardrail(self, state: GraphState):
        if state.get("error"):
            return "fail"
        return "pass"

    def check_intent(self, state: GraphState):
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
        status = state.get("result_count_status", "zero")
        return status
    
    def check_clarification_needed(self, state: GraphState):
        if state.get("needs_clarification"):
            return "clarify"
        return "continue"
    
    def check_relaxation_done(self, state: GraphState):
        if state.get("relaxation_level", 0) >= 4:
            return "done"
        return "continue"

    # --- Enhancement Nodes ---
    
    def clarification_node(self, state: GraphState):
        """Deliberate pause node — only reached when a pause is genuinely warranted."""
        _t0 = time.time()
        _trace_id = _current_trace_id.get()
        _subject_ref = _current_subject_ref.get() or None

        def _audit_return(return_val: dict, path: str, status: str = "success") -> dict:
            # ── COMMENTED: now handled automatically by AuditTrailCallback.on_chain_end ──
            # if self.audit_wrapper:
            #     threading.Thread(
            #         target=self.audit_wrapper.log_node_execution,
            #         kwargs={
            #             "trace_id":    _trace_id,
            #             "node_name":   "clarification",
            #             "node_type":   "router",
            #             "node_order":  9,
            #             "status":      status,
            #             "node_output": path,
            #             "latency_ms":  (time.time() - _t0) * 1000,
            #             "subject_ref": _subject_ref,
            #         },
            #         daemon=True,
            #     ).start()
            return return_val
        
        previous_preferences = state.get("previous_preferences")
        new_preferences      = state.get("new_preferences")

        if state.get("result_count_status") == "too_many":
            question = state.get(
                "clarification_question",
                "I found a lot of matching bags. Could you help me narrow it down? "
                "Do you have a preferred colour, brand, or budget in mind?"
            )
            print("[CLARIFICATION] too_many path — asking refinement question")
            return _audit_return({
                "needs_clarification":           True,
                "clarification_question":        question,
                "clarification_asked_last_turn": True,
                "result_count_status":           None,
            }, "too_many")

        if previous_preferences and new_preferences:
            conflict_message  = self._format_conflict_message(previous_preferences, new_preferences)
            enhanced_message  = self._format_clarification_options(
                conflict_message, previous_preferences, new_preferences, "REPLACE"
            )
            print("[CLARIFICATION] Preference conflict — asking START_FRESH / MERGE / REPLACE")
            return _audit_return({
                "needs_clarification":           True,
                "clarification_question":        enhanced_message,
                "clarification_asked_last_turn": True,
                "preferences":                   previous_preferences,
                "new_preferences":               new_preferences,
            }, "preference_conflict")

        print("[CLARIFICATION] Warning: reached with no actionable state — passing through")
        return _audit_return({
            "needs_clarification": False,
            "clarification_question": None,
            "clarification_asked_last_turn": False,
        }, "no_actionable_state", status="error")
    
    def _classify_preference_action(self, previous_preferences, new_preferences, query: str) -> dict:
        """Use LLM to determine if user wants to START_FRESH, MERGE, or REPLACE"""
        import json
        import re
        
        try:
            prev_prefs_str = self._format_preferences_for_prompt(previous_preferences)
            new_prefs_str = self._format_preferences_for_prompt(new_preferences)
            
            prompt = load_prompt("preference_action_classifier", {
                "previous_preferences": prev_prefs_str,
                "new_request": query,
                "new_preferences": new_prefs_str
            })
            
            response = self.chat_model.invoke(prompt)
            content = response.content if hasattr(response, 'content') else str(response)
            
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group(0))
                return result
            
            return {
                "action": "MERGE",
                "confidence": "low",
                "clarification_needed": True,
                "clarification_message": "I'm not sure if you want to add these filters or start a new search. What would you prefer?"
            }
            
        except Exception as e:
            print(f"[CLARIFICATION] Error in action classification: {e}")
            return {
                "action": "MERGE",
                "confidence": "low",
                "clarification_needed": True,
                "clarification_message": "How would you like to proceed with your search?"
            }
    
    def _detect_preference_conflicts(self, prev_prefs, new_prefs) -> bool:
        """Detect if new preferences conflict with previous ones"""
        if prev_prefs.colors and new_prefs.colors:
            if set(prev_prefs.colors) != set(new_prefs.colors):
                return True
        if prev_prefs.brands and new_prefs.brands:
            if set(prev_prefs.brands) != set(new_prefs.brands):
                return True
        if prev_prefs.categories and new_prefs.categories:
            if set(prev_prefs.categories) != set(new_prefs.categories):
                return True
        if prev_prefs.materials and new_prefs.materials:
            if set(prev_prefs.materials) != set(new_prefs.materials):
                return True
        
        has_significant_previous = bool(prev_prefs.colors or prev_prefs.categories or prev_prefs.materials)
        has_significant_new = bool(new_prefs.colors or new_prefs.categories or new_prefs.materials)
        
        if has_significant_previous and has_significant_new:
            prev_dimensions = set()
            new_dimensions = set()
            
            if prev_prefs.colors: prev_dimensions.add('colors')
            if prev_prefs.categories: prev_dimensions.add('categories')
            if prev_prefs.materials: prev_dimensions.add('materials')
            
            if new_prefs.colors: new_dimensions.add('colors')
            if new_prefs.categories: new_dimensions.add('categories')
            if new_prefs.materials: new_dimensions.add('materials')
            
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
        
        if not conflicts:
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
        if prefs.categories: parts.append(f"Style: {', '.join(prefs.categories)}")
        if prefs.colors: parts.append(f"Colors: {', '.join(prefs.colors)}")
        if prefs.brands: parts.append(f"Brands: {', '.join(prefs.brands)}")
        if prefs.price_min or prefs.price_max:
            price_range = []
            if prefs.price_min: price_range.append(f"${prefs.price_min}+")
            if prefs.price_max: price_range.append(f"under ${prefs.price_max}")
            parts.append(f"Price: {' '.join(price_range)}")
        if prefs.materials: parts.append(f"Material: {', '.join(prefs.materials)}")
        return " | ".join(parts) if parts else "No specific preferences"
    
    def _format_clarification_options(self, message: str, prev_prefs, new_prefs, suggested_action: str) -> str:
        """Format clarification message with clear options"""
        base_message = message if message else "I see you have a new request."
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
            return new_preferences or SearchPreferences()
        elif action == "REPLACE":
            print("[CLARIFICATION] Applying REPLACE - updating specific attributes")
            return self._merge_preferences(previous_preferences, new_preferences, "preference_update")
        else:
            print("[CLARIFICATION] Applying MERGE - combining preferences")
            return self._merge_preferences(previous_preferences, new_preferences, "shopping")
    
    def personalization_node(self, state: GraphState):
        """Node: Generate personalized context from history and user profile"""
        _t0 = time.time()
        _trace_id = _current_trace_id.get()
        _subject_ref = _current_subject_ref.get() or None

        def _log(status: str, output_summary: str) -> None:
            # ── COMMENTED: now handled automatically by AuditTrailCallback.on_chain_end ──
            # if self.audit_wrapper:
            #     threading.Thread(
            #         target=self.audit_wrapper.log_node_execution,
            #         kwargs={
            #             "trace_id":    _trace_id,
            #             "node_name":   "personalization",
            #             "node_type":   "llm",
            #             "node_order":  3,
            #             "status":      status,
            #             "node_output": output_summary,
            #             "latency_ms":  (time.time() - _t0) * 1000,
            #             "subject_ref": _subject_ref,
            #         },
            #         daemon=True,
            #     ).start()
            pass  # callback handles this now

        if not self.personalization_enabled:
            result = self._legacy_personalization(state)
            _log("success", "legacy_personalization")
            return result

        user_id = state.get("user_id")
        query = state.get("query", "")
        history = state.get("history", [])
        summary = state.get("summary", "")
        preferences = state.get("preferences")
        last_discussed_product = state.get("last_discussed_product")
        personalization_session = state.get("personalization_session")

        if not user_id:
            print("[PERSONALIZATION] No user_id provided, using legacy personalization")
            result = self._legacy_personalization(state)
            _log("success", "legacy_personalization_no_user_id")
            return result

        try:
            from .personalization.models import UserProfile, SessionState
            from .personalization.engine import RateLimitExceeded

            current_profile = self.profile_storage.load_profile(user_id)
            current_session = None
            if personalization_session:
                current_session = SessionState.from_dict(personalization_session)

            try:
                updated_profile, updated_session, context = self.personalization_engine.process_message(
                    user_id=user_id,
                    user_message=query,
                    current_profile=current_profile,
                    current_session=current_session
                )

                self.profile_storage.save_profile(updated_profile)
                personalization_context = context.get("profile_summary", "")

                if last_discussed_product:
                    personalization_context += f". Recently discussed: {last_discussed_product.get('name')} by {last_discussed_product.get('brand')}"

                merged_preferences = self._merge_personalization_with_preferences(
                    current_preferences=preferences,
                    personalization_context=context,
                    updated_profile=updated_profile
                )

                print(f"[PERSONALIZATION] Profile updated for user {user_id}")
                print(f"[PERSONALIZATION] Context: {personalization_context}")

                result = {
                    "personalization_context": personalization_context,
                    "personalization_session": updated_session.to_dict(),
                    "preferences": merged_preferences or preferences
                }
                _log("success", (personalization_context or "")[:500])
                return result

            except RateLimitExceeded as e:
                print(f"[PERSONALIZATION] Rate limit exceeded for user {user_id}: {e}")
                result = {
                    "personalization_context": "Rate limit reached. Using previous preferences.",
                    "personalization_session": personalization_session
                }
                _log("skipped", "rate_limited")
                return result

        except Exception as e:
            print(f"[PERSONALIZATION] Error: {e}")
            import traceback
            print(f"[PERSONALIZATION] Traceback: {traceback.format_exc()}")
            result = self._legacy_personalization(state)
            _log("success", "legacy_personalization_fallback")
            return result
    
    def _legacy_personalization(self, state: GraphState):
        """Legacy personalization implementation (simple history-based)"""
        history = state.get("history", [])
        summary = state.get("summary", "")
        preferences = state.get("preferences")
        last_discussed_product = state.get("last_discussed_product")
        personalization_context = None
        
        if history or summary:
            try:
                history_text = summary if summary else "\\n".join(history[-5:])
                context_parts = []
                selected_products = []
                for entry in history:
                    if "[Selected Product]" in entry:
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
                
                if preferences:
                    if preferences.colors: context_parts.append(f"User prefers {', '.join(preferences.colors[:2])} colors")
                    if preferences.materials: context_parts.append(f"Interested in {', '.join(preferences.materials[:2])} materials")
                    if preferences.categories: context_parts.append(f"Looking for {', '.join(preferences.categories[:2])}")
                    if preferences.price_max: context_parts.append(f"Budget-conscious: up to ${preferences.price_max}")
                
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
            learned_brands = []
            learned_colors = []
            learned_materials = []
            
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
            
            learned_categories = []
            categories_category = updated_profile.preferences.get("categories")
            if categories_category and categories_category.items:
                learned_categories = [
                    pref.value for pref in 
                    sorted(categories_category.items.values(), key=lambda x: x.weight, reverse=True)[:3]
                ]
            
            excluded_colors_set = set(colors_category.disliked_items.keys()) if colors_category else set()
            excluded_brands_set = set(brands_category.disliked_items.keys()) if brands_category else set()
            excluded_materials_set = set(materials_category.disliked_items.keys()) if materials_category else set()
            
            learned_colors = [c for c in learned_colors if c not in excluded_colors_set]
            learned_brands = [b for b in learned_brands if b not in excluded_brands_set]
            learned_materials = [m for m in learned_materials if m not in excluded_materials_set]
            
            current_colors = current_preferences.colors if current_preferences.colors else []
            current_brands = current_preferences.brands if current_preferences.brands else []
            current_materials = current_preferences.materials if current_preferences.materials else []
            
            current_colors = [c for c in current_colors if c not in excluded_colors_set]
            current_brands = [b for b in current_brands if b not in excluded_brands_set]
            current_materials = [m for m in current_materials if m not in excluded_materials_set]
            
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
        _t0 = time.time()
        result = self.result_validator.process(state)

        # ── COMMENTED: now handled automatically by AuditTrailCallback.on_chain_end ──
        # if self.audit_wrapper:
        #     threading.Thread(
        #         target=self.audit_wrapper.log_node_execution,
        #         kwargs={
        #             "trace_id":    _current_trace_id.get(),
        #             "node_name":   "result_validator",
        #             "node_type":   "router",
        #             "node_order":  5,
        #             "status":      "success",
        #             "node_output": result.get("result_count_status"),
        #             "latency_ms":  (time.time() - _t0) * 1000,
        #             "subject_ref": _current_subject_ref.get() or None,
        #         },
        #         daemon=True,
        #     ).start()

        return result
    
    def constraint_relaxer_node(self, state: GraphState):
        """Node: Relax search constraints for better results"""
        from .rag_utils import progressive_filter_relaxation, format_relaxation_message
        _t0 = time.time()
        _trace_id = _current_trace_id.get()
        _subject_ref = _current_subject_ref.get() or None

        def _audit_return(return_val: dict, summary: str) -> dict:
            # ── COMMENTED: now handled automatically by AuditTrailCallback.on_chain_end ──
            # if self.audit_wrapper:
            #     threading.Thread(
            #         target=self.audit_wrapper.log_node_execution,
            #         kwargs={
            #             "trace_id":    _trace_id,
            #             "node_name":   "constraint_relaxer",
            #             "node_type":   "router",
            #             "node_order":  10,
            #             "status":      "success",
            #             "node_output": summary,
            #             "latency_ms":  (time.time() - _t0) * 1000,
            #             "subject_ref": _subject_ref,
            #         },
            #         daemon=True,
            #     ).start()
            return return_val
        
        results = state.get("results", [])
        preferences = state.get("preferences")
        relaxation_level = state.get("relaxation_level", 0)
        
        print(f"\n[CONSTRAINT RELAXER] Current level: {relaxation_level}, Results: {len(results)}")
        
        if len(results) > 0:
            print(f"[CONSTRAINT RELAXER] Already have {len(results)} results, stopping relaxation")
            return _audit_return({"result_count_status": "optimal"}, f"stopped_have_{len(results)}_results")

        if relaxation_level >= 4:
            print(f"[CONSTRAINT RELAXER] Max level reached, giving up")
            tried = []
            if preferences:
                if preferences.brands: tried.append(f"brand ({', '.join(preferences.brands)})")
                if preferences.colors: tried.append(f"colour ({', '.join(preferences.colors)})")
                if preferences.materials: tried.append(f"material ({', '.join(preferences.materials)})")
                if preferences.categories: tried.append(f"category ({', '.join(preferences.categories)})")
            tried_text = ", ".join(tried) if tried else "your criteria"
            return _audit_return({
                "relaxation_message": (
                    f"I searched broadly but couldn't find any products matching {tried_text}. "
                    f"Try a different combination — for example, a different category or price range."
                )
            }, f"gave_up_at_level_{relaxation_level}_tried_{tried_text}")

        new_level = relaxation_level + 1
        print(f"[CONSTRAINT RELAXER] Applying relaxation level {new_level}")

        if preferences:
            try:
                relaxed_preferences = progressive_filter_relaxation(preferences, new_level)
                relaxation_msg = format_relaxation_message(new_level, 0, 0, preferences=preferences)

                return _audit_return({
                    "preferences": relaxed_preferences,
                    "relaxation_level": new_level,
                    "relaxation_message": relaxation_msg,
                    "result_count_status": "zero"
                }, f"level_{new_level}_applied: {relaxation_msg}")
            except Exception as e:
                print(f"[CONSTRAINT RELAXER] Error: {e}")

        return _audit_return({
            "relaxation_level": new_level,
            "result_count_status": "zero",
            "relaxation_message": "Broadening search criteria..."
        }, f"level_{new_level}_fallback_no_preferences")
    
    def reranker_node(self, state: GraphState):
        """Node: Rerank products using LLM"""
        with track_time("node_reranker"):
            _t0 = time.time()
            result = self.reranker.process(state)

        # ── COMMENTED: now handled automatically by AuditTrailCallback.on_chain_end ──
        # node_metadata with agent_id/reasoning still works — pass it via manual call if needed
        # if self.audit_wrapper:
        #     threading.Thread(
        #         target=self.audit_wrapper.log_node_execution,
        #         kwargs={
        #             "trace_id":    _current_trace_id.get(),
        #             "node_name":   "reranker",
        #             "node_type":   "llm",
        #             "node_order":  6,
        #             "status":      "success",
        #             "node_output": f"{len(result.get('reranked_results') or [])} products",
        #             "node_metadata": {
        #                 "agent_id":       "reranker_agent",
        #                 "agent_role":     "ranker",
        #                 "handoff_from":   "result_validator",
        #                 "handoff_reason": "results ready for ranking",
        #                 "reasoning":      "TEST_REASONING_VALUE",
        #             },
        #             "latency_ms":  (time.time() - _t0) * 1000,
        #             "subject_ref": _current_subject_ref.get() or None,
        #         },
        #         daemon=True,
        #     ).start()

        return result

    def response_generator_node(self, state: GraphState):
        """Node: Generate conversational response"""
        with track_time("node_response_generator"):
            result = self.response_generator.process(state)

# ── COMMENTED: AuditTrailCallback.on_llm_end captures model output automatically ──
        if self.audit_wrapper:
            products = state.get("reranked_results") or state.get("results") or []
            recommended_ids = [
                p.get("id") for p in products[:3] if isinstance(p, dict) and p.get("id")
            ]
            threading.Thread(
                target=self.audit_wrapper.log_model_output,
                kwargs={
                    "trace_id":          _current_trace_id.get(),
                    "output_text":       result.get("generated_response", ""),
                    "recommended_items": recommended_ids,
                    "subject_ref":       _current_subject_ref.get() or None,
                },
                daemon=True,
            ).start()

        return result
    
    def output_guardrail_node(self, state: GraphState):
        """Node: Validate output response for safety and accuracy"""
        with track_time("node_output_guardrail"):
            generated_response = state.get("generated_response", "")
            query = state.get("query", "")
            results = state.get("reranked_results") or state.get("results", [])
            preferences = state.get("preferences")
            product_discussion_mode = state.get("product_discussion_mode", False)
            product_context = state.get("product_context")
        
        if product_discussion_mode and product_context:
            products_described = [product_context]
            print(f"\n[OUTPUT GUARDRAIL NODE] Validating response ({len(generated_response)} chars)...")
            print(f"[OUTPUT GUARDRAIL NODE] Product discussion mode - validating against single product")
        else:
            products_described = results[:3] if results else []
            print(f"\n[OUTPUT GUARDRAIL NODE] Validating response ({len(generated_response)} chars)...")
            print(f"[OUTPUT GUARDRAIL NODE] Validating against {len(products_described)} products (top 3 described)")
        
        if not generated_response:
            print("[OUTPUT GUARDRAIL NODE] No generated response, using fallback")
            return {
                "safe_response": "I'm here to help you find products. What are you looking for?",
                "guardrail_status": "pass",
                "guardrail_issues": []
            }
        
        validation_result = self.output_guardrail.validate_response(
            response=generated_response,
            query=query,
            products=products_described,
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

        return {
            "safe_response": validation_result["safe_response"],
            "guardrail_status": validation_result["status"],
            "guardrail_issues": validation_result["issues"],
            "generated_response": validation_result["safe_response"]
        }
    
    def _detect_product_intent(self, state: GraphState):
        """Determine whether this turn is a product-detail interaction."""
        import re as _re
        query = state.get("query", "").lower().strip()
        results = state.get("reranked_results") or state.get("results", [])
        product_discussion_mode = state.get("product_discussion_mode", False)
        product_context = state.get("product_context")

        def _get_info(p):
            if not isinstance(p, dict):
                return None
            if 'metadata' in p:
                m = p['metadata']
                return {'name': m.get('name', 'Unknown'), 'brand': m.get('brand', 'Unknown'),
                        'price': m.get('price', '0'), 'id': p.get('id', ''), 'full': p}
            return {'name': p.get('name', 'Unknown'), 'brand': p.get('brand', 'Unknown'),
                    'price': p.get('price', '0'), 'id': p.get('id', ''), 'full': p}

        id_match = _re.search(r'\(ID:\s*([^\)]+)\)', query, _re.IGNORECASE)
        if id_match:
            product_id = id_match.group(1).strip()
            print(f"[INTENT CLASSIFIER] Explicit product ID detected: {product_id}")

            name_match  = _re.search(r'"([^"]+)"', query)
            brand_match = _re.search(r'by\s+([A-Z\s&]+)(?:\s*$)', query, _re.IGNORECASE)
            product_name  = name_match.group(1)  if name_match  else "the product"
            product_brand = brand_match.group(1).strip() if brand_match else "Unknown Brand"

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

        if product_discussion_mode and product_context:
            if 'metadata' in product_context:
                pid  = product_context['metadata'].get('id', product_context['metadata'].get('product_id', ''))
                pname = product_context['metadata'].get('name', 'Unknown')
            else:
                pid  = product_context.get('id', product_context.get('product_id', ''))
                pname = product_context.get('name', 'Unknown')

            if pid in ('', 'unknown', 'not found', None) or pname == 'Unknown':
                print("[INTENT CLASSIFIER] Stale product context, exiting discussion mode")
                return "exit_discussion"

            print(f"[INTENT CLASSIFIER] Product discussion follow-up → {pname} (ID: {pid})")
            return {
                "intent":                "product_detail",
                "product_discussion_mode": True,
                "product_context":       product_context,
                "selected_product_id":   state.get("selected_product_id"),
                "preferences":           state.get("preferences"),
                "new_preferences":       None,
            }

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
            merged = previous.model_copy(deep=True)
            if new.price_min is not None: merged.price_min = new.price_min
            if new.price_max is not None: merged.price_max = new.price_max
            if new.colors:    merged.colors    = new.colors
            if new.materials: merged.materials = new.materials
            if new.categories: merged.categories = new.categories
            if new.brands:    merged.brands    = new.brands
            if new.features:  merged.features  = list(set(merged.features + new.features))
            if new.excluded_colors:     merged.excluded_colors     = list(set(merged.excluded_colors + new.excluded_colors))
            if new.excluded_brands:     merged.excluded_brands     = list(set(merged.excluded_brands + new.excluded_brands))
            if new.excluded_categories: merged.excluded_categories = list(set(merged.excluded_categories + new.excluded_categories))
            if new.excluded_materials:  merged.excluded_materials  = list(set(merged.excluded_materials + new.excluded_materials))
                
        else:
            merged = previous.model_copy(deep=True)
            if new.price_min is not None:
                merged.price_min = max(merged.price_min or 0, new.price_min)
            if new.price_max is not None:
                if merged.price_max is None:
                    merged.price_max = new.price_max
                else:
                    merged.price_max = min(merged.price_max, new.price_max)

            if new.colors and set(new.colors) != set(merged.colors):
                merged.colors = new.colors
            elif new.colors:
                merged.colors = list(set(merged.colors + new.colors))

            if new.brands and set(new.brands) != set(merged.brands):
                merged.brands = new.brands
            elif new.brands:
                merged.brands = list(set(merged.brands + new.brands))

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
        """Check input query for safety and relevance"""
        try:
            from .prompt_loader import load_prompt
            import re
            
            prompt = load_prompt("input_safety_check", {"query": query})
            response = self.chat_model.invoke(prompt)
            content = response.content if hasattr(response, 'content') else str(response)
            
            status_match = re.search(r'STATUS:\s*(SAFE|UNSAFE)', content, re.IGNORECASE)
            category_match = re.search(r'CATEGORY:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
            reason_match = re.search(r'REASON:\s*(.+?)(?:\n|POLITE)', content, re.IGNORECASE | re.DOTALL)
            message_match = re.search(r'POLITE_DECLINE_MESSAGE:\s*(.+?)(?:\n\n|$)', content, re.IGNORECASE | re.DOTALL)
            
            status = status_match.group(1).upper() if status_match else "SAFE"
            category = category_match.group(1).strip() if category_match else "APPROPRIATE"
            reason = reason_match.group(1).strip() if reason_match else "No issues detected"
            decline_message = message_match.group(1).strip() if message_match else "N/A"
            
            if decline_message in ["N/A", "n/a", ""]:
                decline_message = "I'm here to help you find bags, wallets, and accessories. How can I assist you with shopping today?"
            
            return {"status": status, "category": category, "reason": reason, "decline_message": decline_message}
            
        except Exception as e:
            print(f"[INPUT GUARDRAIL] Safety check error: {e}")
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
        if preferences.materials:  parts.append(f"{', '.join(preferences.materials)}")
        if preferences.colors:     parts.append(f"{', '.join(preferences.colors)}")
        if preferences.brands:     parts.append(f"{', '.join(preferences.brands)}")
        if preferences.features:   parts.append(f"{', '.join(preferences.features)}")
        return " ".join(parts) if parts else "bag"

    def _build_pinecone_filters(self, preferences: SearchPreferences) -> dict:
        """Helper: Build Pinecone metadata filters matching actual index structure"""
        conditions = []

        if preferences.colors:
            color_variations = []
            for color in preferences.colors:
                color_variations.extend([color.lower(), color.title(), color.upper()])
            color_variations = list(set(color_variations))
            print(f"[FILTER DEBUG] Colors: {color_variations}")
            conditions.append({"primary_color": {"$in": color_variations}})
        
        if preferences.excluded_colors:
            if len(preferences.excluded_colors) == 1:
                conditions.append({"primary_color": {"$ne": preferences.excluded_colors[0]}})
            else:
                conditions.append({"primary_color": {"$nin": preferences.excluded_colors}})
        
        if preferences.materials:
            material_variations = []
            for mat in preferences.materials:
                material_variations.extend([mat.lower(), mat.title(), mat.upper()])
            material_variations = list(set(material_variations))
            conditions.append({"material_type": {"$in": material_variations}})
        
        if preferences.excluded_materials:
            if len(preferences.excluded_materials) == 1:
                conditions.append({"material_type": {"$ne": preferences.excluded_materials[0]}})
            else:
                conditions.append({"material_type": {"$nin": preferences.excluded_materials}})
        
        if preferences.excluded_brands:
            if len(preferences.excluded_brands) == 1:
                conditions.append({"brand_clean": {"$ne": preferences.excluded_brands[0]}})
            else:
                conditions.append({"brand_clean": {"$nin": preferences.excluded_brands}})
        
        if len(conditions) == 0:
            return {}
        elif len(conditions) == 1:
            return conditions[0]
        else:
            return {"$and": conditions}

    @staticmethod
    def _generate_category_variations(categories: List[str]) -> List[str]:
        """Build lowercase keyword variations for category substring matching."""
        category_variations = []
        for cat in categories:
            cat_lower = cat.lower()
            category_variations.append(cat_lower)
            if cat_lower.endswith('s'):
                category_variations.append(cat_lower[:-1])
            else:
                category_variations.append(cat_lower + 's')
            if 'tote' in cat_lower:
                category_variations.append('tote')
            elif 'shoulder' in cat_lower:
                category_variations.append('shoulder')
            elif 'crossbody' in cat_lower or 'cross-body' in cat_lower:
                category_variations.extend(['crossbody', 'cross-body'])
            elif 'backpack' in cat_lower:
                category_variations.append('backpack')
        return list(set(category_variations))

    def _category_matches(self, category_clean: Optional[str], categories: List[str]) -> bool:
        """Substring match against the category_clean taxonomy path."""
        if not category_clean or not categories:
            return False
        haystack = category_clean.lower()
        return any(kw in haystack for kw in self._generate_category_variations(categories))

    def process_query(self, query: str, session_id: str, user_id: str = None) -> dict:
        """Main entry point for the app"""
        config = {"configurable": {"thread_id": session_id}}
        
        try:
            state_snapshot = self.app.get_state(config)
            if state_snapshot and state_snapshot.values:
                previous_state = state_snapshot.values
                initial_state = {
                    "query": query,
                    "user_id": user_id,
                    "trace_id": None,
                    "history": previous_state.get("history", []),
                    "summary": previous_state.get("summary", ""),
                    "preferences": previous_state.get("preferences"),
                    "previous_preferences": previous_state.get("preferences"),
                    "reranked_results": None,
                    "results": None,
                    "guardrail_status": None,      
                    "guardrail_issues": None,      
                    "last_discussed_product": previous_state.get("last_discussed_product"),
                    "product_discussion_mode": previous_state.get("product_discussion_mode"),
                    "product_context": previous_state.get("product_context"),
                    "selected_product_id": previous_state.get("selected_product_id"),
                    "clarification_asked_last_turn": previous_state.get("needs_clarification", False),
                    "new_preferences": previous_state.get("new_preferences") if previous_state.get("needs_clarification", False) else None,
                    "user_action_choice": None,
                    "personalization_session": previous_state.get("personalization_session")
                }
            else:
                initial_state = {
                    "query": query,
                    "user_id": user_id,
                    "trace_id": None,
                    "history": [],
                    "summary": "",
                }
        except Exception as e:
            print(f"[MEMORY] Warning: Could not retrieve previous state: {e}")
            initial_state = {
                "query": query,
                "user_id": user_id,
                "trace_id": None,
                "history": [],
                "summary": "",
            }
        
        # ── KEPT: set ContextVars once per request so callback can read them ──
        import uuid as _uuid
        pre_trace_id = str(_uuid.uuid4())
        initial_state["trace_id"] = pre_trace_id
        _current_trace_id.set(pre_trace_id)

        try:
            if self.audit_wrapper and user_id:
                _subj = self.audit_wrapper._compute_refs(user_id)[1]
            else:
                _subj = ''
        except Exception as _e:
            print(f"[AUDIT] subject_ref computation failed (non-blocking): {_e}")
            _subj = ''
        _current_subject_ref.set(_subj)

        # ── KEPT: session logging — needs user email, stays manual ────────────
        _is_new_session = not (
            initial_state.get("history") or initial_state.get("summary")
        )
        if self.audit_wrapper and user_id and _is_new_session:
            threading.Thread(
                target=self.audit_wrapper.log_session,
                kwargs={
                    "session_id":  session_id,
                    "user_email":  user_id,
                    "channel":     "web",
                    "device_type": "unknown",
                },
                daemon=True,
            ).start()

        _graph_t0 = time.time()
        with RequestTrace(query=query, user_id=user_id, session_id=session_id) as rt:
            final_state = self.app.invoke(initial_state, config=config)
            rt.set_result(final_state)
        _graph_ms = (time.time() - _graph_t0) * 1000

        trace_id = rt.trace_id or pre_trace_id
        final_state["trace_id"] = trace_id

        # ── KEPT: top-level interaction + guardrail logs — need final_state ────
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

                # KEPT: log_interaction — one row per request, needs full final_state
                threading.Thread(
                    target=self.audit_wrapper.log_interaction,
                    kwargs={
                        "user_email":            user_id,
                        "user_input":            query,
                        "model_output":          str(_output),
                        "model_name":            os.getenv(
                            "DATABRICKS_CHAT_ENDPOINT",
                            "databricks-meta-llama-3-1-8b-instruct"
                        ),
                        "status":                _status,
                        "trace_id":              pre_trace_id,
                        "session_id":            session_id,
                        "user_country":          _user_country,
                        "system_prompt_version": "v1.0",
                        "mlflow_trace_id":       trace_id,
                        "latency_ms":            _graph_ms,
                        "final_state":           final_state,
                    },
                    daemon=True,
                ).start()

                # KEPT: output guardrail log — needs guardrail_status from final_state
                if final_state.get("guardrail_status"):
                    threading.Thread(
                        target=self.audit_wrapper.log_guardrail,
                        kwargs={
                            "trace_id":        pre_trace_id,
                            "policy_name":     "output_content_safety",
                            "score":           1.0 if final_state.get("guardrail_status") == "pass" else 0.0,
                            "result":          final_state.get("guardrail_status", "pass"),
                            "triggered_block": final_state.get("guardrail_status") == "fail",
                            "subject_ref":     _current_subject_ref.get() or None,
                        },
                        daemon=True,
                    ).start()

            except Exception as _e:
                print(f"[AUDIT] Logging failed (non-blocking): {_e}")

        return final_state
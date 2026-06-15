"""
Node Implementations for the RAG pipeline.
Includes ResultValidator, Reranker, and ResponseGenerator.
Constraint relaxation is handled directly in workflow.py (constraint_relaxer_node).
"""

from typing import Dict, Any, List, Optional
from .models import SearchPreferences, Product
from .rag_utils import (
    format_products_for_llm,
    build_generation_prompt,
    build_reranking_prompt,
    parse_rerank_scores,
)
from .performance import track_time
import json


class ResultValidator:
    """
    Node 6: Validates search results and determines routing
    
    Routes based on result count:
    - 0 results → constraint_relaxer
    - 1-10 results → reranker (optimal)
    - 11-50 results → reranker (good)
    - 50+ results → clarification (too many)
    """
    
    def __init__(self):
        self.optimal_min = 1
        self.optimal_max = 10
        self.good_max = 50
    
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Validate result count and set routing status."""
        results = state.get("results", [])
        count   = len(results)
        query   = state.get("query", "")

        print(f"\n[RESULT VALIDATOR] Found {count} products")

        if count == 0:
            status = "zero"
            print("[RESULT VALIDATOR] Status: ZERO — routing to constraint relaxer")
            return {"result_count_status": status}

        elif count <= self.optimal_max:
            status = "optimal"
            print(f"[RESULT VALIDATOR] Status: OPTIMAL ({count} products) — routing to reranker")
            return {"result_count_status": status}

        elif count <= self.good_max:
            status = "good"
            print(f"[RESULT VALIDATOR] Status: GOOD ({count} products) — routing to reranker")
            return {"result_count_status": status}

        else:
            status = "too_many"
            print(f"[RESULT VALIDATOR] Status: TOO MANY ({count} products) — routing to clarification")

            # Build a clarification question that helps the user narrow down.
            # Extract what the user already specified so we only ask for what's missing.
            preferences = state.get("preferences")
            missing = []
            if preferences:
                if not getattr(preferences, "colors",    None): missing.append("colour")
                if not getattr(preferences, "brands",    None): missing.append("brand")
                if not getattr(preferences, "materials", None): missing.append("material")
                if not getattr(preferences, "price_max", None): missing.append("budget")
                if not getattr(preferences, "categories",None): missing.append("bag type")

            if missing:
                hint = f"For example, could you tell me your preferred {', '.join(missing[:2])}?"
            else:
                hint = "For example, do you have a specific occasion, size, or feature in mind?"

            clarification_question = (
                f"I found {count} bags matching your search — that's quite a lot to go through! "
                f"Let me help you narrow it down. {hint}"
            )

            return {
                "result_count_status":  status,
                "needs_clarification":  True,
                "clarification_question": clarification_question,
                # Mark the flag so the clarification node skips its own logic on next turn
                "clarification_asked_last_turn": False,
            }


class Reranker:
    """
    Node 8: LLM-based reranking for personalized results
    
    Uses LLM to score products based on:
    - User preferences alignment
    - Conversation history
    - Personalization context
    - Price-value ratio
    """
    
    def __init__(self, llm_chat_model):
        """
        Initialize with Databricks ChatDatabricks model
        
        Args:
            llm_chat_model: ChatDatabricks instance for reranking
        """
        self.llm = llm_chat_model
    
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Rerank products using LLM"""
        with track_time("llm_reranking"):
            results = state.get("results", [])
            query = state.get("query", "")
            preferences = state.get("preferences")
            personalization_context = state.get("personalization_context")
            
            if not results:
                print("[RERANKER] No results to rerank")
                return {"reranked_results": []}
            
            if len(results) <= 3:
                # Skip reranking for very few results
                print(f"[RERANKER] Skipping reranking for {len(results)} results (too few)")
                return {"reranked_results": results}
            
            print(f"\n[RERANKER] Reranking {len(results)} products...")
            
            try:
                # Build reranking prompt
                rerank_prompt = build_reranking_prompt(
                    query=query,
                    products=results,
                    preferences=preferences,
                    personalization_context=personalization_context
                )
                
                # Get LLM scores
                # Use ChatDatabricks invoke method
                with track_time("llm_call_reranking"):
                    messages = [
                        {"role": "system", "content": "You are a shopping assistant that ranks products based on relevance."},
                        {"role": "user", "content": rerank_prompt}
                    ]
                    
                    response = self.llm.invoke(messages)
                    llm_response = response.content if hasattr(response, 'content') else str(response)
                    llm_response = llm_response.strip()
                print(f"[RERANKER] LLM Response (first 200 chars): {llm_response[:200]}...")
                
                # Parse scores
                scores_dict = parse_rerank_scores(llm_response)
                
                if not scores_dict:
                    print("[RERANKER] Warning: Could not parse scores, returning original order")
                    return {"reranked_results": results}
                
                # Assign scores to products
                reranked_products = []
                for idx, product in enumerate(results, 1):
                    score_info = scores_dict.get(idx, {"score": 50, "reason": "No score provided"})
                    
                    # Handle both Document objects and dict formats
                    if isinstance(product, dict):
                        # Already a dict from previous reranked_results
                        product_dict = product.copy()
                        product_dict["rerank_score"] = score_info["score"]
                        product_dict["rerank_reason"] = score_info["reason"]
                    else:
                        # Document object from vector store
                        product_dict = {
                            "id": product.metadata.get("product_id", str(idx)),
                            "name": product.metadata.get("name", "Unknown"),
                            "brand": product.metadata.get("brand", "Unknown"),
                            "category": product.metadata.get("bag_style", "Unknown"),
                            "price": product.metadata.get("price", 0),
                            "description": product.page_content if hasattr(product, "page_content") else "",
                            "url": product.metadata.get("url_full", ""),
                            "image_url": product.metadata.get("primary_image_url", ""),
                            "relevance_score": 0.0,
                            "rerank_score": score_info["score"],
                            "rerank_reason": score_info["reason"],
                            "metadata": product.metadata
                        }
                    
                    reranked_products.append(product_dict)
                
                # Sort by rerank score (descending)
                reranked_products.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
                
                # Filter out irrelevant products (score < 30)
                MIN_RELEVANCE_SCORE = 30
                relevant_products = [p for p in reranked_products if p.get("rerank_score", 0) >= MIN_RELEVANCE_SCORE]
                
                filtered_count = len(reranked_products) - len(relevant_products)
                if filtered_count > 0:
                    print(f"[RERANKER] Filtered out {filtered_count} irrelevant products (score < {MIN_RELEVANCE_SCORE})")
                
                if not relevant_products:
                    print(f"[RERANKER] Warning: All products scored below {MIN_RELEVANCE_SCORE}, keeping top 3")
                    relevant_products = reranked_products[:3]  # Keep at least top 3
                
                print(f"[RERANKER] Returning {len(relevant_products)} relevant products")
                print(f"[RERANKER] Score range: {relevant_products[0].get('rerank_score')} - {relevant_products[-1].get('rerank_score')}")
                print(f"[RERANKER] Top 3 scores: {[p.get('rerank_score') for p in relevant_products[:3]]}")  
                
                return {"reranked_results": relevant_products}
            
            except Exception as e:
                print(f"[RERANKER] Error during reranking: {e}")
                print("[RERANKER] Falling back to original order")
                return {"reranked_results": results}


class ResponseGenerator:
    """
    Node 9: Generates natural language response from retrieved products
    
    This is the GENERATION component of RAG:
    - Takes retrieved products (R)
    - Augments with user context (A)
    - Generates conversational response (G)
    """
    
    def __init__(self, llm_chat_model):
        """
        Initialize with Databricks ChatDatabricks model
        
        Args:
            llm_chat_model: ChatDatabricks instance for response generation
        """
        self.llm = llm_chat_model
    
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a user-facing response.

        Two modes, selected automatically:

        product_detail  (product_discussion_mode=True)
            A single known product is in context.  Uses the ``product_detail_response``
            prompt template to give a deep, conversational answer about that product.
            Skips the search pipeline entirely — this path enters directly from
            intent_classifier when intent = "product_detail".

        search_results  (default)
            Uses reranked_results[:3] and the ``build_generation_prompt`` helper.
            Includes hallucination guard, unknown-brand notice, relaxation message,
            and zero-results fallback.

        Both modes write to ``generated_response`` and update ``history``, then
        the single output_guardrail node validates whichever text was produced.
        """
        with track_time("llm_response_generation"):
            query   = state.get("query", "")
            history = state.get("history", [])

            # ── Product-detail mode ───────────────────────────────────────────
            if state.get("product_discussion_mode") and state.get("product_context"):
                return self._generate_product_detail_response(state, query, history)

            # ── Chat mode ─────────────────────────────────────────────────────
            if state.get("intent") == "chat":
                return self._generate_chat_response(state, query, history)

            # ── Search-results mode ───────────────────────────────────────────
            # Use reranked results if available, otherwise use original results
            products = state.get("reranked_results") or state.get("results", [])

            # Take only top 3 products for the LLM to describe (reduces token usage and improves quality)
            products = products[:3]

            preferences = state.get("preferences")
            personalization_context = state.get("personalization_context")
            summary = state.get("summary", "")
            relaxation_message = state.get("relaxation_message")
            
            print(f"\n[RESPONSE GENERATOR] Generating response for top {len(products)} products...")
            
            # Build unknown-brand notice once (used in both zero-results and found-results paths)
            unknown_brand_notice = ""
            if preferences and getattr(preferences, 'unknown_brands', None):
                unknown_list = ", ".join(preferences.unknown_brands)
                unknown_brand_notice = (
                    f"Just so you know, **{unknown_list}** {'is' if len(preferences.unknown_brands) == 1 else 'are'} "
                    f"not currently available in our catalogue. "
                    f"Here are similar styles from our available brands instead."
                )

            # Handle zero results case
            if not products:
                no_results_response = self._generate_no_results_response(
                    query, preferences, relaxation_message
                )
                if unknown_brand_notice:
                    no_results_response = f"{unknown_brand_notice}\n\n{no_results_response}"
                return {"generated_response": no_results_response}
            
            try:
                # Build generation prompt
                history_summary = summary if summary else "\n".join(history[-3:])

                prompt = build_generation_prompt(
                    query=query,
                    products=products,
                    preferences=preferences,
                    personalization_context=personalization_context,
                    history_summary=history_summary
                )

                # Build a product anchor list for the system message so the LLM
                # cannot substitute names from its training knowledge
                product_anchors = []
                for i, p in enumerate(products, 1):
                    meta = p.get('metadata', {}) if isinstance(p, dict) else {}
                    name = meta.get('name') or p.get('name', 'Unknown')
                    brand = meta.get('brand') or p.get('brand', 'Unknown')
                    try:
                        price = float(str(meta.get('price') or p.get('price', 0)).replace('$', '').replace(',', ''))
                    except (ValueError, TypeError):
                        price = 0.0
                    product_anchors.append(f"  {i}. \"{name}\" by {brand} — ${price:.2f}")

                anchor_text = "\n".join(product_anchors)
                product_count = len(products)

                # Generate response using ChatDatabricks
                with track_time("llm_call_response_generation"):
                    messages = [
                        {
                            "role": "system",
                            "content": (
                                f"You are a helpful shopping assistant. "
                                f"You MUST describe ONLY these {product_count} product(s) using their EXACT names as listed:\n"
                                f"{anchor_text}\n\n"
                                f"Rules:\n"
                                f"- Use ONLY the product names listed above. Do NOT invent, rename, or substitute any product.\n"
                                f"- Do NOT add products from your training knowledge.\n"
                                f"- Do NOT add placeholder text, template instructions, or meta-commentary.\n"
                                f"- Stop writing after you have described all {product_count} product(s)."
                            )
                        },
                        {"role": "user", "content": prompt}
                    ]

                    response = self.llm.invoke(messages)

                    generated_text = response.content if hasattr(response, 'content') else str(response)
                    generated_text = generated_text.strip()

                # Post-process: strip template remnants
                import re
                lines = generated_text.split('\n')
                cleaned_lines = []
                for line in lines:
                    if '[Price]' in line or '[Brand]' in line or '[One sentence' in line:
                        continue
                    if re.match(r'^Product \d+:$', line.strip()):
                        continue
                    if 'highlighting actual product features' in line:
                        continue
                    if line.strip().startswith('Note:') and 'no more products' in line.lower():
                        continue
                    # Strip meta-commentary lines the LLM sometimes adds
                    if re.match(r'^(Alternatively|Note:|However,|If the user|The corrected)', line.strip()):
                        continue
                    cleaned_lines.append(line)

                generated_text = '\n'.join(cleaned_lines)
                generated_text = re.sub(r'\n{3,}', '\n\n', generated_text).strip()

                # Hallucination guard: verify at least one actual product name appears in the response.
                # Small models (8B) can ignore instructions and substitute names from training data.
                def _first_word(s):
                    return s.split()[0].lower() if s.split() else ''

                actual_names = [
                    (p.get('metadata', {}) if isinstance(p, dict) else {}).get('name') or p.get('name', '')
                    for p in products
                ]
                response_lower = generated_text.lower()
                grounded = any(
                    name and (name.lower() in response_lower or _first_word(name) in response_lower)
                    for name in actual_names
                )

                if not grounded:
                    print("[RESPONSE GENERATOR] Hallucination detected — product names not found in response. Using grounded template.")
                    generated_text = self._generate_grounded_response(products, query)
                else:
                    # Format spacing
                    generated_text = re.sub(r'(\.) (\*\*[A-Z])', r'\1\n\n\2', generated_text)
                    generated_text = re.sub(r'(\*\*\$[\d.]+\*\*) ([A-Z])', r'\1\n\2', generated_text)

                if unknown_brand_notice:
                    generated_text = f"{unknown_brand_notice}\n\n{generated_text}"
                elif relaxation_message:
                    generated_text = f"{relaxation_message}\n\n{generated_text}"

                print(f"[RESPONSE GENERATOR] Generated response ({len(generated_text)} chars)")
                print(f"[RESPONSE GENERATOR] Preview: {generated_text[:150]}...")

                history_entry = f"Assistant: {generated_text}"
                return {
                    "generated_response": generated_text,
                    "history": history + [history_entry],
                }

            except Exception as e:
                print(f"[RESPONSE GENERATOR] Error generating response: {e}")
                fallback = self._generate_grounded_response(products, query)
                history_entry = f"Assistant: {fallback}"
                return {
                    "generated_response": fallback,
                    "history": history + [history_entry],
                }
    
    def _generate_chat_response(
        self,
        state: Dict[str, Any],
        query: str,
        history: list,
    ) -> Dict[str, Any]:
        """Generate a conversational reply for non-shopping chat intent.

        Uses the ``chat_response`` prompt template.  The response goes through
        output_guardrail like every other path — no hardcoded strings.
        """
        from .prompt_loader import load_prompt

        conversation_context = "\n".join(history[-4:]) if history else ""

        prompt = load_prompt("chat_response", {
            "query":                query,
            "conversation_context": conversation_context,
        })

        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a helpful shopping assistant for bags and accessories. "
                        "Keep responses concise (2-4 sentences). "
                        "Never invent product names or prices. "
                        "If asked about anything unrelated to bags or shopping, politely redirect."
                    ),
                },
                {"role": "user", "content": prompt},
            ]
            response      = self.llm.invoke(messages)
            response_text = (response.content if hasattr(response, "content") else str(response)).strip()

            print(f"[RESPONSE GENERATOR] Chat response ({len(response_text)} chars)")

            history_entry = f"Assistant: {response_text}"
            return {
                "generated_response": response_text,
                "history":            history + [history_entry],
            }

        except Exception as exc:
            print(f"[RESPONSE GENERATOR] Chat error: {exc}")
            return {
                "generated_response": (
                    "I'm here to help you find bags and accessories! "
                    "What are you looking for today?"
                )
            }

    def _generate_product_detail_response(
        self,
        state: Dict[str, Any],
        query: str,
        history: list,
    ) -> Dict[str, Any]:
        """Generate a deep single-product answer (product_detail intent path).

        Reads product_context from state, normalises the metadata format,
        validates required fields, then calls the LLM with the
        ``product_detail_response`` prompt template.
        """
        from .prompt_loader import load_prompt

        product_context = state.get("product_context", {})

        # Normalise flat vs metadata-wrapped format
        metadata = product_context.get("metadata", product_context) if isinstance(product_context, dict) else {}

        product_name  = metadata.get("name",  "Unknown Product")
        product_id    = metadata.get("id",    metadata.get("product_id", ""))
        product_brand = metadata.get("brand", "Unknown Brand")

        # Guard: missing context
        if not product_context:
            return {"generated_response": "I couldn't find that product. Could you specify which one you're interested in?"}

        # Guard: invalid IDs
        if product_id in ("", "unknown", "not found", None):
            return {"generated_response": "I'm having trouble accessing that product's details. Would you like to search again or select a different product?"}

        # Guard: completely empty metadata
        if product_name == "Unknown Product" and product_brand == "Unknown Brand":
            return {"generated_response": "I found limited information about that product. Would you like to search for other options?"}

        # ── Field helpers ─────────────────────────────────────────────────────
        def _field(val, *fallback_keys, default="[Not available]"):
            if val and str(val).strip().lower() not in ("", "unknown", "not specified", "n/a", "none"):
                return str(val).strip()
            for k in fallback_keys:
                v = metadata.get(k, "")
                if v and str(v).strip().lower() not in ("", "unknown", "not specified", "n/a", "none"):
                    return str(v).strip()
            return default

        description = metadata.get("description", "") or metadata.get("embedding_text", "")
        if not description or str(description).strip() in ("", "No description available"):
            description = "[No detailed description available]"

        from .memory_manager import MemoryManager   # import lazily to avoid circular dep
        # Use last 3 history turns for context without importing the full manager instance
        conversation_context = "\n".join(history[-3:]) if history else ""

        prompt = load_prompt("product_detail_response", {
            "product_name":         product_name,
            "product_brand":        product_brand,
            "product_price":        metadata.get("price", "0"),
            "product_category":     metadata.get("category", metadata.get("bag_style", "bag")),
            "product_color":        _field(metadata.get("primary_color"), "color",       default="[Color not specified]"),
            "product_material":     _field(metadata.get("material_type"), "material_raw", "fabrication",
                                           default="[Material information not available in listing]"),
            "product_dimensions":   _field(metadata.get("dimensions"),    default="[Not specified]"),
            "product_pattern":      _field(metadata.get("pattern"),       default="[Not specified]"),
            "product_gender":       _field(metadata.get("gender"),        default="[Not specified]"),
            "product_description":  description,
            "user_query":           query,
            "conversation_context": conversation_context,
        })

        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a helpful shopping assistant. "
                        "Answer ONLY based on the product information provided. "
                        "If information is missing (marked with [brackets]), clearly state you don't have it. "
                        "NEVER make up details. "
                        "Respond naturally as if talking to a customer."
                    ),
                },
                {"role": "user", "content": prompt},
            ]
            response      = self.llm.invoke(messages)
            response_text = (response.content if hasattr(response, "content") else str(response)).strip()

            print(f"[RESPONSE GENERATOR] Product detail response ({len(response_text)} chars)")

            history_entry = (
                f"Assistant: [Product Discussion - {product_name} (ID: {product_id}) by {product_brand}] "
                f"{response_text}"
            )
            return {
                "generated_response": response_text,
                "history":            history + [history_entry],
                "last_discussed_product": {
                    "id":    product_id,
                    "name":  product_name,
                    "brand": product_brand,
                },
            }

        except Exception as exc:
            print(f"[RESPONSE GENERATOR] Product detail error: {exc}")
            return {
                "generated_response": (
                    f"I found the {product_name} by {product_brand}, "
                    f"priced at ${metadata.get('price', '0')}. "
                    f"What would you like to know about it?"
                )
            }

    def _generate_no_results_response(
        self,
        query: str,
        preferences: Optional[SearchPreferences],
        relaxation_message: Optional[str]
    ) -> str:
        """Generate response when no products found, naming the specific filters that had no matches."""
        if relaxation_message:
            return relaxation_message

        # Build a specific description of what couldn't be matched
        unmatched = []
        suggestions = []
        if preferences:
            if preferences.brands:
                unmatched.append(f"brand: {', '.join(preferences.brands)}")
                suggestions.append("try a different brand or remove the brand filter")
            if preferences.colors:
                unmatched.append(f"colour: {', '.join(preferences.colors)}")
                suggestions.append(f"try a different colour")
            if preferences.materials:
                unmatched.append(f"material: {', '.join(preferences.materials)}")
            if preferences.categories:
                unmatched.append(f"category: {', '.join(preferences.categories)}")
            if preferences.price_max:
                suggestions.append(f"try increasing your budget above ${preferences.price_max:.0f}")

        if unmatched:
            unmatched_text = "; ".join(unmatched)
            base = f"No products found matching {unmatched_text}."
        else:
            base = "No products found matching your criteria."

        if suggestions:
            return f"{base} You could {suggestions[0]}."
        return f"{base} Try broadening your search — for example, remove a filter or adjust the price range."
    
    def _generate_grounded_response(self, products: List[Any], query: str) -> str:
        """Deterministic response built entirely from actual product data — no LLM involved."""
        if not products:
            return "I couldn't find any products matching your search."

        lines = [f"Here {'is' if len(products) == 1 else 'are'} {len(products)} product{'s' if len(products) != 1 else ''} matching your search:\n"]

        for product in products:
            meta = product.get('metadata', {}) if isinstance(product, dict) else getattr(product, 'metadata', {})
            name = meta.get('name') or product.get('name', 'Unknown')
            brand = meta.get('brand') or product.get('brand', 'Unknown')
            color = meta.get('primary_color') or meta.get('color', '')
            material = meta.get('material_type') or meta.get('material', '')
            category = meta.get('category') or product.get('category', '')
            description = (meta.get('embedding_text') or meta.get('description', ''))[:150]

            try:
                price = float(str(meta.get('price') or product.get('price', 0)).replace('$', '').replace(',', ''))
            except (ValueError, TypeError):
                price = 0.0

            product_line = f"**{name}** by {brand} — **${price:.2f}**"
            detail_parts = [p for p in [color, material, category] if p and p.lower() not in ('unknown', '')]
            if detail_parts:
                product_line += f"\n{' · '.join(detail_parts)}"
            if description:
                product_line += f"\n{description}"

            lines.append(product_line)

        return "\n\n".join(lines)

    # Keep old name as alias so any external callers don't break
    def _generate_fallback_response(self, products: List[Any], query: str) -> str:
        return self._generate_grounded_response(products, query)


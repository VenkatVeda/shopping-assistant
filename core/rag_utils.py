"""
RAG Utility Functions for Shopping Assistant
Provides helper functions for Response Generation and Reranking
"""

from typing import List, Dict, Any, Optional
from .models import Product, SearchPreferences
from .prompt_loader import load_prompt
import json
import os


def get_product_by_id(product_id: str, vector_client=None) -> Optional[Dict[str, Any]]:
    """
    Fetch a single product by ID using MCP Vector Search
    
    Args:
        product_id: The product ID to fetch
        vector_client: VectorSearchClient instance (optional, creates new if not provided)
        
    Returns:
        Product data dict with metadata, or None if not found
    """
    try:
        # If no client provided, check if we should use MCP or legacy
        if vector_client is None:
            use_mcp = os.getenv("USE_MCP_VECTOR_SEARCH", "true").lower() == "true"
            
            if use_mcp:
                from .mcp_client import VectorSearchClient
                vector_client = VectorSearchClient()
            else:
                # Legacy Pinecone mode
                from pinecone import Pinecone
                pinecone_api_key = os.getenv("PINECONE_API_KEY")
                if not pinecone_api_key:
                    print("[GET_PRODUCT_BY_ID] PINECONE_API_KEY not found")
                    return None
                
                pc = Pinecone(api_key=pinecone_api_key)
                pinecone_index_name = os.getenv("PINECONE_INDEX_NAME", "bags-index")
                vector_client = pc.Index(pinecone_index_name)
        
        print(f"[GET_PRODUCT_BY_ID] Fetching product ID: {product_id}")
        
        # Use MCP client or legacy Pinecone
        if hasattr(vector_client, 'fetch_by_id'):
            # MCP client
            result = vector_client.fetch_by_id(product_id)
        else:
            # Legacy Pinecone index
            id_variants = [
                str(product_id),
                str(int(product_id)) if str(product_id).isdigit() else None,
                f"{int(product_id):010d}" if str(product_id).isdigit() else None,
                f"{int(product_id):012d}" if str(product_id).isdigit() else None,
            ]
            id_variants = [v for v in id_variants if v is not None]
            
            fetch_result = vector_client.fetch(ids=id_variants)
            
            if fetch_result and 'vectors' in fetch_result:
                for id_variant in id_variants:
                    if id_variant in fetch_result['vectors']:
                        vector_data = fetch_result['vectors'][id_variant]
                        result = {
                            'id': str(product_id),
                            'values': vector_data.get('values', []),
                            'metadata': vector_data.get('metadata', {})
                        }
                        break
                else:
                    result = None
            else:
                result = None
        
        if result:
            print(f"[GET_PRODUCT_BY_ID] ✓ Found product: {result.get('metadata', {}).get('name', 'Unknown')}")
            import sys
            print(f"[GET_PRODUCT_BY_ID] Result structure - keys: {list(result.keys()) if isinstance(result, dict) else 'Not a dict'}", file=sys.stderr)
            if isinstance(result, dict) and 'metadata' in result:
                print(f"[GET_PRODUCT_BY_ID] Metadata keys: {list(result.get('metadata', {}).keys())}", file=sys.stderr)
                print(f"[GET_PRODUCT_BY_ID] Sample metadata: name={result.get('metadata', {}).get('name')}, brand={result.get('metadata', {}).get('brand')}, price={result.get('metadata', {}).get('price')}", file=sys.stderr)
        else:
            print(f"[GET_PRODUCT_BY_ID] ✗ Product {product_id} not found")
        
        return result
            
    except Exception as e:
        print(f"[GET_PRODUCT_BY_ID] Error fetching product {product_id}: {e}")
        import traceback
        traceback.print_exc()
        return None


def format_products_for_llm(products: List[Any], max_products: int = 10) -> str:
    """
    Format products for LLM consumption in prompts
    
    Args:
        products: List of ChromaDB Document objects, Product models, or dicts
        max_products: Maximum number of products to include
        
    Returns:
        Formatted string representation of products
    """
    if not products:
        return "No products found."
    
    formatted_products = []
    
    for idx, product in enumerate(products[:max_products], 1):
        # Handle dict objects from Pinecone (most common case)
        if isinstance(product, dict):
            # Check if metadata is nested (Pinecone format)
            metadata = product.get('metadata', {})
            if metadata:  # Data is in metadata field
                name = metadata.get('name', 'Unknown')
                brand = metadata.get('brand', 'Unknown')
                price = metadata.get('price', '0')
                # Handle price as string or float
                try:
                    price = float(str(price).replace('$', '').replace(',', '').strip())
                except (ValueError, AttributeError):
                    price = 0
                category = metadata.get('category', metadata.get('bag_style', 'Unknown'))
                color = metadata.get('primary_color', metadata.get('color_normalized', 'Unknown'))
                material = metadata.get('material_type', metadata.get('primary_material', 'Unknown'))
                description = metadata.get('embedding_text', metadata.get('description', ''))[:200]
            else:  # Data is at root level (reranker format)
                name = product.get('name', 'Unknown')
                brand = product.get('brand', 'Unknown')
                price = product.get('price', 0)
                category = product.get('category', 'Unknown')
                color = product.get('color', 'Unknown')
                material = product.get('material', 'Unknown')
                description = product.get('description', '')[:200]
        # Handle ChromaDB Document objects
        elif hasattr(product, 'metadata'):
            metadata = product.metadata
            name = metadata.get('name', 'Unknown')
            brand = metadata.get('brand', 'Unknown')
            price = metadata.get('price', 0)
            category = metadata.get('bag_style', 'Unknown')
            color = metadata.get('color_normalized', 'Unknown')
            material = metadata.get('primary_material', 'Unknown')
            description = product.page_content if hasattr(product, 'page_content') else ''
            description = description[:200]
        # Handle Product model objects
        else:
            name = getattr(product, 'name', 'Unknown')
            brand = getattr(product, 'brand', 'Unknown')
            price = getattr(product, 'price', 0)
            category = getattr(product, 'category', 'Unknown')
            color = getattr(product, 'metadata', {}).get('color_normalized', 'Unknown')
            material = getattr(product, 'metadata', {}).get('primary_material', 'Unknown')
            description = getattr(product, 'description', '')[:200]
        
        product_text = f"""
Product {idx}:
- Name: {name}
- Brand: {brand}
- Price: ${price:.2f}
- Category: {category}
- Color: {color}
- Material: {material}
- Description: {description}...
"""
        formatted_products.append(product_text.strip())
    
    return "\n\n".join(formatted_products)


def build_generation_prompt(
    query: str,
    products: List[Any],
    preferences: Optional[SearchPreferences],
    personalization_context: Optional[str] = None,
    history_summary: Optional[str] = None
) -> str:
    """
    Build comprehensive prompt for response generation
    
    Args:
        query: User's original query
        products: Retrieved products
        preferences: Extracted search preferences
        personalization_context: Personalized insights about user
        history_summary: Summary of conversation history
        
    Returns:
        Complete prompt for LLM response generation
    """
    # Format products
    products_text = format_products_for_llm(products, max_products=10)
    
    # Build preferences summary
    prefs_parts = []
    if preferences:
        if preferences.categories:
            prefs_parts.append(f"Categories: {', '.join(preferences.categories)}")
        if preferences.colors:
            prefs_parts.append(f"Colors: {', '.join(preferences.colors)}")
        if preferences.materials:
            prefs_parts.append(f"Materials: {', '.join(preferences.materials)}")
        if preferences.brands:
            prefs_parts.append(f"Brands: {', '.join(preferences.brands)}")
        if preferences.price_min or preferences.price_max:
            price_range = f"${preferences.price_min or 0} - ${preferences.price_max or '∞'}"
            prefs_parts.append(f"Price Range: {price_range}")
        if preferences.features:
            prefs_parts.append(f"Features: {', '.join(preferences.features)}")
        if preferences.closure_types:
            prefs_parts.append(f"Closure Types: {', '.join(preferences.closure_types)}")
        if preferences.strap_types:
            prefs_parts.append(f"Strap Types: {', '.join(preferences.strap_types)}")
    
    preferences_text = "\n- ".join(prefs_parts) if prefs_parts else "No specific preferences"
    
    # Build context sections
    context_sections = []
    
    if history_summary:
        context_sections.append(f"Conversation History:\n{history_summary}")
    
    if personalization_context:
        context_sections.append(f"User Insights:\n{personalization_context}")
    
    context_text = "\n\n".join(context_sections) if context_sections else "This is a new conversation."
    
    # Load prompt template and substitute variables
    prompt = load_prompt("response_generation", {
        "query": query,
        "preferences_text": preferences_text,
        "context_text": context_text,
        "products_text": products_text
    })
    
    return prompt


def build_reranking_prompt(
    query: str,
    products: List[Any],
    preferences: Optional[SearchPreferences],
    personalization_context: Optional[str] = None
) -> str:
    """
    Build prompt for LLM-based product reranking
    
    Args:
        query: User's original query
        products: Products to rerank
        preferences: User preferences
        personalization_context: Personalized insights
        
    Returns:
        Prompt for reranking
    """
    # Format products with indices
    products_text = format_products_for_llm(products, max_products=20)
    
    # Build preferences summary
    prefs_summary = []
    if preferences:
        if preferences.categories:
            prefs_summary.append(f"Looking for: {', '.join(preferences.categories)}")
        if preferences.colors:
            prefs_summary.append(f"Preferred colors: {', '.join(preferences.colors)}")
        if preferences.materials:
            prefs_summary.append(f"Preferred materials: {', '.join(preferences.materials)}")
        if preferences.price_max:
            prefs_summary.append(f"Budget: Up to ${preferences.price_max}")
        if preferences.features:
            prefs_summary.append(f"Must have: {', '.join(preferences.features)}")
    
    prefs_text = "\n".join(prefs_summary) if prefs_summary else "No specific preferences"
    
    # Build personalization context text
    personalization_context_text = f"User Context: {personalization_context}" if personalization_context else ""
    
    # Load prompt template and substitute variables
    prompt = load_prompt("product_reranking", {
        "query": query,
        "prefs_text": prefs_text,
        "personalization_context_text": personalization_context_text,
        "products_text": products_text
    })
    
    return prompt


def parse_rerank_scores(llm_response: str) -> Dict[int, Dict[str, Any]]:
    """
    Parse LLM reranking response to extract scores
    
    Args:
        llm_response: Raw LLM response containing scores
        
    Returns:
        Dictionary mapping product index to score and reason
    """
    try:
        # Try to extract JSON from response
        # Handle cases where LLM adds extra text
        start_idx = llm_response.find('[')
        end_idx = llm_response.rfind(']') + 1
        
        if start_idx == -1 or end_idx == 0:
            # No JSON found, return empty dict
            return {}
        
        json_str = llm_response[start_idx:end_idx]
        scores_list = json.loads(json_str)
        
        # Convert to dict for easy lookup
        scores_dict = {}
        for item in scores_list:
            idx = item.get('product_index', 0)
            score = item.get('score', 50)
            reason = item.get('reason', '')
            scores_dict[idx] = {'score': score, 'reason': reason}
        
        return scores_dict
        
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        print(f"[RERANKING] Warning: Could not parse LLM scores: {e}")
        return {}


def progressive_filter_relaxation(
    preferences: SearchPreferences,
    level: int
) -> SearchPreferences:
    """
    Apply progressive filter relaxation based on level
    
    Relaxation Strategy:
    - Level 0: No relaxation (original filters)
    - Level 1: Remove color filters
    - Level 2: Expand price range by 20%
    - Level 3: Remove material filters
    - Level 4: Remove all filters except category
    
    Args:
        preferences: Original search preferences
        level: Relaxation level (0-4)
        
    Returns:
        Relaxed preferences
    """
    if level == 0:
        return preferences
    
    # Create a copy to avoid modifying original
    relaxed = preferences.model_copy(deep=True)
    
    if level >= 1:
        # Remove color filters
        relaxed.colors = []
        relaxed.excluded_colors = []
        print(f"[RELAXATION] Level {level}: Removed color filters")
    
    if level >= 2:
        # Expand price range by 20%
        if relaxed.price_max:
            relaxed.price_max = relaxed.price_max * 1.2
            print(f"[RELAXATION] Level {level}: Expanded price max to ${relaxed.price_max:.2f}")
        if relaxed.price_min:
            relaxed.price_min = max(0, relaxed.price_min * 0.8)
            print(f"[RELAXATION] Level {level}: Reduced price min to ${relaxed.price_min:.2f}")
    
    if level >= 3:
        # Remove material filters
        relaxed.materials = []
        relaxed.excluded_materials = []
        print(f"[RELAXATION] Level {level}: Removed material filters")
    
    if level >= 4:
        # Remove all filters except category
        relaxed.brands = []
        relaxed.excluded_brands = []
        relaxed.features = []
        relaxed.closure_types = []
        relaxed.strap_types = []
        relaxed.sizes = []
        relaxed.has_zipper = None
        print(f"[RELAXATION] Level {level}: Removed all filters except category")
    
    return relaxed


def format_relaxation_message(level: int, original_count: int, new_count: int, preferences=None) -> str:
    """
    Generate user-friendly message about filter relaxation, naming the specific filters dropped.

    Args:
        level: Relaxation level applied
        original_count: Number of results before relaxation
        new_count: Number of results after relaxation
        preferences: SearchPreferences object BEFORE relaxation (used to name dropped values)
    """
    if level == 0:
        return ""

    # Build a specific description of what was dropped at this level
    dropped = []
    if level >= 1 and preferences and preferences.colors:
        colour_list = ", ".join(preferences.colors)
        dropped.append(f"colour ({colour_list})")
    elif level == 1:
        dropped.append("colour filter")

    if level >= 2 and preferences and (preferences.price_min or preferences.price_max):
        dropped.append("price range")
    elif level == 2:
        dropped.append("price filter")

    if level >= 3 and preferences and preferences.materials:
        material_list = ", ".join(preferences.materials)
        dropped.append(f"material ({material_list})")
    elif level == 3:
        dropped.append("material filter")

    if level >= 4:
        brand_part = ""
        if preferences and preferences.brands:
            brand_list = ", ".join(preferences.brands)
            # Check if any of these brands might not be in catalogue — always note it
            brand_part = f"brand ({brand_list} — not available in our catalogue)"
        else:
            brand_part = "remaining filters"
        dropped.append(brand_part)

    if dropped:
        dropped_text = " and ".join(dropped)
        base_message = f"No exact match found — I removed the {dropped_text} to broaden the search"
    else:
        base_message = "I broadened the search criteria"

    if new_count > 0:
        return f"{base_message} and found {new_count} option{'s' if new_count != 1 else ''} for you."
    else:
        return f"{base_message}, but still found no results."

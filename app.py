"""
Smart Shopping Assistant - Databricks App
Flask backend serving static frontend and API endpoints with LangGraph Workflow
"""

from flask import Flask, send_from_directory, jsonify, request, session, redirect, render_template
from werkzeug.middleware.proxy_fix import ProxyFix
import os
import sys
import logging
import uuid
import secrets
import time
import json as _json
from functools import wraps

# Add core module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'core'))
from core import ShoppingAssistantWorkflow
from core.auth import (
    get_google_auth_url, 
    exchange_code_for_token, 
    get_google_user_info,
    generate_access_token,
    generate_refresh_token,
    verify_token
)
from core.performance import get_monitor, get_tracker, track_time
from core.observability import metrics_store, RequestTrace
from core.evals import EvalRunner
from core.geoip import get_country_from_request
from core.shopping_audit import log_wishlist_action, log_cart_action

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Enable MLflow LangChain autolog — automatically captures prompt_tokens,
# completion_tokens, and total_tokens from every ChatDatabricks / LangChain
# llm.invoke() call and attaches them to the active MLflow span.
try:
    import mlflow
    mlflow.langchain.autolog(
        log_input_examples=False,   # no PII in stored examples
        log_model_signatures=False, # skip model artifact logging
        log_models=False,           # skip model artifact logging
        silent=True,                # suppress verbose autolog warnings
    )
    logger.info("[OBSERVABILITY] MLflow LangChain autolog enabled — token usage will be captured per LLM call")
except Exception as _autolog_err:
    logger.warning(f"[OBSERVABILITY] MLflow autolog not available: {_autolog_err}")

# Initialize Flask app
app = Flask(__name__, static_folder='static', static_url_path='/static')

# Configure session — generate a random key if not explicitly set so every
# deployment has a unique key and session cookies cannot be forged from the source code.
_flask_secret = os.environ.get('FLASK_SECRET_KEY')
if not _flask_secret:
    import secrets as _secrets
    _flask_secret = _secrets.token_hex(32)
    logger.warning("FLASK_SECRET_KEY not set — using a randomly generated key. Sessions will not survive restarts.")
app.secret_key = _flask_secret

# Handle Databricks Apps proxy
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Configuration
app.config['JSON_SORT_KEYS'] = False

# ============================================================================
# PRODUCT DATA HELPERS
# ============================================================================

def clean_display_value(val):
    """Return None for empty / placeholder values so the frontend hides the field."""
    if not val:
        return None
    s = str(val).strip()
    if s.lower() in ('n/a', 'not specified', 'not_specified', 'unknown', 'none', ''):
        return None
    return s


def clean_bag_style(style):
    """
    Convert a raw category value to a human-readable label.
    Handles URL breadcrumb paths like 'women/handbags/cross-body-bags'
    → 'Cross Body Bags'
    """
    if not style:
        return None
    s = str(style).strip()
    if s.lower() in ('n/a', 'not specified', 'unknown', 'none', ''):
        return None
    # URL breadcrumb — take the last non-empty segment
    if '/' in s:
        segments = [seg for seg in s.split('/') if seg]
        s = segments[-1] if segments else s
    # Replace hyphens / underscores with spaces and title-case each word
    cleaned = ' '.join(word.capitalize() for word in s.replace('-', ' ').replace('_', ' ').split())
    return cleaned or None


# ============================================================================
# SILVER TABLE PRODUCT LOOKUP
# ============================================================================

_silver_warehouse_id = os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID")
if not _silver_warehouse_id:
    logger.warning(
        "DATABRICKS_SQL_WAREHOUSE_ID is not set — Silver table product lookups will be skipped. "
        "Set this variable in your Databricks App config or .env file before deploying to production."
    )
_SILVER_TABLE = "sandbox.venkat.product_data_silver_bags"


def fetch_product_from_silver(product_id: str) -> dict | None:
    """
    Query the silver table for a single product and return a rich metadata dict
    the product_detail_response_node can use directly.

    Returns None if the lookup fails or the product is not found.
    """
    if not product_id or product_id in ("unknown", ""):
        return None
    if not _silver_warehouse_id:
        logger.warning("[SILVER] DATABRICKS_SQL_WAREHOUSE_ID not set — skipping Silver lookup")
        return None
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.sql import StatementState, StatementParameterListItem

        w = WorkspaceClient()   # inherits credentials from the Databricks Apps env
        result = w.statement_execution.execute_statement(
            warehouse_id=_silver_warehouse_id,
            statement=f"SELECT * FROM {_SILVER_TABLE} WHERE product_id = :product_id LIMIT 1",
            parameters=[StatementParameterListItem(name="product_id", value=str(product_id))],
            wait_timeout="20s",
        )

        if result.status.state != StatementState.SUCCEEDED:
            logger.warning(
                f"[SILVER] SQL failed for product_id={product_id}: "
                f"{result.status.error}"
            )
            return None

        if (
            not result.result
            or not result.result.data_array
            or len(result.result.data_array) == 0
        ):
            logger.warning(f"[SILVER] No row found for product_id={product_id}")
            return None

        columns = [col.name for col in result.manifest.schema.columns]
        row = result.result.data_array[0]
        silver = dict(zip(columns, row))
        logger.info(f"[SILVER] Fetched product_id={product_id} | {silver.get('name')}")

        # Parse attributes_map JSON (stored as a string in the result)
        attributes_str = ""
        raw_attrs = silver.get("attributes_map")
        if raw_attrs:
            try:
                attrs = _json.loads(raw_attrs) if isinstance(raw_attrs, str) else raw_attrs
                attributes_str = " | ".join(f"{k}: {v}" for k, v in attrs.items())
            except Exception:
                attributes_str = str(raw_attrs)

        # Build the richest possible description for the LLM
        description_parts = []
        if silver.get("long_description_clean"):
            description_parts.append(silver["long_description_clean"])
        if silver.get("complete_description_clean"):
            description_parts.append(silver["complete_description_clean"])
        if attributes_str:
            description_parts.append(f"Additional attributes: {attributes_str}")
        rich_description = "\n\n".join(description_parts) or silver.get("embedding_text", "")

        price = silver.get("price_from") or silver.get("price_to") or 0

        return {
            "name":                    silver.get("name", ""),
            "brand":                   silver.get("brand_clean") or silver.get("brand", ""),
            "price":                   str(price),
            "primary_image_url":       silver.get("primary_image_url") or "",
            "url_full":                silver.get("url_full", ""),
            # Keys used by product_detail_response_node
            "category":                silver.get("category_clean") or silver.get("category", ""),
            "primary_color":           silver.get("primary_color", ""),
            "material_type":           silver.get("material_type", ""),
            "material_raw":            silver.get("material_raw", ""),
            "gender":                  silver.get("gender", ""),
            "pattern":                 silver.get("pattern", ""),
            "dimensions":              silver.get("dimensions", ""),
            "price_tier":              silver.get("price_tier", ""),
            # Separate clean description fields so the frontend can pick the best one
            "long_description_clean":  silver.get("long_description_clean", ""),
            "complete_description_clean": silver.get("complete_description_clean", ""),
            "description":             rich_description,   # used by product_detail_response_node
            "embedding_text":          silver.get("embedding_text", ""),
        }

    except Exception as exc:
        logger.error(f"[SILVER] Lookup error for product_id={product_id}: {exc}", exc_info=True)
        return None


# Initialize Shopping Assistant Workflow
try:
    logger.info("Initializing Shopping Assistant Workflow...")
    workflow = ShoppingAssistantWorkflow()
    logger.info("✓ Workflow initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize workflow: {e}")
    import traceback
    logger.error(f"Traceback: {traceback.format_exc()}")
    workflow = None

# Debug logging
@app.before_request
def log_request():
    logger.info(f"Request: {request.method} {request.path} | URL: {request.url} | Headers: {dict(request.headers)}")


# JWT Authentication Middleware
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        # Check Authorization header
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
        
        # Check cookie as fallback
        if not token:
            token = request.cookies.get('access_token')
        
        if not token:
            return jsonify({'error': 'Authentication required', 'message': 'Please login first'}), 401
        
        payload = verify_token(token)
        if 'error' in payload:
            return jsonify({'error': 'Invalid or expired token', 'message': 'Please login again'}), 401
        
        # Attach user info to request
        request.user_id = payload.get('user_id')
        request.user_email = payload.get('email')
        request.user_name = payload.get('name')
        
        return f(*args, **kwargs)
    
    return decorated


# ============================================================================
# STATIC FILE ROUTES
# ============================================================================

@app.route('/')
def index():
    """Serve the main HTML page - requires authentication"""
    logger.info("Serving index.html")
    
    # Check if user is authenticated via cookie
    access_token = request.cookies.get('access_token')
    user_name = request.cookies.get('user_name')
    
    if not access_token or not user_name:
        logger.info("User not authenticated, redirecting to login")
        return redirect('/login')
    
    # Verify token is valid
    payload = verify_token(access_token)
    if 'error' in payload:
        logger.info("Invalid token, redirecting to login")
        return redirect('/login')
    
    # User is authenticated, serve the app
    try:
        static_dir = os.path.join(os.path.dirname(__file__), 'static')
        index_path = os.path.join(static_dir, 'index.html')
        logger.info(f"Looking for index.html at: {index_path}")
        logger.info(f"File exists: {os.path.exists(index_path)}")
        
        with open(index_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return content, 200, {'Content-Type': 'text/html; charset=utf-8'}
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        return f"Error loading index.html: {e}", 500


@app.route('/test')
@token_required
def test():
    """Test endpoint to verify app is running"""
    import glob
    files = glob.glob('*')
    return jsonify({
        "status": "App is running!",
        "files_in_directory": files,
        "current_directory": os.getcwd()
    })


@app.route('/<path:path>')
def serve_static(path):
    """Serve static files (CSS, JS, images) - handles both /static/* and direct paths"""
    try:
        logger.info(f"Serving static file: {path}")
        
        # Try static directory first
        static_dir = os.path.join(os.path.dirname(__file__), 'static')
        file_path = os.path.join(static_dir, path)
        
        # If not in static, try root (for backwards compatibility)
        if not os.path.exists(file_path):
            file_path = os.path.join(os.path.dirname(__file__), path)
        
        if os.path.exists(file_path) and os.path.isfile(file_path):
            # Determine content type
            if path.endswith('.css'):
                content_type = 'text/css'
            elif path.endswith('.js'):
                content_type = 'application/javascript'
            elif path.endswith('.html'):
                content_type = 'text/html'
            elif path.endswith('.jpg') or path.endswith('.jpeg'):
                content_type = 'image/jpeg'
            elif path.endswith('.png'):
                content_type = 'image/png'
            else:
                content_type = 'application/octet-stream'
            
            with open(file_path, 'rb') as f:
                content = f.read()
            return content, 200, {'Content-Type': content_type}
        else:
            logger.warning(f"File not found: {file_path}")
            return "File not found", 404
    except Exception as e:
        logger.error(f"Error serving {path}: {e}")
        return f"Error: {e}", 500


# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================

@app.route('/login')
def login_page():
    """Serve the login page"""
    return render_template('login.html')


@app.route('/oauth/login')
def oauth_login():
    """Initiate Google OAuth flow"""
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    auth_url = get_google_auth_url(state)
    return redirect(auth_url)


@app.route('/oauth/callback')
def oauth_callback():
    """Handle OAuth callback from Google"""
    try:
        state = request.args.get("state")
        if state != session.get("oauth_state"):
            return jsonify({"error": "Invalid state"}), 400

        code = request.args.get("code")
        if not code:
            return jsonify({"error": "No authorization code"}), 400

        # Exchange code for token
        token_response = exchange_code_for_token(code)
        google_access_token = token_response["access_token"]

        # Get user info from Google
        user_info = get_google_user_info(google_access_token)

        # Use email as stable user identifier (or hash it for privacy)
        email = user_info["email"]
        user_id = email  # Use email directly, or use: hashlib.sha256(email.encode()).hexdigest()
        name = user_info.get("name", email.split("@")[0])
        picture = user_info.get("picture", "")

# ── AUDIT: register customer in customer_pii on every login ──
        try:
            if workflow and workflow.audit_wrapper:
                workflow.audit_wrapper.register_customer(
                    user_email      = email,
                    full_name       = name,
                    user_country    = get_country_from_request(request) or os.getenv("DEFAULT_USER_COUNTRY", ""),
                    consent_version = "tnc_v2.1",
                )
        except Exception as _re:
            logger.warning(f"[AUDIT] register_customer failed (non-blocking): {_re}")
        # ─────────────────────────────────────────────────────────────
        # Generate JWT tokens
        access_token = generate_access_token(user_id, email, name)
        refresh_token = generate_refresh_token(user_id, email, name)

        # Redirect to main app with tokens
        response = redirect('/')
        response.set_cookie('access_token', access_token, httponly=True, secure=True, samesite='Lax', max_age=15*60)
        response.set_cookie('refresh_token', refresh_token, httponly=True, secure=True, samesite='Lax', max_age=7*24*60*60)
        response.set_cookie('user_name', name, secure=True, samesite='Lax', max_age=7*24*60*60)
        response.set_cookie('user_email', email, secure=True, samesite='Lax', max_age=7*24*60*60)
        response.set_cookie('user_picture', picture, secure=True, samesite='Lax', max_age=7*24*60*60)
        
        return response
        
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return jsonify({"error": "Authentication failed", "message": str(e)}), 500


@app.route('/api/refresh', methods=['POST'])
def refresh():
    """Refresh access token using refresh token"""
    refresh_token = request.cookies.get('refresh_token') or request.json.get('refresh_token')
    
    if not refresh_token:
        return jsonify({'error': 'Refresh token required'}), 400
    
    payload = verify_token(refresh_token)
    if 'error' in payload:
        return jsonify(payload), 401

    new_access = generate_access_token(
        payload["user_id"],
        payload.get("email", ""),
        payload.get("name", ""),
    )
    
    response = jsonify({"access_token": new_access})
    response.set_cookie('access_token', new_access, httponly=True, secure=True, samesite='Lax', max_age=15*60)
    return response


@app.route('/api/logout', methods=['POST'])
def logout():
    """Logout user by clearing cookies"""
    response = jsonify({'message': 'Logged out successfully'})
    response.set_cookie('access_token', '', expires=0, httponly=True, secure=True, samesite='Lax')
    response.set_cookie('refresh_token', '', expires=0, httponly=True, secure=True, samesite='Lax')
    response.set_cookie('user_name', '', expires=0, secure=True, samesite='Lax')
    response.set_cookie('user_email', '', expires=0, secure=True, samesite='Lax')
    response.set_cookie('user_picture', '', expires=0, secure=True, samesite='Lax')
    return response


# ============================================================================
# API ROUTES
# ============================================================================

@app.route('/api/search', methods=['POST'])
@token_required
def search():
    """
    Search endpoint for product recommendations using LangGraph Workflow
    
    Expected JSON payload:
    {
        "query": "user search query",
        "session_id": "unique-session-id"
    }
    
    Returns:
    {
        "message": "Assistant response",
        "results": [...],  # product results
        "preferences": {...}  # extracted preferences
    }
    """
    # Start performance monitoring
    monitor = get_monitor()
    monitor.start_monitoring_request()

    # Parse early so RequestTrace gets the query string
    data = request.json or {}
    query = data.get('query', '').strip()
    session_id = data.get('session_id') or str(uuid.uuid4())
    user_id = getattr(request, 'user_id', None)

    with RequestTrace(query=query, user_id=user_id):
      try:
        if workflow is None:
            return jsonify({
                "error": "Workflow not initialized",
                "message": "The assistant is currently unavailable. Please check configuration."
            }), 503

        logger.info(f"Search request - User: {user_id}, Session: {session_id[:8]}..., Query: {query}")

        if not query:
            return jsonify({
                "error": "Query is required",
                "message": "Please provide a search query"
            }), 400

        # Execute workflow with user_id for personalization
        with track_time("workflow_execution"):
            final_state = workflow.process_query(query, session_id, user_id=user_id)
        
        # Check for errors
        if final_state.get("error"):
            return jsonify({
                'message': f"⚠️ Error: {final_state['error']}",
                'results': [],
                'preferences': None
            })
        
        # Handle clarification needed
        if final_state.get("needs_clarification") and final_state.get("clarification_question"):
            # Show what the user is REQUESTING (new_preferences) in the chips, not the
            # accumulated old state — so the user sees "blue" not "mimco, armani, blue".
            new_prefs = final_state.get("new_preferences")
            formatted_prefs = None
            if new_prefs:
                formatted_prefs = {
                    'price_min': new_prefs.price_min,
                    'price_max': new_prefs.price_max,
                    'categories': new_prefs.categories,
                    'brands': new_prefs.brands,
                    'colors': new_prefs.colors,
                    'materials': new_prefs.materials
                }

            return jsonify({
                'message': final_state['clarification_question'],
                'results': [],
                'preferences': formatted_prefs,
                'needs_clarification': True
            })
        
        # Handle shopping results
        # Prefer reranked_results (what the LLM described) over raw results
        with track_time("result_formatting"):
            results = final_state.get("reranked_results") or final_state.get("results") or []
            preferences = final_state.get("preferences")
        
        # Format results for frontend
        formatted_results = []
        for item in results:
            metadata = item.get('metadata', {})
            
            # Convert price from string to float
            price_value = metadata.get('price', '0')
            try:
                price_float = float(price_value)
            except (ValueError, TypeError):
                price_float = 0.0
            
            # Get image URL - check both possible field names
            image_url = metadata.get('primary_image_url', '') or metadata.get('image_url', '')
            
            # Get product URL - url_full is the correct field name in the index
            product_url = metadata.get('url_full', '') or metadata.get('url', '') or metadata.get('product_url', '')
            if not product_url:
                logger.warning(f"No URL found for product: {metadata.get('name', 'Unknown')} (ID: {item.get('id', 'N/A')}) - Available keys: {list(metadata.keys())}")
            
            product_data = {
                'id': item.get('id', ''),
                'name': metadata.get('name', ''),
                'brand': metadata.get('brand', ''),
                'price': price_float,
                'image': image_url,
                'description': metadata.get('embedding_text', '')[:200] + '...' if metadata.get('embedding_text') else '',
                'url': product_url,
                'rating': 4.5,  # Default rating
                'color': clean_display_value(metadata.get('primary_color')),
                'bag_style': clean_bag_style(metadata.get('category')),
                'fabrication': clean_display_value(metadata.get('material_type')),
                'gender': clean_display_value(metadata.get('gender')),
                'price_tier': clean_display_value(metadata.get('price_tier')),
                'on_sale': metadata.get('on_sale', 'false') == 'true',
                'available': metadata.get('available', 'true') == 'true',
                'relevance_score': item.get('score', 0)
            }
            
            logger.info(f"Product: {product_data['name']}, Image URL: {image_url[:100] if image_url else 'EMPTY'}")
            formatted_results.append(product_data)
        
        # Format preferences for frontend
        formatted_preferences = None
        if preferences:
            formatted_preferences = {
                'price_min': preferences.price_min,
                'price_max': preferences.price_max,
                'categories': preferences.categories or [],
                'colors': preferences.colors or [],
                'materials': preferences.materials or [],
                'brands': preferences.brands or [],
                'features': preferences.features or []
            }
        
        # Use generated response if available (from RAG pipeline)
        generated_response = final_state.get("generated_response")
        safe_response = final_state.get("safe_response")  # Guardrail-validated response
        relaxation_message = final_state.get("relaxation_message")
        
        # Prefer safe_response from guardrail, then generated_response
        if safe_response:
            message = safe_response
            # Log if guardrail made corrections
            guardrail_status = final_state.get("guardrail_status", "unknown")
            guardrail_issues = final_state.get("guardrail_issues", [])
            if guardrail_issues:
                logger.info(f"Guardrail issues: {guardrail_issues}")
            if guardrail_status in ["warning", "fail"]:
                logger.warning(f"Guardrail status: {guardrail_status}, corrections applied")
        elif generated_response:
            message = generated_response
        elif relaxation_message:
            # If constraint relaxation happened, show that message
            message = relaxation_message
        else:
            message = f"Found {len(results)} products matching your criteria!" if results else "No products found. Try adjusting your search criteria."
        
        logger.info(f"Returning {len(formatted_results)} results")
        
        # Check if we're in product discussion mode
        product_discussion_mode = final_state.get("product_discussion_mode", False)
        product_context = final_state.get("product_context")
        selected_product_id = final_state.get("selected_product_id")
        
        # If in product discussion mode, only return the single product
        if product_discussion_mode and product_context:
            top_products = []
            more_products = []
        else:
            # Separate top 3 recommended products from the rest
            top_products = formatted_results[:3] if len(formatted_results) > 3 else formatted_results
            more_products = formatted_results[3:] if len(formatted_results) > 3 else []
        
        response_data = {
            'message': message,
            'results': formatted_results,  # Keep for backward compatibility
            'top_products': top_products,  # Top 3 recommendations (empty in discussion mode)
            'more_products': more_products,  # Remaining products (empty in discussion mode)
            'preferences': formatted_preferences,
            'product_discussion_mode': product_discussion_mode
        }
        
        # Add product context if in discussion mode
        if product_discussion_mode and product_context:
            # Format the single product for the frontend
            try:
                logger.info(f"[PRODUCT DISCUSSION MODE] Formatting product context")
                logger.info(f"[PRODUCT DISCUSSION MODE] Product context type: {type(product_context)}")
                logger.info(f"[PRODUCT DISCUSSION MODE] Product context keys: {list(product_context.keys()) if isinstance(product_context, dict) else 'Not a dict'}")
                
                if 'metadata' in product_context:
                    metadata = product_context['metadata']
                    product_id = product_context.get('id', '')
                    logger.info(f"[PRODUCT DISCUSSION MODE] Using metadata format, product_id from context: {product_id}")
                    logger.info(f"[PRODUCT DISCUSSION MODE] Metadata: name={metadata.get('name')}, brand={metadata.get('brand')}, id={metadata.get('id')}")
                else:
                    # Product is already in flat format
                    metadata = product_context
                    product_id = metadata.get('id', metadata.get('product_id', ''))
                    logger.info(f"[PRODUCT DISCUSSION MODE] Using flat format, product_id: {product_id}")
                    logger.info(f"[PRODUCT DISCUSSION MODE] Data: name={metadata.get('name')}, brand={metadata.get('brand')}")
                
                # Extract price
                price_str = str(metadata.get('price', '0')).replace('$', '').replace(',', '').strip()
                try:
                    price_float = float(price_str)
                except (ValueError, TypeError):
                    price_float = 0.0
                
                # Get image URL - check both possible field names
                image_url = metadata.get('primary_image_url', '') or metadata.get('image_url', '')
                logger.info(f"[PRODUCT DISCUSSION MODE] Image URL from metadata: {image_url[:100] if image_url else 'NONE'}")
                if not image_url or image_url == 'N/A':
                    image_url = 'https://via.placeholder.com/300x300?text=No+Image'
                    logger.warning(f"[PRODUCT DISCUSSION MODE] No image URL, using placeholder")
                
                # Get product URL - url_full is the correct field name in the index
                product_url = metadata.get('url_full', '') or metadata.get('url', '') or metadata.get('product_url', '')
                logger.info(f"[PRODUCT DISCUSSION MODE] Product URL: {product_url[:100] if product_url else 'NONE'}")
                if not product_url:
                    logger.warning(f"[PRODUCT DISCUSSION MODE] No URL in metadata. Available keys: {list(metadata.keys())}")
                
                single_product = {
                    'id': product_id,
                    'name': metadata.get('name', ''),
                    'brand': metadata.get('brand', ''),
                    'price': price_float,
                    'image': image_url,
                    'description': metadata.get('embedding_text', '')[:200] + '...' if metadata.get('embedding_text') else '',
                    'url': product_url,
                    'rating': 4.5,
                    'color': clean_display_value(metadata.get('primary_color') or metadata.get('color')),
                    'bag_style': clean_bag_style(metadata.get('category') or metadata.get('bag_style')),
                    'fabrication': clean_display_value(metadata.get('material_type') or metadata.get('fabrication')),
                    'gender': clean_display_value(metadata.get('gender')),
                    'price_tier': clean_display_value(metadata.get('price_tier')),
                    'on_sale': metadata.get('on_sale', 'false') == 'true',
                    'available': metadata.get('available', 'true') == 'true',
                    'relevance_score': 1.0
                }
                
                response_data['results'] = [single_product]
                response_data['product_context'] = single_product  # Add for frontend detection
                response_data['selected_product_id'] = product_id
                logger.info(f"Product discussion mode: returning single product {single_product['name']}")
                logger.info(f"Product image URL: {image_url[:100] if image_url else 'NONE'}")
                
            except Exception as e:
                logger.error(f"Error formatting product context: {str(e)}", exc_info=True)
                # Keep original formatted_results
        
        # Get performance metrics
        latency_breakdown = monitor.finish_monitoring_request(query)
        
        # Add performance metrics to response (for debugging)
        if os.getenv('ENABLE_LATENCY_REPORTING', 'false').lower() == 'true':
            response_data['performance'] = latency_breakdown
        
        return jsonify(response_data)

      except Exception as e:
        logger.error(f"Error in search endpoint: {str(e)}", exc_info=True)
        return jsonify({
            "error": "An error occurred processing your request",
            "message": str(e)
        }), 500


@app.route('/api/select_product', methods=['POST'])
@token_required
def select_product():
    """
    Inject product discussion mode into the LangGraph session state without
    running a full search.  Called by the frontend when the user clicks
    'Ask about this' so that follow-up questions are answered in context of
    that specific product rather than triggering a new search.
    """
    try:
        if workflow is None:
            return jsonify({'error': 'Workflow not initialized'}), 503

        data = request.json or {}
        session_id = data.get('session_id')
        product = data.get('product')

        if not session_id or not product:
            return jsonify({'error': 'session_id and product are required'}), 400

        config = {"configurable": {"thread_id": session_id}}
        product_id = str(product.get('id', 'unknown'))
        product_name = product.get('name', 'Unknown')

        # ── Step 1: try to get rich data from the silver table ──────────────
        silver = fetch_product_from_silver(product_id)

        if silver:
            # Silver table returned full product data — use it as-is
            metadata = silver
            metadata["id"] = product_id
            logger.info(f"[SELECT PRODUCT] Using silver-table data for {product_name}")
        else:
            # Fallback: build metadata from the flat product dict the frontend sent.
            # Map frontend field names → the keys product_detail_response_node expects.
            logger.warning(
                f"[SELECT PRODUCT] Silver lookup failed — falling back to frontend data for {product_name}"
            )
            metadata = {
                "id":               product_id,
                "name":             product.get('name') or '',
                "brand":            product.get('brand') or '',
                "price":            str(product.get('price') or 0),
                "primary_image_url": product.get('image') or '',
                "url_full":         product.get('url') or '',
                "primary_color":    product.get('color') or '',
                "category":         product.get('bag_style') or '',
                "material_type":    product.get('fabrication') or '',
                "gender":           product.get('gender') or '',
                "price_tier":       product.get('price_tier') or '',
                # Use full description text (not truncated) as both keys
                "description":      product.get('description') or '',
                "embedding_text":   product.get('description') or '',
                "on_sale":          str(product.get('on_sale', False)).lower(),
                "available":        str(product.get('available', True)).lower(),
            }

        # ── Step 2: wrap in the {'id', 'metadata'} format the workflow expects ─
        product_context = {
            "id":       product_id,
            "metadata": metadata,
        }

        # ── Step 3: inject into LangGraph state (no search triggered) ────────
        workflow.app.update_state(config, {
            "product_discussion_mode": True,
            "selected_product_id": product_id,
            "product_context": product_context,
        })

        logger.info(
            f"[SELECT PRODUCT] Session {session_id[:8]}... → product '{product_name}' "
            f"(id={product_id})"
        )

        # Return the enriched metadata so the frontend can refresh the product detail view.
        # Image: prefer silver table URL; fall back to the URL that was already rendering
        #        in the product card (product.get('image')) so the re-render never shows a
        #        broken image when the silver table's primary_image_url column is empty.
        image_url = (
            metadata.get('primary_image_url')      # silver table value (may be empty str)
            or product.get('image', '')             # original card image — known to work
        )
        # Description: prefer the human-readable fields, fall back to the rich_description
        # (which itself falls back to embedding_text in fetch_product_from_silver).
        description = (
            metadata.get('long_description_clean')
            or metadata.get('complete_description_clean')
            or metadata.get('description', '')
        )
        enriched_product = {
            'id':                        product_id,
            'name':                      metadata.get('name', product.get('name', '')),
            'brand':                     metadata.get('brand', product.get('brand', '')),
            'price':                     float(str(metadata.get('price', product.get('price', 0)) or 0).replace('$','').replace(',','')),
            'image':                     image_url,
            'url':                       metadata.get('url_full') or product.get('url', ''),
            'color':                     clean_display_value(metadata.get('primary_color') or product.get('color')),
            'bag_style':                 clean_bag_style(metadata.get('category') or product.get('bag_style')),
            'fabrication':               clean_display_value(metadata.get('material_type') or product.get('fabrication')),
            'dimensions':                clean_display_value(metadata.get('dimensions')),
            'pattern':                   clean_display_value(metadata.get('pattern')),
            'gender':                    clean_display_value(metadata.get('gender') or product.get('gender')),
            'price_tier':                clean_display_value(metadata.get('price_tier') or product.get('price_tier')),
            'description':               description,
        }
        return jsonify({'status': 'ok', 'product_id': product_id, 'enriched_product': enriched_product})

    except Exception as e:
        logger.error(f"Error in select_product: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint"""
    workflow_status = "ready" if workflow is not None else "unavailable"
    return jsonify({
        "status": "healthy" if workflow else "degraded",
        "service": "shopping-assistant",
        "workflow": workflow_status
    })


@app.route('/api/test-extended-tools', methods=['GET'])
@token_required
def test_extended_tools():
    """Test endpoint to verify extended tools are working on Databricks"""
    try:
        # Check if feature is enabled
        enabled = os.getenv("ENABLE_EXTENDED_MCP_TOOLS", "false").lower() == "true"
        
        if not enabled:
            return jsonify({
                "status": "disabled",
                "message": "ENABLE_EXTENDED_MCP_TOOLS is not set to 'true'",
                "environment": {
                    "ENABLE_EXTENDED_MCP_TOOLS": os.getenv("ENABLE_EXTENDED_MCP_TOOLS", "not set")
                }
            })
        
        # Try to import and test
        from core.extended_tools import get_extended_tools
        tools = get_extended_tools()
        
        # Get tool type
        tool_type = type(tools).__name__
        
        # Test health check
        health_result = tools.health_check()
        
        # Test stock tool with a simple query
        stock_test = None
        try:
            stock_info = tools.get_stock_info("TCS")
            stock_test = {
                "status": "working",
                "sample": stock_info[:200] if stock_info else "No data"
            }
        except Exception as e:
            stock_test = {
                "status": "error",
                "error": str(e)
            }
        
        return jsonify({
            "status": "enabled",
            "tool_mode": tool_type,
            "health_check": health_result,
            "stock_tool_test": stock_test,
            "environment": {
                "is_databricks": "DATABRICKS_RUNTIME_VERSION" in os.environ or "/databricks/" in os.getcwd(),
                "ENABLE_EXTENDED_MCP_TOOLS": os.getenv("ENABLE_EXTENDED_MCP_TOOLS")
            }
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "error": str(e),
            "error_type": type(e).__name__
        }), 500


@app.route('/api/performance', methods=['GET'])
@token_required
def get_performance_stats():
    """Get aggregate performance statistics across all requests"""
    monitor = get_monitor()
    tracker = get_tracker()
    
    overall_stats = monitor.get_overall_stats()
    
    # Get summary data
    all_stats = tracker.get_all_stats()
    
    # Format response
    response = {
        "aggregate": {
            "total_requests": overall_stats['total_requests'],
            "slow_requests": overall_stats['slow_requests'],
            "slow_request_threshold_seconds": overall_stats['slow_request_threshold_seconds']
        },
        "operations": {}
    }
    
    # Add operation statistics
    for operation, stats in all_stats.items():
        response['operations'][operation] = {
            "count": stats['count'],
            "avg_ms": round(stats['avg'] * 1000, 2),
            "min_ms": round(stats['min'] * 1000, 2),
            "max_ms": round(stats['max'] * 1000, 2),
            "total_seconds": round(stats['total'], 3)
        }
    
    # Sort by average time (slowest first)
    sorted_operations = sorted(
        response['operations'].items(),
        key=lambda x: x[1]['avg_ms'],
        reverse=True
    )
    
    response['operations'] = dict(sorted_operations)
    response['timestamp'] = time.strftime('%Y-%m-%d %H:%M:%S')
    
    return jsonify(response)


@app.route('/performance', methods=['GET'])
@token_required
def performance_dashboard():
    """Web dashboard view of performance metrics"""
    monitor = get_monitor()
    tracker = get_tracker()
    
    overall_stats = monitor.get_overall_stats()
    all_stats = tracker.get_all_stats()
    
    # Build HTML table
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Performance Dashboard - Shopping Assistant</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 20px;
                min-height: 100vh;
            }
            .container {
                max-width: 1200px;
                margin: 0 auto;
                background: white;
                border-radius: 12px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                overflow: hidden;
            }
            .header {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 30px;
                text-align: center;
            }
            .header h1 {
                font-size: 32px;
                margin-bottom: 10px;
            }
            .header p {
                opacity: 0.9;
                font-size: 14px;
            }
            .summary {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                padding: 30px;
                background: #f8f9fa;
                border-bottom: 1px solid #e0e0e0;
            }
            .summary-card {
                background: white;
                padding: 20px;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            .summary-card .label {
                font-size: 12px;
                color: #666;
                text-transform: uppercase;
                letter-spacing: 1px;
                margin-bottom: 8px;
            }
            .summary-card .value {
                font-size: 28px;
                font-weight: bold;
                color: #333;
            }
            .summary-card .value.warning { color: #f59e0b; }
            .summary-card .value.success { color: #10b981; }
            .table-container {
                padding: 30px;
                overflow-x: auto;
            }
            table {
                width: 100%;
                border-collapse: collapse;
                font-size: 14px;
            }
            th {
                background: #f8f9fa;
                color: #333;
                font-weight: 600;
                padding: 12px 16px;
                text-align: left;
                border-bottom: 2px solid #e0e0e0;
                cursor: pointer;
                user-select: none;
            }
            th:hover {
                background: #e9ecef;
            }
            td {
                padding: 12px 16px;
                border-bottom: 1px solid #f0f0f0;
            }
            tr:hover {
                background: #f8f9fa;
            }
            .badge {
                display: inline-block;
                padding: 4px 12px;
                border-radius: 12px;
                font-size: 12px;
                font-weight: 600;
            }
            .badge-slow { background: #fee2e2; color: #dc2626; }
            .badge-medium { background: #fef3c7; color: #d97706; }
            .badge-fast { background: #d1fae5; color: #059669; }
            .progress-bar {
                background: #e0e0e0;
                height: 6px;
                border-radius: 3px;
                overflow: hidden;
                margin-top: 4px;
            }
            .progress-fill {
                background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
                height: 100%;
                transition: width 0.3s ease;
            }
            .actions {
                padding: 20px 30px;
                background: #f8f9fa;
                border-top: 1px solid #e0e0e0;
                display: flex;
                gap: 10px;
                justify-content: space-between;
                align-items: center;
            }
            .btn {
                padding: 10px 20px;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: 600;
                cursor: pointer;
                text-decoration: none;
                display: inline-block;
                transition: all 0.2s;
            }
            .btn-primary {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
            }
            .btn-primary:hover {
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
            }
            .btn-secondary {
                background: white;
                color: #333;
                border: 1px solid #e0e0e0;
            }
            .btn-secondary:hover {
                background: #f8f9fa;
            }
            .timestamp {
                color: #666;
                font-size: 13px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚡ Performance Dashboard</h1>
                <p>Shopping Assistant Latency Monitoring</p>
            </div>
            
            <div class="summary">
                <div class="summary-card">
                    <div class="label">Total Requests</div>
                    <div class="value">""" + str(overall_stats['total_requests']) + """</div>
                </div>
                <div class="summary-card">
                    <div class="label">Slow Requests</div>
                    <div class="value warning">""" + str(overall_stats['slow_requests']) + """</div>
                </div>
                <div class="summary-card">
                    <div class="label">Slow Threshold</div>
                    <div class="value">""" + str(overall_stats['slow_request_threshold_seconds']) + """s</div>
                </div>
            </div>
            
            <div class="table-container">
                <table id="perfTable">
                    <thead>
                        <tr>
                            <th onclick="sortTable(0)">Operation ▼</th>
                            <th onclick="sortTable(1)">Count</th>
                            <th onclick="sortTable(2)">Avg (ms) ▼</th>
                            <th onclick="sortTable(3)">Min (ms)</th>
                            <th onclick="sortTable(4)">Max (ms)</th>
                            <th onclick="sortTable(5)">Total (s)</th>
                            <th>Visual</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody>
    """
    
    # Sort operations by average time (slowest first)
    sorted_ops = sorted(all_stats.items(), key=lambda x: x[1].get('avg', 0), reverse=True)
    max_avg = max([s[1].get('avg', 0) for s in sorted_ops]) if sorted_ops else 1
    
    for operation, stats in sorted_ops:
        avg_ms = stats['avg'] * 1000
        min_ms = stats['min'] * 1000
        max_ms = stats['max'] * 1000
        total_s = stats['total']
        count = stats['count']
        
        # Determine status badge
        if avg_ms > 1000:
            badge = '<span class="badge badge-slow">SLOW</span>'
        elif avg_ms > 100:
            badge = '<span class="badge badge-medium">MEDIUM</span>'
        else:
            badge = '<span class="badge badge-fast">FAST</span>'
        
        # Progress bar width
        bar_width = (stats['avg'] / max_avg * 100) if max_avg > 0 else 0
        
        html += f"""
                        <tr>
                            <td><strong>{operation}</strong></td>
                            <td>{count}</td>
                            <td><strong>{avg_ms:.2f}</strong></td>
                            <td>{min_ms:.2f}</td>
                            <td>{max_ms:.2f}</td>
                            <td>{total_s:.3f}</td>
                            <td>
                                <div class="progress-bar">
                                    <div class="progress-fill" style="width: {bar_width}%"></div>
                                </div>
                            </td>
                            <td>{badge}</td>
                        </tr>
        """
    
    html += """
                    </tbody>
                </table>
            </div>
            
            <div class="actions">
                <div class="timestamp">Last updated: """ + monitor.get_overall_stats().get('timestamp', 'N/A') + """</div>
                <div>
                    <a href="/api/performance" class="btn btn-secondary">JSON API</a>
                    <button onclick="window.location.reload()" class="btn btn-primary">🔄 Refresh</button>
                </div>
            </div>
        </div>
        
        <script>
            function sortTable(n) {
                const table = document.getElementById("perfTable");
                let switching = true;
                let dir = "desc";
                let switchcount = 0;
                
                while (switching) {
                    switching = false;
                    const rows = table.rows;
                    
                    for (let i = 1; i < (rows.length - 1); i++) {
                        let shouldSwitch = false;
                        const x = rows[i].getElementsByTagName("TD")[n];
                        const y = rows[i + 1].getElementsByTagName("TD")[n];
                        
                        let xContent = x.textContent || x.innerText;
                        let yContent = y.textContent || y.innerText;
                        
                        // Try to parse as number
                        const xNum = parseFloat(xContent);
                        const yNum = parseFloat(yContent);
                        
                        if (!isNaN(xNum) && !isNaN(yNum)) {
                            if (dir == "desc" && xNum < yNum) shouldSwitch = true;
                            if (dir == "asc" && xNum > yNum) shouldSwitch = true;
                        } else {
                            if (dir == "desc" && xContent.toLowerCase() < yContent.toLowerCase()) shouldSwitch = true;
                            if (dir == "asc" && xContent.toLowerCase() > yContent.toLowerCase()) shouldSwitch = true;
                        }
                        
                        if (shouldSwitch) {
                            rows[i].parentNode.insertBefore(rows[i + 1], rows[i]);
                            switching = true;
                            switchcount++;
                            break;
                        }
                    }
                    
                    if (switchcount == 0 && dir == "desc") {
                        dir = "asc";
                        switching = true;
                    }
                }
            }
        </script>
    </body>
    </html>
    """
    
    return html


@app.route('/api/performance/last', methods=['GET'])
@token_required
def get_last_performance():
    """Get performance breakdown for the last request"""
    tracker = get_tracker()
    timings = tracker.get_current_timings()
    
    if not timings:
        return jsonify({
            "error": "No timing data available",
            "message": "Run a query first to see performance data"
        }), 404
    
    # Calculate percentages
    total_time = timings.get('total_request_time', 0)
    operations = []
    
    for operation, duration in timings.items():
        if operation != 'total_request_time':
            percentage = (duration / total_time * 100) if total_time > 0 else 0
            operations.append({
                "name": operation,
                "duration_ms": round(duration * 1000, 2),
                "percentage": round(percentage, 1)
            })
    
    # Sort by duration
    operations.sort(key=lambda x: x['duration_ms'], reverse=True)
    
    # Identify bottlenecks
    bottlenecks = [
        op['name'] for op in operations 
        if op['duration_ms'] > 500 or op['percentage'] > 30
    ]
    
    # Performance rating
    total_ms = total_time * 1000
    if total_ms < 1000:
        rating = "excellent"
    elif total_ms < 2000:
        rating = "good"
    elif total_ms < 3000:
        rating = "acceptable"
    elif total_ms < 5000:
        rating = "slow"
    else:
        rating = "very_slow"
    
    return jsonify({
        "total_time_ms": round(total_ms, 2),
        "total_time_seconds": round(total_time, 3),
        "performance_rating": rating,
        "operations": operations,
        "bottlenecks": bottlenecks,
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    })


@app.route('/api/performance/reset', methods=['POST'])
@token_required
def reset_performance_stats():
    """Reset all performance statistics (useful for testing)"""
    tracker = get_tracker()
    tracker.reset()
    return jsonify({
        "message": "Performance statistics reset successfully",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    })


# ============================================================================
# OBSERVABILITY — METRICS ENDPOINT
# ============================================================================

@app.route('/api/metrics', methods=['GET'])
@token_required
def get_metrics():
    """
    Returns a full observability snapshot:
      - per-node latency (avg / p50 / p95 / p99 / min / max) in ms
      - search metrics (result counts, zero-result rate)
      - event counters (intent distribution, guardrail pass/fail, errors)
      - last 20 request summaries
    """
    snapshot = metrics_store.snapshot()
    snapshot["timestamp"] = time.strftime('%Y-%m-%d %H:%M:%S')
    # Attach semantic cache stats if the workflow is initialised
    try:
        snapshot["semantic_cache"] = workflow.semantic_cache.stats()
    except Exception:
        pass
    return jsonify(snapshot)


@app.route('/api/cache/invalidate', methods=['POST'])
@token_required
def invalidate_cache():
    """
    Flush the semantic cache — call this after a catalogue update so stale
    result sets are not served.

    POST body (optional JSON):
        { "fingerprint": "<hex>" }   -- flush only one filter bucket
        {}                           -- flush everything
    """
    try:
        body        = request.get_json(silent=True) or {}
        fingerprint = body.get("fingerprint")
        removed     = workflow.semantic_cache.invalidate(fingerprint)
        return jsonify({"status": "ok", "removed": removed, "fingerprint": fingerprint})
    except Exception as e:
        logger.error(f"Cache invalidation error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ============================================================================
# OBSERVABILITY — EVALS ENDPOINT
# ============================================================================

@app.route('/api/evals/run', methods=['POST'])
@token_required
def run_evals():
    """
    Trigger the full evaluation suite on demand.

    POST body (optional JSON):
    {
        "sample_traces": [
            { "query": "...", "state": { ...node output dict... } },
            ...
        ]
    }

    If sample_traces is omitted, only metric-based evaluators run
    (guardrails, latency SLOs) using data already in the metrics store.

    Returns:
    {
        "timestamp": "...",
        "overall_pass": bool,
        "overall_score": float (0–1),
        "node_results": { node_name: { score, passed, details, suggestions } },
        "failed_nodes": [...],
        "suggestions": [...]
    }
    """
    if workflow is None:
        return jsonify({"error": "Workflow not initialised"}), 503

    body = request.json or {}
    sample_traces = body.get("sample_traces")

    runner = workflow.eval_runner
    report = runner.run_all(sample_traces=sample_traces)
    return jsonify(report)


@app.route('/api/evals/intent', methods=['POST'])
@token_required
def eval_intent():
    """
    Evaluate a single intent_classifier result.

    POST body:
    { "query": "...", "result_state": { "intent": "...", "preferences": {...} } }
    """
    if workflow is None:
        return jsonify({"error": "Workflow not initialised"}), 503

    body = request.json or {}
    query = body.get("query", "")
    result_state = body.get("result_state", {})

    result = workflow.eval_runner.eval_intent_node(result_state, query)
    return jsonify(result.to_dict())


@app.route('/api/evals/response', methods=['POST'])
@token_required
def eval_response():
    """
    LLM-as-judge evaluation for a single response.

    POST body:
    {
        "query": "...",
        "result_state": {
            "generated_response": "...",
            "reranked_results": [...]
        }
    }
    """
    if workflow is None:
        return jsonify({"error": "Workflow not initialised"}), 503

    body = request.json or {}
    query = body.get("query", "")
    result_state = body.get("result_state", {})

    result = workflow.eval_runner.eval_response_node(result_state, query)
    return jsonify(result.to_dict())


# ============================================================================
# METRICS DASHBOARD (browser-friendly HTML)
# ============================================================================

@app.route('/metrics', methods=['GET'])
@token_required
def metrics_dashboard():
    """Human-readable metrics dashboard showing node latencies and eval health."""
    snap = metrics_store.snapshot()
    node_latency = snap.get("node_latency_ms", {})
    counters = snap.get("counters", {})
    search = snap.get("search_metrics", {})
    recent = snap.get("recent_requests", [])

    rows_latency = ""
    for node, stats in sorted(node_latency.items()):
        if stats.get("count", 0) == 0:
            continue
        slo_map = {
            "input_guardrail": 500, "intent_classifier": 1500,
            "product_search": 2000, "reranker": 3000,
            "response_generator": 2500, "output_guardrail": 1000,
            "request_total": 8000,
        }
        slo = slo_map.get(node, 9999)
        p95 = stats.get("p95", 0)
        colour = "#c8f7c5" if p95 <= slo else "#f7c5c5"
        rows_latency += (
            f"<tr style='background:{colour}'>"
            f"<td>{node}</td>"
            f"<td>{stats['count']}</td>"
            f"<td>{stats['avg']:.0f}</td>"
            f"<td>{stats['p50']:.0f}</td>"
            f"<td>{stats['p95']:.0f}</td>"
            f"<td>{stats['p99']:.0f}</td>"
            f"<td>{stats['min']:.0f}</td>"
            f"<td>{stats['max']:.0f}</td>"
            f"<td>{slo}</td>"
            f"</tr>"
        )

    rows_counters = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>"
        for k, v in sorted(counters.items())
    )

    rows_recent = "".join(
        f"<tr><td>{r.get('query_preview','')[:60]}</td>"
        f"<td>{r.get('duration_ms',0):.0f}ms</td>"
        f"<td>{'❌ ' + r['error'] if r.get('error') else '✅'}</td></tr>"
        for r in reversed(recent)
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
  <title>Shopping Assistant — Observability Dashboard</title>
  <meta http-equiv="refresh" content="30">
  <style>
    body {{ font-family: Calibri, sans-serif; margin: 24px; background: #f9f9f9; }}
    h1 {{ color: #0F4761; }} h2 {{ color: #E97132; border-bottom: 2px solid #E97132; padding-bottom:4px; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 32px; }}
    th {{ background: #E97132; color: white; padding: 8px 12px; text-align: left; }}
    td {{ padding: 6px 12px; border-bottom: 1px solid #ddd; }}
    tr:hover td {{ background: #fef3ec; }}
    .kpi {{ display: inline-block; background: white; border: 2px solid #E97132;
             border-radius: 8px; padding: 16px 24px; margin: 8px; min-width: 120px; text-align: center; }}
    .kpi .val {{ font-size: 2em; font-weight: bold; color: #0F4761; }}
    .kpi .lbl {{ font-size: 0.85em; color: #555; }}
    .refresh {{ color: #888; font-size: 0.8em; }}
  </style>
</head>
<body>
  <h1>Shopping Assistant — Observability Dashboard</h1>
  <p class="refresh">Auto-refreshes every 30s &nbsp;|&nbsp; {time.strftime('%Y-%m-%d %H:%M:%S')}</p>

  <div>
    <div class="kpi"><div class="val">{counters.get("request.total", 0)}</div><div class="lbl">Total Requests</div></div>
    <div class="kpi"><div class="val">{counters.get("request.error", 0)}</div><div class="lbl">Request Errors</div></div>
    <div class="kpi"><div class="val">{search.get("zero_result_rate_pct", 0):.1f}%</div><div class="lbl">Zero-Result Rate</div></div>
    <div class="kpi"><div class="val">{counters.get("guardrail_input.blocked", 0)}</div><div class="lbl">Input Blocked</div></div>
    <div class="kpi"><div class="val">{counters.get("guardrail_output.fail", 0)}</div><div class="lbl">Output Guardrail Fails</div></div>
    <div class="kpi"><div class="val">{node_latency.get("request_total", {{}}).get("p95", 0):.0f}ms</div><div class="lbl">p95 End-to-End</div></div>
  </div>

  <h2>Node Latency (ms) — green = within SLO, red = SLO breach</h2>
  <table>
    <tr><th>Node</th><th>Count</th><th>Avg</th><th>p50</th><th>p95</th><th>p99</th><th>Min</th><th>Max</th><th>SLO</th></tr>
    {rows_latency or '<tr><td colspan=9>No data yet — run some searches first</td></tr>'}
  </table>

  <h2>Event Counters</h2>
  <table>
    <tr><th>Event</th><th>Count</th></tr>
    {rows_counters or '<tr><td colspan=2>No counters yet</td></tr>'}
  </table>

  <h2>Recent Requests (last {len(recent)})</h2>
  <table>
    <tr><th>Query</th><th>Duration</th><th>Status</th></tr>
    {rows_recent or '<tr><td colspan=3>No requests yet</td></tr>'}
  </table>

  <p>
    <a href="/api/metrics">📥 Raw JSON metrics</a> &nbsp;|&nbsp;
    <a href="/performance">📊 Legacy performance dashboard</a>
  </p>
</body>
</html>"""
    return html


# ============================================================================
# WISHLIST API ROUTES
# ============================================================================

@app.route('/api/wishlist', methods=['GET'])
@token_required
def wishlist_get():
    """Fetch all active wishlist items for the authenticated user."""
    user_id = getattr(request, 'user_id', None)
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    if workflow is None or workflow.wishlist_store is None:
        return jsonify({'error': 'Wishlist unavailable'}), 503

    subject_ref = workflow.audit_wrapper._compute_refs(user_id)[1]
    items = workflow.wishlist_store.fetch(subject_ref)
    return jsonify({'items': items, 'count': len(items)})


@app.route('/api/wishlist/add', methods=['POST'])
@token_required
def wishlist_add():
    """Add a product to the authenticated user's wishlist."""
    user_id = getattr(request, 'user_id', None)
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    if workflow is None or workflow.wishlist_store is None:
        return jsonify({'error': 'Wishlist unavailable'}), 503

    data = request.json or {}
    product_id = data.get('product_id', '').strip()
    if not product_id:
        return jsonify({'error': 'product_id is required'}), 400

    subject_ref = workflow.audit_wrapper._compute_refs(user_id)[1]

    if workflow.wishlist_store.is_in_wishlist(subject_ref, product_id):
        return jsonify({'status': 'already_exists', 'product_id': product_id}), 200

    wishlist_id = workflow.wishlist_store.add(
        subject_ref=subject_ref,
        product_id=product_id,
        product_name=data.get('product_name'),
        brand=data.get('brand'),
        price=data.get('price'),
        image_url=data.get('image_url'),
        retailer_url=data.get('retailer_url'),
    )
    log_wishlist_action(workflow.audit_wrapper,
        trace_id=str(uuid.uuid4()),
        action='add_to_wishlist',
        product_id=product_id,
        status='success',
        subject_ref=subject_ref,
    )
    return jsonify({'status': 'added', 'wishlist_id': wishlist_id, 'product_id': product_id}), 201


@app.route('/api/wishlist/remove', methods=['DELETE'])
@token_required
def wishlist_remove():
    """Remove a product from the authenticated user's wishlist (soft delete)."""
    user_id = getattr(request, 'user_id', None)
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    if workflow is None or workflow.wishlist_store is None:
        return jsonify({'error': 'Wishlist unavailable'}), 503

    data = request.json or {}
    product_id = data.get('product_id', '').strip()
    if not product_id:
        return jsonify({'error': 'product_id is required'}), 400

    subject_ref = workflow.audit_wrapper._compute_refs(user_id)[1]
    workflow.wishlist_store.remove(subject_ref, product_id)
    log_wishlist_action(workflow.audit_wrapper,
        trace_id=str(uuid.uuid4()),
        action='remove_from_wishlist',
        product_id=product_id,
        status='success',
        subject_ref=subject_ref,
    )
    return jsonify({'status': 'removed', 'product_id': product_id}), 200


# ============================================================================
# CART API
# ============================================================================

@app.route('/api/cart', methods=['GET'])
@token_required
def cart_get():
    """Return the authenticated user's active cart items with subtotal."""
    user_id = getattr(request, 'user_id', None)
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    if workflow is None or workflow.cart_store is None:
        return jsonify({'error': 'Cart unavailable'}), 503

    subject_ref = workflow.audit_wrapper._compute_refs(user_id)[1]
    items = workflow.cart_store.fetch(subject_ref)

    subtotal = sum(
        (float(item.get('price') or 0)) * int(item.get('quantity') or 1)
        for item in items
    )
    return jsonify({'items': items, 'count': len(items), 'subtotal': round(subtotal, 2)})


@app.route('/api/cart/add', methods=['POST'])
@token_required
def cart_add():
    """Add a product to the cart, or increment its quantity if already present."""
    user_id = getattr(request, 'user_id', None)
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    if workflow is None or workflow.cart_store is None:
        return jsonify({'error': 'Cart unavailable'}), 503

    data       = request.json or {}
    product_id = data.get('product_id', '').strip()
    if not product_id:
        return jsonify({'error': 'product_id is required'}), 400

    quantity = max(1, int(data.get('quantity', 1)))
    subject_ref = workflow.audit_wrapper._compute_refs(user_id)[1]

    cart_item_id = workflow.cart_store.add(
        subject_ref  = subject_ref,
        product_id   = product_id,
        product_name = data.get('product_name'),
        brand        = data.get('brand'),
        price        = data.get('price'),
        image_url    = data.get('image_url'),
        retailer_url = data.get('retailer_url'),
        quantity     = quantity,
    )
    log_cart_action(workflow.audit_wrapper,
        trace_id    = str(uuid.uuid4()),
        action      = 'add_to_cart',
        product_id  = product_id,
        status      = 'success',
        quantity    = quantity,
        subject_ref = subject_ref,
    )
    return jsonify({'status': 'added', 'cart_item_id': cart_item_id, 'product_id': product_id}), 201


@app.route('/api/cart/remove', methods=['DELETE'])
@token_required
def cart_remove():
    """Remove a product from the cart (soft delete)."""
    user_id = getattr(request, 'user_id', None)
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    if workflow is None or workflow.cart_store is None:
        return jsonify({'error': 'Cart unavailable'}), 503

    data       = request.json or {}
    product_id = data.get('product_id', '').strip()
    if not product_id:
        return jsonify({'error': 'product_id is required'}), 400

    subject_ref = workflow.audit_wrapper._compute_refs(user_id)[1]
    workflow.cart_store.remove(subject_ref, product_id)
    log_cart_action(workflow.audit_wrapper,
        trace_id    = str(uuid.uuid4()),
        action      = 'remove_from_cart',
        product_id  = product_id,
        status      = 'success',
        quantity    = 0,
        subject_ref = subject_ref,
    )
    return jsonify({'status': 'removed', 'product_id': product_id}), 200


@app.route('/api/cart/update', methods=['PUT'])
@token_required
def cart_update():
    """Update quantity for a cart item. quantity=0 removes the item."""
    user_id = getattr(request, 'user_id', None)
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    if workflow is None or workflow.cart_store is None:
        return jsonify({'error': 'Cart unavailable'}), 503

    data       = request.json or {}
    product_id = data.get('product_id', '').strip()
    if not product_id:
        return jsonify({'error': 'product_id is required'}), 400

    quantity = int(data.get('quantity', 1))
    subject_ref = workflow.audit_wrapper._compute_refs(user_id)[1]
    workflow.cart_store.update_quantity(subject_ref, product_id, quantity)

    action = 'remove_from_cart' if quantity <= 0 else 'update_cart'
    log_cart_action(workflow.audit_wrapper,
        trace_id    = str(uuid.uuid4()),
        action      = action,
        product_id  = product_id,
        status      = 'success',
        quantity    = max(0, quantity),
        subject_ref = subject_ref,
    )
    status = 'removed' if quantity <= 0 else 'updated'
    return jsonify({'status': status, 'product_id': product_id, 'quantity': max(0, quantity)}), 200


# ============================================================================
# APPLICATION ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    # For both local development and Databricks Apps
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Starting app on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)

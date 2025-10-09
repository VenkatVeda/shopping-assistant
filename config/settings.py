# config/settings.py
import os
import logging
from dotenv import load_dotenv

load_dotenv(".env")

# Configure logging levels to reduce verbosity
def configure_logging():
    """Configure logging to reduce LLM prompt verbosity"""
    # Get log level from environment (default to INFO)
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    
    # Check if LLM prompts should be hidden
    hide_llm_prompts = os.getenv("HIDE_LLM_PROMPTS", "true").lower() == "true"
    
    if hide_llm_prompts:
        # Set specific loggers to WARNING or ERROR to reduce noise
        logging.getLogger("openai").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("langchain").setLevel(logging.WARNING)
        logging.getLogger("langchain_openai").setLevel(logging.WARNING)
        logging.getLogger("langchain_core").setLevel(logging.WARNING)
        logging.getLogger("langchain_community").setLevel(logging.WARNING)
        logging.getLogger("chromadb").setLevel(logging.WARNING)
        logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
        print("🔇 LLM prompt logging suppressed for cleaner logs")
    else:
        print("📝 LLM prompt logging enabled (verbose mode)")
    
    # Keep our app logs at specified level for monitoring
    logging.getLogger("__main__").setLevel(log_level)
    
    # Set root logger to specified level
    logging.basicConfig(
        level=log_level,
        format='%(message)s'  # Simple format for clean logs
    )

# Initialize logging configuration
configure_logging()

# Azure Configuration
AZURE_CONFIG = {
    "api_key": os.getenv("AZURE_OPENAI_API_KEY"),
    "api_version": "2024-12-01-preview",
    "azure_endpoint": "https://azai-uaip-sandbox-eastus-001.openai.azure.com/",
    "embedding_deployment": "text-embedding-ada-002",
    "llm_deployment": "xponent-openai-gpt-4o-mini"
}

# Vector Database Configuration
VECTOR_CONFIG = {
    "chroma_dir":"vector_db backups/chroma_db_numeric",
    "collection_name": "bags"
}

# Data Configuration
DATA_CONFIG = {
    "excel_file": "bags.xlsx"
}

# Session Management Configuration
SESSION_CONFIG = {
    "timeout_hours": 24,  # Session timeout in hours
    "cleanup_interval_minutes": 60,  # How often to clean up expired sessions
    "max_concurrent_sessions": 1000  # Maximum number of concurrent sessions
}

# Preference Schema
PREFERENCE_SCHEMA = {
    "price_min": None,
    "price_max": None,
    "brands": [],
    "categories": [],
    "colors": [],
    "materials": [],
    "features": [],
    "excluded_colors": [],
    "excluded_brands": [],
    "excluded_categories": []
}

#generalise it with pandas

BAG_CATEGORIES = {
    "tote bags", "shoulder bags", "duffle bags", "backpacks", "clutches", "crossbody bags",
    "handbag", "messenger", "satchel", "laptop bag", "briefcase", "wristlet",
    "wallet", "purse"
}

VALID_BRANDS = {
    '1978W', 'Active Flex', 'Alan Pinkus', 'Amelia Lane', 'American Tourister', 'Armani Exchange',
    'Australian House & Garden', 'Basque', 'Belle & Bloom', 'Billabong', 'Boutique Retailer', 
    'Calvin Klein', 'Cellini', 'Cellini Sport', 'Commonry', 'Country Road', 'Creed', 'David Lawrence',
    'Delsey', 'Disney', 'Dune London', 'Elliker', 'emerge Woman', 'Fella Hamilton', 'Fine Day',
    'Forever New', 'Fossil', 'GAP', 'Guess', 'Hedgren', 'Hot Wheels', 'Jane Debster',
    'Joan Weisz', 'Kinnon', 'La Enviro', 'Lacoste', 'Lauren Ralph Lauren', "Levi's",
    'Madison Accessories', 'Maine & Crawford', 'Marcs', 'Maxwell & Williams', 'Milleni', 'Mimco',
    'Mocha', 'Morgan & Taylor', 'Nakedvice', 'NINA', 'Nine West', 'Novo Shoes', 'OiOi', 'Olga Berg',
    'Oxford', 'PIERRE CARDIN', 'PINK INC', 'Piper', 'Prairie', 'Radley', 'Ravella', 'Rebecca Minkoff',
    'REVIEW', 'Roxy', 'RVCA', 'Samsonite', 'Sandler', 'Sass & Bide', 'Scala', 'Seafolly',
    'Seed Heritage', 'Senso', 'Status Anxiety', 'Steve Madden', 'Taking Shape', 'TATONKA', 'Tokito',
    'Tommy Hilfiger', 'Tonic', 'Trenery', 'Trent Nathan', 'Unison', 'Wishes', 'Witchery', 'Yellow Drama'
}  

BRAND_CORRECTIONS = {
    'ck': 'Calvin Klein',
    'rm': 'Rebecca Minkoff',
    'th': 'Tommy Hilfiger',
    'pierre': 'PIERRE CARDIN',
    'calvin': 'Calvin Klein',
    'tommy': 'Tommy Hilfiger',
    'ralph lauren': 'Lauren Ralph Lauren',
    'american t': 'American Tourister',
    'fossil bag': 'Fossil',
    'guess bag': 'Guess',
}

# Add this list of valid colors near your other constants
VALID_COLORS = {
    'black', 'brown', 'blue', 'red', 'green', 'yellow', 
    'white', 'grey', 'gray', 'pink', 'purple', 'orange',
    'beige', 'navy', 'cream', 'tan', 'gold', 'silver'
}
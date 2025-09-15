# config/settings.py
import os
from dotenv import load_dotenv

load_dotenv(".env")

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
    "chroma_dir": "./chroma_db_numeric",
    "collection_name": "bags"
}

# Data Configuration
DATA_CONFIG = {
    "excel_file": "bags.xlsx"
}

# Preference Schema
PREFERENCE_SCHEMA = {
    "price_min": None,
    "price_max": None,
    "brands": [],
    "categories": [],
    "colors": [],
    "materials": [],
    "features": []
}

#generalise it with pandas

BAG_CATEGORIES = {
    "tote", "shoulder bag", "duffle bag", "backpack", "clutch", "crossbody",
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
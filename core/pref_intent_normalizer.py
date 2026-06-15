import os
import json
import time
from dotenv import load_dotenv
from databricks_langchain import ChatDatabricks
from .prompt_loader import load_prompt

# Load environment variables
load_dotenv()

# Configuration for Databricks Intent Endpoint
INTENT_ENDPOINT = os.getenv("DATABRICKS_INTENT_ENDPOINT", "databricks-meta-llama-3-1-8b-instruct")

# Load system prompt from file
SYSTEM_PROMPT = load_prompt("intent_classification")

from .models import UserQuery, SearchPreferences

# Canonical set of brands that exist in the product catalogue.
# Must stay in sync with the VALID_BRANDS list in intent_classification.txt.
CATALOGUE_BRANDS: set = {
    '1978W', 'Active Flex', 'Alan Pinkus', 'Amelia Lane', 'American Tourister',
    'Armani Exchange', 'Australian House & Garden', 'Basque', 'Belle & Bloom',
    'Billabong', 'Boutique Retailer', 'Calvin Klein', 'Cellini', 'Cellini Sport',
    'Commonry', 'Country Road', 'Creed', 'David Lawrence', 'Delsey', 'Disney',
    'Dune London', 'Elliker', 'emerge Woman', 'Fella Hamilton', 'Fine Day',
    'Forever New', 'Fossil', 'GAP', 'Guess', 'Hedgren', 'Hot Wheels',
    'Jane Debster', 'Joan Weisz', 'Kinnon', 'La Enviro', 'Lacoste',
    'Lauren Ralph Lauren', "Levi's", 'Madison Accessories', 'Maine & Crawford',
    'Marcs', 'Maxwell & Williams', 'Milleni', 'Mimco', 'Mocha',
    'Morgan & Taylor', 'Nakedvice', 'NINA', 'Nine West', 'Novo Shoes', 'OiOi',
    'Olga Berg', 'Oxford', 'PIERRE CARDIN', 'PINK INC', 'Piper', 'Prairie',
    'Radley', 'Ravella', 'Rebecca Minkoff', 'REVIEW', 'Roxy', 'RVCA',
    'Samsonite', 'Sandler', 'Sass & Bide', 'Scala', 'Seafolly', 'Seed Heritage',
    'Senso', 'Status Anxiety', 'Steve Madden', 'Taking Shape', 'TATONKA',
    'Tokito', 'Tommy Hilfiger', 'Tonic', 'Trenery', 'Trent Nathan', 'Unison',
    'Wishes', 'Witchery', 'Yellow Drama',
}

# Lowercase lookup for case-insensitive matching
_CATALOGUE_BRANDS_LOWER: dict = {b.lower(): b for b in CATALOGUE_BRANDS}

class IntentClassifier:
    def __init__(self):
        """Initialize the Intent Classifier with Databricks ChatDatabricks."""
        self.llm = ChatDatabricks(
            endpoint=INTENT_ENDPOINT,
            temperature=0.0,
            max_tokens=1000
        )

    def process_query(self, query: str, context: str = "") -> UserQuery:
        """
        Process a user query to extract preferences and intent.
        
        Args:
            query (str): The user's natural language query.
            context (str): Previous conversation context (history + summary).
            
        Returns:
            UserQuery: Structured preferences and intent.
        """
        try:
            # Build messages with context if available
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT}
            ]
            
            # Add context if provided — for UNDERSTANDING only, not for carrying forward preferences.
            # The workflow layer handles preference merging/accumulation separately.
            # The LLM must extract ONLY what the user explicitly states in the CURRENT query.
            if context:
                messages.append({
                    "role": "system",
                    "content": (
                        f"Previous conversation context:\n{context}\n\n"
                        "Use this context ONLY to understand references in the current query "
                        "(e.g. 'those bags', 'the same brand', pronouns). "
                        "DO NOT carry forward or include previous preferences (brands, colors, "
                        "categories, price, etc.) unless the user explicitly restates them in "
                        "the current message. Extract ONLY what is stated in the current query."
                    )
                })
            
            messages.append({"role": "user", "content": query})
            
            response = self.llm.invoke(messages)
            
            # Extract content from ChatDatabricks response
            content = response.content if hasattr(response, 'content') else str(response)
            
            if content is None or content.strip() == "":
                return self._get_empty_response(query, "Model returned empty content")
            
            content = content.strip()
            
            # Use regex to find the first JSON object
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            
            result_dict = {}
            if json_match:
                json_str = json_match.group(0)
                try:
                    result_dict = json.loads(json_str)
                except json.JSONDecodeError:
                    pass
            
            if not result_dict:
                try:
                    result_dict = json.loads(content)
                except json.JSONDecodeError as e:
                    return self._get_empty_response(query, f"JSON Parse Error: {str(e)}")

            # Calculate completeness score
            score = self._calculate_completeness(result_dict)
            
            # Map to Pydantic models
            prefs = SearchPreferences(
                price_min=result_dict.get("price_min"),
                price_max=result_dict.get("price_max"),
                brands=result_dict.get("brands", []),
                categories=result_dict.get("categories", []),
                colors=result_dict.get("colors", []),
                materials=result_dict.get("materials", []),
                features=result_dict.get("features", []),
                closure_types=result_dict.get("closure_types", []),
                strap_types=result_dict.get("strap_types", []),
                sizes=result_dict.get("sizes", []),
                has_zipper=result_dict.get("has_zipper"),
                excluded_colors=result_dict.get("excluded_colors", []),
                excluded_brands=result_dict.get("excluded_brands", []),
                excluded_categories=result_dict.get("excluded_categories", []),
                excluded_materials=result_dict.get("excluded_materials", [])
            )
            
            # Filter brands: separate catalogue brands from unknown brands
            prefs = self._validate_brands(prefs)

            # Apply regex-based price fallback to correct LLM mis-extractions
            prefs = self._apply_price_fallback(query, prefs)

            return UserQuery(
                raw_query=query,
                intent=result_dict.get("intent", "chat"),
                completeness_score=score,
                preferences=prefs
            )
            
        except Exception as e:
            return self._get_empty_response(query, f"Error: {str(e)}")

    def _calculate_completeness(self, data: dict) -> float:
        score = 0.0
        if data.get("categories"): score += 0.5
        if data.get("price_min") or data.get("price_max") or "budget-friendly" in data.get("features", []): score += 0.2
        if data.get("colors") or data.get("materials") or data.get("brands"): score += 0.2
        if data.get("features"): score += 0.1
        return min(1.0, score)

    def _validate_brands(self, prefs: "SearchPreferences") -> "SearchPreferences":
        """Split LLM-extracted brands into catalogue brands and unknown brands.

        The LLM sometimes returns brand names that are not in our catalogue (e.g. Gucci,
        Louis Vuitton). These must not be used as search filters — they would silently return
        zero results. Instead, they are moved to unknown_brands so the response layer can
        surface a clear message to the user.
        """
        if not prefs.brands:
            return prefs

        valid = []
        unknown = []
        for brand in prefs.brands:
            # Case-insensitive lookup; restore canonical casing if found
            canonical = _CATALOGUE_BRANDS_LOWER.get(brand.lower())
            if canonical:
                valid.append(canonical)
            else:
                unknown.append(brand)
                print(f"[BRAND FILTER] '{brand}' not in catalogue — moved to unknown_brands")

        prefs.brands = valid
        prefs.unknown_brands = unknown
        return prefs

    def _apply_price_fallback(self, query: str, prefs: "SearchPreferences") -> "SearchPreferences":
        """Override LLM price extraction with regex when LLM misread the value."""
        import re
        q = query.lower()

        # Allow up to 4 filler words between the keyword and the number so that
        # "over the price of 250", "above a budget of $100", etc. are all matched.
        _NUM = r'\$?\s*(\d+(?:\.\d+)?)'
        _GAP = r'(?:\s+\w+){0,4}\s*'

        # "not below/under X" and "not less than X" mean price_min, checked first
        not_below = re.search(r'not\s+(?:below|under|less\s+than)' + _GAP + _NUM, q)

        # "over/above/more than/at least X"
        over = re.search(r'(?:over|above|more\s+than|at\s+least|higher\s+than|greater\s+than|minimum|min)' + _GAP + _NUM, q)

        # "under/below/less than/up to X" — but NOT when preceded by "not "
        under_raw = re.search(r'(?:under|below|less\s+than|max|up\s+to|lower\s+than|maximum|no\s+more\s+than)' + _GAP + _NUM, q)
        # Suppress under match if it was part of "not below/not under"
        under = None
        if under_raw:
            start = under_raw.start()
            preceding = q[max(0, start - 5):start]
            if 'not ' not in preceding:
                under = under_raw

        # "between X and Y"
        between = re.search(r'between' + r'\s*' + _NUM + r'\s*(?:and|to|-)\s*' + _NUM, q)

        # "around/about X"
        around = re.search(r'(?:around|about|roughly|approximately)' + _GAP + _NUM, q)

        regex_min = None
        regex_max = None

        if between:
            regex_min = float(between.group(1))
            regex_max = float(between.group(2))
        else:
            if not_below:
                regex_min = float(not_below.group(1))
            elif over:
                regex_min = float(over.group(1))

            if under:
                regex_max = float(under.group(1))
            elif around and regex_min is None:
                regex_max = float(around.group(1))

        # Only override if regex found something AND it differs from LLM extraction
        if regex_max is not None and prefs.price_max != regex_max:
            print(f"[PRICE FALLBACK] LLM had price_max={prefs.price_max}, regex says {regex_max} — using regex")
            prefs.price_max = regex_max
        if regex_min is not None and prefs.price_min != regex_min:
            print(f"[PRICE FALLBACK] LLM had price_min={prefs.price_min}, regex says {regex_min} — using regex")
            prefs.price_min = regex_min

        # If regex detected a min-price intent, clear any LLM-set max that contradicts it
        # (e.g. LLM set price_max=250 but user actually said "over 250")
        if regex_min is not None and prefs.price_max is not None and prefs.price_max <= prefs.price_min:
            print(f"[PRICE FALLBACK] Clearing contradictory price_max={prefs.price_max} (≤ price_min={prefs.price_min})")
            prefs.price_max = None

        return prefs

    def _get_empty_response(self, query: str, error_msg: str = "") -> UserQuery:
        """Return a safe empty response."""
        return UserQuery(
            raw_query=query,
            intent="chat",
            completeness_score=0.0
        )

if __name__ == "__main__":
    # Simple CLI for testing
    classifier = IntentClassifier()
    print("Intent Classifier Initialized")
    print("Type 'exit' to quit.\n")
    
    while True:
        user_input = input("You: ")
        if user_input.lower() in ['exit', 'quit']:
            break
            
        result = classifier.process_query(user_input)
        print(f"Intent: {result.intent}")
        print(f"Score: {result.completeness_score}")
        print("\n")

def fix_color_modifiers(flattened, user_message):
    """
    Correct color negations caused by adjectives like "bright", "dark", "light".
    E.g., "bright pink" should not negate pink.
    """
    # Ensure "negations" key exists
    flattened.setdefault("negations", {})
    flattened["negations"].setdefault("colors", [])

    colors = flattened.get("colors", [])
    negations = flattened["negations"]["colors"]

    # Adjectives that should NOT negate colors
    color_adjectives = ["bright", "light", "dark", "pastel", "neon", "vivid"]

    # Known color names for validation
    known_colors = [
        "red", "blue", "green", "yellow", "black", "white", "brown", "pink", 
        "purple", "orange", "grey", "gray", "beige", "tan", "navy", "maroon",
        "gold", "silver", "cream", "ivory", "turquoise", "teal", "lavender"
    ]

    # Remove these adjectives from negations
    new_negations = [n for n in negations if n.lower() not in color_adjectives]
    flattened["negations"]["colors"] = new_negations

    # Heuristic: if adjective appears with a color in the input, keep the color
    words = user_message.lower().split()
    for i, word in enumerate(words):
        if word in color_adjectives and i + 1 < len(words):
            next_word = words[i + 1]
            if next_word in known_colors and next_word not in colors:
                colors.append(next_word)

    flattened["colors"] = list(set(colors))  # remove duplicates
    return flattened


def extract_style_attributes(flattened, user_message):
    """
    Extract style-related adjectives from user input and append to 'features'
    (the normalised key used throughout the engine).
    E.g., 'cute', 'minimalist', 'sporty', 'classic', 'vintage'
    """
    STYLE_KEYWORDS = ["cute", "minimalist", "sporty", "classic", "vintage", "modern", "elegant"]

    flattened.setdefault("features", [])
    features = flattened["features"]

    for word in STYLE_KEYWORDS:
        if word in user_message.lower() and word not in features:
            features.append(word)

    flattened["features"] = features
    return flattened


def detect_special_occasion(user_message):
    """
    Detect if the user mentions a special occasion.
    Returns True/False
    """
    SPECIAL_KEYWORDS = ["birthday", "anniversary", "wedding", "festival", "diwali", "christmas", "new year"]

    user_text = user_message.lower()
    for keyword in SPECIAL_KEYWORDS:
        if keyword in user_text:
            return True
    return False

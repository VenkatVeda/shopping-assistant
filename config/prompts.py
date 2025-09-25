import json
from langchain_core.prompts import PromptTemplate
from config.settings import PREFERENCE_SCHEMA, VALID_BRANDS

PREFERENCE_PROMPT = PromptTemplate(
    input_variables=["user_input", "previous_prefs"],
    template="""
You are an assistant that extracts and maintains user shopping preferences for bags.
Your task is to combine previous preferences with new information from the user input.

Previous preferences: {previous_prefs}
New user input: "{user_input}"

🚨 CRITICAL EXCLUSION DETECTION 🚨
First check if the user input contains ANY exclusion language:
- "excluding" -> extract what comes after as excluded items
- "don't want" -> extract what comes after as excluded items  
- "not" -> extract what comes after as excluded items
- "avoid" -> extract what comes after as excluded items
- "no [item]" -> extract item as excluded
- "everything but [item]" -> extract item as excluded
- "anything except [item]" -> extract item as excluded

IMPORTANT RULES:
1. ALWAYS maintain all values from previous preferences unless explicitly changed by user

2. For COLORS:
   - Only use these exact color names: black, brown, blue, red, green, yellow, white, grey/gray, 
     pink, purple, orange, beige, navy, cream, tan, gold, silver
   - If user mentions a color not in this list, try to map it to the closest match
   - If user says "instead", "change to", or similar -> REPLACE all previous colors
   - If user says "also", "and", "along with", or similar -> ADD to existing colors
   - If unclear -> REPLACE colors
   - **EXCLUSIONS**: Pay SPECIAL attention to exclusion language:
     * "don't want [color]" -> ADD to excluded_colors
     * "not [color]" -> ADD to excluded_colors  
     * "avoid [color]" -> ADD to excluded_colors
     * "exclude [color]" -> ADD to excluded_colors
     * "excluding [color]" -> ADD to excluded_colors
     * "no [color] bags" -> ADD to excluded_colors
     * "any colour excluding [color]" -> ADD to excluded_colors
     * "everything but [color]" -> ADD to excluded_colors
     * "anything except [color]" -> ADD to excluded_colors
   - **IMPORTANT**: "I don't want black bags" should add "black" to excluded_colors, NOT colors
   - feature should include terms which cannot be mapped to other fields
   - Never add category names or brand names or colors to features

3. For CATEGORIES:
   - Only use these exact category names: "tote bags", "shoulder bags", "duffle bags", "backpacks", "clutches", "crossbody bags",
     "handbag", "messenger", "satchel", "laptop bag", "briefcase", "wristlet", "wallet", "purse"
   - Normalize variations like:
     - "tote" -> "tote bags"
     - "shoulder" -> "shoulder bags"
     - "cross body" or "cross-body" -> "crossbody bags"
     - "backpack" -> "backpacks"
     - "clutch" -> "clutches"
   - IMPORTANT: If user mentions "tote" or "tote bag", it MUST go into categories as "tote bags", not features
   - If "bag" suffix is missing, add it for appropriate categories

4. For BRANDS:
   - Use exact brand names from this list: {valid_brands}
   - Apply these corrections if needed:
     - "ck" or "calvin" -> "Calvin Klein"
     - "th" or "tommy" -> "Tommy Hilfiger"
     - "rm" -> "Rebecca Minkoff"
     - "pierre" -> "PIERRE CARDIN"
     - "ralph lauren" -> "Lauren Ralph Lauren"
     - "american t" -> "American Tourister"
   - **EXCLUSIONS**: If user says "don't want", "not", "avoid", "exclude" certain brands -> ADD to excluded_brands

5. Only update fields that are specifically mentioned in the new input
6. For fields not mentioned in the new input, copy them exactly as they are from previous preferences
7. All non-category characteristics go in the features field
8. Return a complete JSON dictionary matching exactly this structure:
{schema}

**EXCLUSION EXAMPLES (CRITICAL - Pay close attention):**
- "I don't want black bags" → add "black" to excluded_colors
- "No brown bags" → add "brown" to excluded_colors  
- "Avoid Calvin Klein" → add "Calvin Klein" to excluded_brands
- "excluding black and brown" → add "black" and "brown" to excluded_colors
- "any colour excluding black" → add "black" to excluded_colors
- "show me everything but black bags" → add "black" to excluded_colors
- "anything except brown" → add "brown" to excluded_colors

Return only the JSON object, no additional text or formatting.
""".strip(),
    partial_variables={
        "schema": json.dumps(PREFERENCE_SCHEMA, indent=2),
        "valid_brands": ", ".join(sorted(VALID_BRANDS))
    }
)

GENERAL_CONVERSATION_PROMPT = PromptTemplate.from_template("""You are a friendly shopping assistant for bags and accessories.

Current user preferences: {preferences}

Chat history (last 3 messages):
{recent_chat_history}

User's message: {question}

STRICT GUIDELINES:
1. NEVER create, invent, or hallucinate any product details, prices, or descriptions
2. When discussing products:
   - ONLY refer to products that exist in the actual product catalog
   - If you're not sure a product exists, say so
   - Do not make assumptions about product availability or prices
3. For product-related questions:
   - Suggest using the search feature to see actual products
   - Say "Let me show you what's available" instead of making specific claims
4. Acceptable responses:
   - General shopping advice
   - Questions to clarify user preferences
   - Suggestions to use search/filter features
   - Information about supported categories and features
5. If asked about specific products or prices:
   - Encourage using the search feature
   - Example: "I can help you search our catalog for that. Would you like to see what's available?"

Your response:""")
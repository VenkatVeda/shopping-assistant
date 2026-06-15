"""
LLM-based preference extraction using Databricks Foundation Models only.
"""

import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class PreferenceExtractor:
    """
    Extracts preferences using the Databricks Foundation Model API.
    The WorkspaceClient is created once and reused across all calls.
    """

    def __init__(self, llm_client=None, model_name: str = "databricks-meta-llama-3-1-8b-instruct"):
        """
        Args:
            llm_client: Unused — kept for backwards-compatibility. Client is
                        created internally via WorkspaceClient().
            model_name: Databricks serving endpoint name.
        """
        self.model_name = model_name
        # Instantiate once — avoids per-call auth + connection-pool overhead
        try:
            from databricks.sdk import WorkspaceClient
            self._workspace_client = WorkspaceClient()
        except Exception as e:
            logger.warning(f"[EXTRACTOR] Could not initialise WorkspaceClient: {e}")
            self._workspace_client = None

    def extract(self, user_input: str, context: Optional[str] = None) -> Dict[str, Any]:
        """Extract preferences from a user message via the Databricks LLM."""
        prompt = self._build_extraction_prompt(user_input, context)
        try:
            response_text = self._call_databricks_llm(prompt)
            extracted = self._parse_llm_response(response_text)
            logger.debug("[EXTRACTOR] Extraction succeeded")
            return extracted
        except Exception as e:
            logger.error(f"[EXTRACTOR] Error calling LLM: {e}")
            return self._empty_extraction()

    def _call_databricks_llm(self, prompt: str) -> str:
        """Call the Databricks Foundation Model API using the shared WorkspaceClient."""
        if self._workspace_client is None:
            raise RuntimeError("WorkspaceClient not initialised")

        from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

        response = self._workspace_client.serving_endpoints.query(
            name=self.model_name,
            messages=[ChatMessage(role=ChatMessageRole.USER, content=prompt)],
            max_tokens=500,
            temperature=0.1,
        )
        return response.choices[0].message.content

    def _build_extraction_prompt(self, user_input: str, context: Optional[str]) -> str:
        return f"""You are a precise information extraction system for a shopping assistant. Extract structured data from the user's message.

User message: "{user_input}"
{f"Previous context: {context}" if context else ""}

Your task: Extract shopping preferences and classify the user's intent.

Intent Types (choose ONE):
1. "explicit_preference" - User clearly states a preference
   - Keywords: "I like", "I prefer", "I love", "my favorite"
2. "query" - User is browsing/searching
   - Keywords: "show me", "looking for", "I want to see"
3. "negation" - User explicitly rejects something
   - Keywords: "don't like", "not interested", "avoid", "hate"
4. "gift" - Shopping for someone else
   - Keywords: "gift", "for my", "present", "for someone"
5. "requirement" - User states a need
   - Keywords: "I need", "must have"

CRITICAL RULES:
- "I am looking for X" = query (NOT preference)
- "I like X" = explicit_preference
- "not the bright one" → bright goes in negations.colors
- Style words (minimalist, cute, elegant, vintage, sporty, classic, modern) → attributes
- Color adjectives (bright, dark, light, pastel) are NOT negations
- Only explicit rejections ("not red", "exclude black") go in negations

Output Format (JSON only, no explanation):
{{
  "intent_type": "query",
  "extracted": {{
    "colors": ["red"],
    "brands": ["nike"],
    "materials": ["leather"],
    "bag_types": ["tote"],
    "attributes": ["waterproof"],
    "price_range": {{"min": null, "max": 5000}}
  }},
  "negations": {{
    "colors": [],
    "brands": [],
    "materials": [],
    "bag_types": [],
    "attributes": []
  }},
  "signals": {{
    "is_gift": false,
    "is_special_occasion": false,
    "confidence": 0.7
  }}
}}

Now extract from: "{user_input}"

Return ONLY the JSON object, no other text."""

    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """Parse and normalise LLM JSON response."""
        cleaned = re.sub(r'```json\s*|\s*```', '', response.strip())
        cleaned = re.sub(r'```\s*|\s*```', '', cleaned)

        first = cleaned.find('{')
        last  = cleaned.rfind('}')
        if first >= 0 and last > first:
            cleaned = cleaned[first:last + 1]

        try:
            parsed = json.loads(cleaned)
            parsed["extracted"] = self._normalize_extracted(parsed.get("extracted", {}))
            parsed["negations"] = self._normalize_negations(parsed.get("negations", {}))
            parsed["signals"] = {
                "is_gift":            parsed.get("signals", {}).get("is_gift", False),
                "is_special_occasion": parsed.get("signals", {}).get("is_special_occasion", False),
                "confidence":         parsed.get("signals", {}).get("confidence", 0.0),
            }

            if not self._validate_extraction(parsed):
                logger.warning("[EXTRACTOR] Invalid structure after normalisation — using empty")
                return self._empty_extraction()

            return parsed

        except json.JSONDecodeError as e:
            logger.error(f"[EXTRACTOR] JSON parse error: {e} — snippet: {cleaned[:200]}")
            return self._empty_extraction()

    def _normalize_extracted(self, extracted: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "colors":     extracted.get("colors", []),
            "brands":     extracted.get("brands", []),
            "materials":  extracted.get("materials", []),
            "categories": extracted.get("bag_types", []),
            "features":   extracted.get("attributes", []) + extracted.get("designs", []),
            "price_range": extracted.get("price_range", {"min": None, "max": None}),
        }

    def _normalize_negations(self, negations: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalise negation keys to match the same schema as extracted.
        bag_types → categories, attributes → features.
        """
        return {
            "colors":    negations.get("colors", []),
            "brands":    negations.get("brands", []),
            "materials": negations.get("materials", []),
            "categories": negations.get("categories", []) + negations.get("bag_types", []),
            "features":  negations.get("features", []) + negations.get("attributes", []),
        }

    def _validate_extraction(self, data: Dict[str, Any]) -> bool:
        if not isinstance(data, dict):
            return False
        for key in ("intent_type", "extracted", "negations", "signals"):
            if key not in data:
                return False
        if not isinstance(data["extracted"], dict):
            return False
        return data["intent_type"] in {
            "query", "explicit_preference", "negation", "gift", "requirement"
        }

    def _empty_extraction(self) -> Dict[str, Any]:
        return {
            "intent_type": "query",
            "extracted": {
                "colors": [], "brands": [], "materials": [],
                "categories": [], "features": [],
                "price_range": {"min": None, "max": None},
            },
            "negations": {
                "colors": [], "brands": [], "materials": [],
                "categories": [], "features": [],
            },
            "signals": {
                "is_gift": False,
                "is_special_occasion": False,
                "confidence": 0.0,
            },
        }

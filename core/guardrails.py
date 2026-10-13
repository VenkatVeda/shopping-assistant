"""
Output Guardrails for Shopping Assistant
Implements content safety, factual validation, and response quality checks
"""

import difflib
import re
import time
from typing import Dict, Any, List, Optional, Tuple
from databricks_langchain import ChatDatabricks
from .prompt_loader import load_prompt
from .audit_logger import log_guardrail


class OutputGuardrail:
    def __init__(self, chat_model: ChatDatabricks):
        self.llm = chat_model
        self.max_response_length = 2000
        self.min_response_length = 10

    def validate_response(
        self,
        response: str,
        query: str,
        products: List[dict],
        preferences: Optional[Any] = None,
        audit_context: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        _t0 = time.time()
        print("\n[OUTPUT GUARDRAIL] Starting validation...")

        quick_check = self._quick_validation(response, products)
        if quick_check["status"] == "fail":
            print(f"[OUTPUT GUARDRAIL] Quick check failed: {quick_check['issues']}")
            log_guardrail(
                guardrail_type="output",
                status=quick_check["status"],
                issues=quick_check["issues"],
                corrections_made=quick_check.get("corrections_made", False),
                latency_ms=(time.time() - _t0) * 1000,
                query_preview=query[:120],
                **(audit_context or {}),
            )
            return quick_check

        safety_check = self._check_content_safety(response, query, len(products))
        if safety_check["status"] == "fail":
            print(f"[OUTPUT GUARDRAIL] Safety check failed: {safety_check['issues']}")
            log_guardrail(
                guardrail_type="output",
                status=safety_check["status"],
                issues=safety_check["issues"],
                corrections_made=safety_check.get("corrections_made", False),
                latency_ms=(time.time() - _t0) * 1000,
                query_preview=query[:120],
                **(audit_context or {}),
            )
            return safety_check

        if products and len(products) > 0:
            accuracy_check = self._check_product_facts(response, query, products)
            if accuracy_check["status"] in ["fail", "warning"]:
                print(f"[OUTPUT GUARDRAIL] Fact check {accuracy_check['status']}: {accuracy_check['issues']}")
                log_guardrail(
                    guardrail_type="output",
                    status=accuracy_check["status"],
                    issues=accuracy_check["issues"],
                    corrections_made=accuracy_check.get("corrections_made", False),
                    latency_ms=(time.time() - _t0) * 1000,
                    query_preview=query[:120],
                    **(audit_context or {}),
                )
                return accuracy_check

        print("[OUTPUT GUARDRAIL] All checks passed ✓")
        result = {"status": "pass", "safe_response": response, "issues": [], "corrections_made": False}
        log_guardrail(
            guardrail_type="output",
            status="pass",
            issues=[],
            corrections_made=False,
            latency_ms=(time.time() - _t0) * 1000,
            query_preview=query[:120],
            **(audit_context or {}),
        )
        return result

    def _quick_validation(self, response: str, products: List[dict]) -> Dict[str, Any]:
        issues = []

        if len(response) < self.min_response_length:
            issues.append("Response too short")

        if len(response) > self.max_response_length:
            issues.append("Response too long")
            response = response[:self.max_response_length-3] + "..."

        pii_patterns = [
            (r'\b\d{3}-\d{2}-\d{4}\b', 'SSN'),
            (r'\b\d{16}\b', 'Credit Card'),
            (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', 'Email'),
            (r'\b\d{3}-\d{3}-\d{4}\b', 'Phone'),
        ]

        for pattern, name in pii_patterns:
            if re.search(pattern, response):
                issues.append(f"Potential {name} detected")
                response = re.sub(pattern, '[REDACTED]', response)

        harmful_phrases = [
            'password', 'credit card', 'ssn', 'social security',
            'bank account', 'routing number'
        ]

        for phrase in harmful_phrases:
            if phrase.lower() in response.lower():
                issues.append(f"Sensitive keyword detected: {phrase}")

        if products:
            actual_count = len(products)
            number_mentions = re.findall(r'\b(\d+)\s+(?:products?|items?|bags?|options?)\b', response.lower())
            if number_mentions:
                for num_str in number_mentions:
                    num = int(num_str)
                    if num != actual_count and num > actual_count * 1.5:
                        issues.append(f"Product count mismatch: mentioned {num} but only {actual_count} exist")

        if issues:
            print(f"[OUTPUT GUARDRAIL] Quick validation issues: {issues}")
            if any("too short" in i or "Sensitive keyword" in i for i in issues):
                return {
                    "status": "fail",
                    "safe_response": "I apologize, but I cannot provide that information. Let me help you find products instead.",
                    "issues": issues,
                    "corrections_made": True
                }
            return {
                "status": "warning",
                "safe_response": response,
                "issues": issues,
                "corrections_made": len(issues) > 0
            }

        return {"status": "pass", "safe_response": response, "issues": [], "corrections_made": False}

    def _check_content_safety(self, response: str, query: str, product_count: int) -> Dict[str, Any]:
        """
        Output content-safety check — INACTIVE. This app-side LLM safety check
        has been superseded by the Databricks AI Gateway's own Safety/PII
        service policy configured on the system.ai.meta-llama-3-1-8b-instruct
        route, which already blocks unsafe output via GatewayPolicyBlock
        upstream of this call. Left commented out (rather than deleted) so it
        can be re-enabled if the gateway policy is ever removed.
        """
        # try:
        #     prompt = load_prompt("output_content_safety", {
        #         "response": response,
        #         "query": query,
        #         "product_count": product_count
        #     })
        #
        #     llm_response = self.llm.invoke(prompt)
        #     content = llm_response.content if hasattr(llm_response, 'content') else str(llm_response)
        #
        #     status, issues, corrected = self._parse_guardrail_response(content)
        #
        #     if status == "FAIL":
        #         return {
        #             "status": "fail",
        #             "safe_response": corrected if corrected != "No correction needed" else response,
        #             "issues": issues,
        #             "corrections_made": True
        #         }
        #     elif status == "WARNING":
        #         return {
        #             "status": "warning",
        #             "safe_response": corrected if corrected != "No correction needed" else response,
        #             "issues": issues,
        #             "corrections_made": corrected != "No correction needed"
        #         }
        #
        #     return {"status": "pass", "safe_response": response, "issues": [], "corrections_made": False}
        #
        # except Exception as e:
        #     from .gateway_client import GatewayPolicyBlock
        #     if isinstance(e, GatewayPolicyBlock):
        #         raise
        #     print(f"[OUTPUT GUARDRAIL] Safety check error: {e}")
        #     return {"status": "pass", "safe_response": response, "issues": [f"Safety check error: {str(e)}"], "corrections_made": False}
        return {"status": "pass", "safe_response": response, "issues": [], "corrections_made": False}

    # ── deterministic fact check: product names, brands and prices (no model call) ──

    _PRICE_RE = re.compile(r'\$\s?(\d[\d,]*(?:\.\d{1,2})?)')
    _CLAIM_RE = re.compile(r'\*\*([^*\n]+?)\*\*\s+by\s+', re.IGNORECASE)   # "**Name** by Brand"
    _PRICE_WINDOW = 160   # chars after a product name in which its price is expected

    @staticmethod
    def _norm(text: Any) -> str:
        return re.sub(r'[^a-z0-9]+', '', str(text or '').lower())

    @staticmethod
    def _product_facts(product: Any) -> Dict[str, Any]:
        src = product.get('metadata') if isinstance(product, dict) and product.get('metadata') else product
        src = src if isinstance(src, dict) else {}
        try:
            price = float(str(src.get('price', src.get('product_price'))).replace('$', '').replace(',', '').strip())
        except (ValueError, TypeError):
            price = None
        return {
            "name":  str(src.get('name') or src.get('product_name') or ''),
            "brand": str(src.get('brand') or src.get('product_brand') or ''),
            "price": price,
        }

    def _match_product(self, claimed: str, facts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        c = self._norm(claimed)
        if not c:
            return None
        best, best_ratio = None, 0.0
        for f in facts:
            n = self._norm(f["name"])
            if not n:
                continue
            if c == n or (min(len(c), len(n)) >= 6 and (c in n or n in c)):
                return f
            ratio = difflib.SequenceMatcher(None, c, n).ratio()
            if ratio > best_ratio:
                best, best_ratio = f, ratio
        return best if best_ratio >= 0.88 else None

    def _rebuild_response(self, response: str, facts: List[Dict[str, Any]]) -> str:
        """Keep the model's opening sentences; rewrite the product lines from the real data."""
        first = response.find('**')
        intro = response[:first].strip() if first > 0 else ''
        if not (20 <= len(intro) <= 500) or '$' in intro or '[' in intro:
            intro = "Here are the best matches I found for you:"
        lines = []
        for f in facts[:3]:
            line = f"**{f['name']}**" + (f" by {f['brand']}" if f['brand'] else "")
            if f['price'] is not None:
                line += f"\n**${f['price']:.2f}**"
            lines.append(line)
        return intro + "\n\n" + "\n\n".join(lines)

    def _check_product_facts(self, response: str, query: str, products: List[dict]) -> Dict[str, Any]:
        """
        Compare what the reply says about the described products with the product data.
        Conservative on purpose: only "**Name** by Brand" claims and the price right after
        them are checked, so free-form replies (product discussion, chat) are never rewritten.
        A wrong or invented product line is rewritten from the data; any other price that
        matches nothing is reported as a warning and the reply is left as written.
        """
        ok = {"status": "pass", "safe_response": response, "issues": [], "corrections_made": False}
        facts = [f for f in (self._product_facts(p) for p in products) if f["name"]]
        if not facts:
            return ok

        query_numbers = {round(float(n.replace(',', '')), 2) for n in re.findall(r'\d[\d,]*(?:\.\d+)?', query or '')}
        known_prices = {round(f["price"], 2) for f in facts if f["price"] is not None}
        issues: List[str] = []
        rewrite = False

        claims = list(self._CLAIM_RE.finditer(response))
        for i, m in enumerate(claims):
            fact = self._match_product(m.group(1), facts)
            if fact is None:
                issues.append(f"Product not in the results: {m.group(1).strip()}")
                rewrite = True
                continue
            end = claims[i + 1].start() if i + 1 < len(claims) else len(response)
            block = response[m.end():end]
            if fact["brand"] and self._norm(fact["brand"]) not in self._norm(block[:len(fact["brand"]) + 30]):
                issues.append(f"Brand does not match for {fact['name']}")
                rewrite = True
            for raw in self._PRICE_RE.findall(block[:self._PRICE_WINDOW]):
                value = round(float(raw.replace(',', '')), 2)
                if fact["price"] is not None and abs(value - fact["price"]) > 0.01 and value not in query_numbers:
                    issues.append(f"Price ${value:.2f} does not match {fact['name']} (${fact['price']:.2f})")
                    rewrite = True

        # prices outside any product line (opening text, or a reply with no product lines)
        outside = response[:claims[0].start()] if claims else response
        for raw in self._PRICE_RE.findall(outside):
            value = round(float(raw.replace(',', '')), 2)
            if value not in known_prices and value not in query_numbers:
                issues.append(f"Price ${value:.2f} matches no product in the results")

        if not issues:
            return ok
        if rewrite:
            return {"status": "warning", "safe_response": self._rebuild_response(response, facts),
                    "issues": issues, "corrections_made": True}
        return {"status": "warning", "safe_response": response, "issues": issues, "corrections_made": False}

    def _parse_guardrail_response(self, content: str) -> Tuple[str, List[str], str]:
        status = "PASS"
        issues = []
        corrected = ""

        status_match = re.search(r'SAFETY_STATUS:\s*(PASS|FAIL|WARNING)', content, re.IGNORECASE)
        if status_match:
            status = status_match.group(1).upper()

        issues_match = re.search(r'ISSUES:\s*(.+?)(?=CORRECTED_RESPONSE:|$)', content, re.DOTALL | re.IGNORECASE)
        if issues_match:
            issues_text = issues_match.group(1).strip()
            if issues_text.lower() != "none":
                issues = [i.strip() for i in issues_text.split('\n') if i.strip() and not i.strip().startswith('CORRECTED')]

        corrected_match = re.search(r'CORRECTED_RESPONSE:\s*(.+)', content, re.DOTALL | re.IGNORECASE)
        if corrected_match:
            corrected = corrected_match.group(1).strip()

        return status, issues, corrected

"""
Output Guardrails for Shopping Assistant
Implements content safety, factual validation, and response quality checks
"""

import re
from typing import Dict, Any, List, Optional, Tuple
from databricks_langchain import ChatDatabricks
from .prompt_loader import load_prompt


class OutputGuardrail:
    """
    Comprehensive output guardrail system
    
    Checks:
    1. Content Safety - No inappropriate/harmful content
    2. Factual Accuracy - Verify against actual product data
    3. Response Quality - Length, format, coherence
    4. PII Protection - No personal information leakage
    """
    
    def __init__(self, chat_model: ChatDatabricks):
        self.llm = chat_model
        self.max_response_length = 2000  # characters
        self.min_response_length = 10
    
    def validate_response(
        self,
        response: str,
        query: str,
        products: List[dict],
        preferences: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Main validation function - runs all guardrail checks
        
        Returns:
            dict: {
                "status": "pass" | "warning" | "fail",
                "safe_response": str (original or corrected),
                "issues": List[str],
                "corrections_made": bool
            }
        """
        print("\n[OUTPUT GUARDRAIL] Starting validation...")
        
        # Quick checks first (no LLM needed)
        quick_check = self._quick_validation(response, products)
        if quick_check["status"] == "fail":
            print(f"[OUTPUT GUARDRAIL] Quick check failed: {quick_check['issues']}")
            return quick_check
        
        # Content safety check
        safety_check = self._check_content_safety(response, query, len(products))
        if safety_check["status"] == "fail":
            print(f"[OUTPUT GUARDRAIL] Safety check failed: {safety_check['issues']}")
            return safety_check
        
        # Factual accuracy check (only if products exist)
        if products and len(products) > 0:
            accuracy_check = self._check_factual_accuracy(response, query, products)
            if accuracy_check["status"] in ["fail", "warning"]:
                print(f"[OUTPUT GUARDRAIL] Accuracy check {accuracy_check['status']}: {accuracy_check['issues']}")
                return accuracy_check
        
        print("[OUTPUT GUARDRAIL] All checks passed ✓")
        return {
            "status": "pass",
            "safe_response": response,
            "issues": [],
            "corrections_made": False
        }
    
    def _quick_validation(self, response: str, products: List[dict]) -> Dict[str, Any]:
        """Fast validation checks without LLM"""
        issues = []
        
        # Length check
        if len(response) < self.min_response_length:
            issues.append("Response too short")
        
        if len(response) > self.max_response_length:
            issues.append("Response too long")
            # Truncate with ellipsis
            response = response[:self.max_response_length-3] + "..."
        
        # PII patterns (basic regex)
        pii_patterns = [
            (r'\b\d{3}-\d{2}-\d{4}\b', 'SSN'),  # SSN
            (r'\b\d{16}\b', 'Credit Card'),  # Credit card
            (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', 'Email'),  # Email
            (r'\b\d{3}-\d{3}-\d{4}\b', 'Phone'),  # Phone
        ]
        
        for pattern, name in pii_patterns:
            if re.search(pattern, response):
                issues.append(f"Potential {name} detected")
                # Mask it
                response = re.sub(pattern, '[REDACTED]', response)
        
        # Check for common problematic phrases
        harmful_phrases = [
            'password', 'credit card', 'ssn', 'social security',
            'bank account', 'routing number'
        ]
        
        for phrase in harmful_phrases:
            if phrase.lower() in response.lower():
                issues.append(f"Sensitive keyword detected: {phrase}")
        
        # Check product count hallucination (basic)
        if products:
            actual_count = len(products)
            # Look for numbers in response that don't match product count
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
        """LLM-based content safety check"""
        try:
            prompt = load_prompt("output_content_safety", {
                "response": response,
                "query": query,
                "product_count": product_count
            })
            
            llm_response = self.llm.invoke(prompt)
            content = llm_response.content if hasattr(llm_response, 'content') else str(llm_response)
            
            # Parse response
            status, issues, corrected = self._parse_guardrail_response(content)
            
            if status == "FAIL":
                return {
                    "status": "fail",
                    "safe_response": corrected if corrected != "No correction needed" else response,
                    "issues": issues,
                    "corrections_made": True
                }
            elif status == "WARNING":
                return {
                    "status": "warning",
                    "safe_response": corrected if corrected != "No correction needed" else response,
                    "issues": issues,
                    "corrections_made": corrected != "No correction needed"
                }
            
            return {"status": "pass", "safe_response": response, "issues": [], "corrections_made": False}
            
        except Exception as e:
            print(f"[OUTPUT GUARDRAIL] Safety check error: {e}")
            # On error, be cautious - pass but log
            return {"status": "pass", "safe_response": response, "issues": [f"Safety check error: {str(e)}"], "corrections_made": False}
    
    def _check_factual_accuracy(self, response: str, query: str, products: List[dict]) -> Dict[str, Any]:
        """LLM-based factual accuracy validation"""
        try:
            # Skip accuracy check for product selection queries (just numbers or simple selection phrases)
            import re
            query_stripped = query.strip()
            if re.match(r'^\d+$', query_stripped):
                print(f"[OUTPUT GUARDRAIL] Skipping accuracy check for product selection: '{query_stripped}'")
                return {"status": "pass", "safe_response": response, "issues": [], "corrections_made": False}
            
            # Also skip for common selection phrases
            selection_phrases = ['tell me about', 'show me', 'what about', 'more details', 'more info']
            if any(phrase in query.lower() for phrase in selection_phrases) and len(query.split()) <= 5:
                print(f"[OUTPUT GUARDRAIL] Skipping accuracy check for product selection phrase")
                return {"status": "pass", "safe_response": response, "issues": [], "corrections_made": False}
            
            # Format product data for validation
            products_data = self._format_products_for_validation(products[:10])  # Limit to 10 for token efficiency
            
            prompt = load_prompt("output_factual_validation", {
                "response": response,
                "products_data": products_data,
                "query": query
            })
            
            llm_response = self.llm.invoke(prompt)
            content = llm_response.content if hasattr(llm_response, 'content') else str(llm_response)
            
            # Parse response
            status, errors, corrected = self._parse_validation_response(content)
            
            if status == "FAIL":
                return {
                    "status": "fail",
                    "safe_response": corrected if corrected != "No correction needed" else response,
                    "issues": errors,
                    "corrections_made": True
                }
            elif status == "WARNING":
                return {
                    "status": "warning",
                    "safe_response": corrected if corrected != "No correction needed" else response,
                    "issues": errors,
                    "corrections_made": corrected != "No correction needed"
                }
            
            return {"status": "pass", "safe_response": response, "issues": [], "corrections_made": False}
            
        except Exception as e:
            print(f"[OUTPUT GUARDRAIL] Accuracy check error: {e}")
            # On error, allow to pass but log
            return {"status": "pass", "safe_response": response, "issues": [f"Accuracy check error: {str(e)}"], "corrections_made": False}
    
    def _format_products_for_validation(self, products: List[dict]) -> str:
        """Format product data for LLM validation"""
        formatted = []
        for idx, product in enumerate(products, 1):
            metadata = product.get('metadata', {})
            name = metadata.get('name', 'Unknown')
            brand = metadata.get('brand', 'Unknown')
            price = metadata.get('price', '0')
            
            # Ensure price is formatted consistently
            try:
                price_float = float(str(price).replace('$', '').replace(',', ''))
                price_str = f"${price_float:.2f}"
            except (ValueError, TypeError):
                price_str = str(price)
            
            formatted.append(f"{idx}. {name} by {brand} - {price_str}")
        
        return "\n".join(formatted)
    
    def _parse_guardrail_response(self, content: str) -> Tuple[str, List[str], str]:
        """Parse guardrail LLM response"""
        status = "PASS"
        issues = []
        corrected = ""
        
        # Extract SAFETY_STATUS
        status_match = re.search(r'SAFETY_STATUS:\s*(PASS|FAIL|WARNING)', content, re.IGNORECASE)
        if status_match:
            status = status_match.group(1).upper()
        
        # Extract ISSUES
        issues_match = re.search(r'ISSUES:\s*(.+?)(?=CORRECTED_RESPONSE:|$)', content, re.DOTALL | re.IGNORECASE)
        if issues_match:
            issues_text = issues_match.group(1).strip()
            if issues_text.lower() != "none":
                issues = [i.strip() for i in issues_text.split('\n') if i.strip() and not i.strip().startswith('CORRECTED')]
        
        # Extract CORRECTED_RESPONSE
        corrected_match = re.search(r'CORRECTED_RESPONSE:\s*(.+)', content, re.DOTALL | re.IGNORECASE)
        if corrected_match:
            corrected = corrected_match.group(1).strip()
        
        return status, issues, corrected
    
    def _parse_validation_response(self, content: str) -> Tuple[str, List[str], str]:
        """Parse validation LLM response"""
        status = "PASS"
        errors = []
        corrected = ""
        
        # Extract ACCURACY_STATUS
        status_match = re.search(r'ACCURACY_STATUS:\s*(PASS|FAIL|WARNING)', content, re.IGNORECASE)
        if status_match:
            status = status_match.group(1).upper()
        
        # Extract ERRORS_FOUND
        errors_match = re.search(r'ERRORS_FOUND:\s*(.+?)(?=CORRECTED_RESPONSE:|$)', content, re.DOTALL | re.IGNORECASE)
        if errors_match:
            errors_text = errors_match.group(1).strip()
            if errors_text.lower() != "none":
                errors = [e.strip() for e in errors_text.split('\n') if e.strip() and not e.strip().startswith('CORRECTED')]
        
        # Extract CORRECTED_RESPONSE
        corrected_match = re.search(r'CORRECTED_RESPONSE:\s*(.+)', content, re.DOTALL | re.IGNORECASE)
        if corrected_match:
            corrected = corrected_match.group(1).strip()
        
        return status, errors, corrected

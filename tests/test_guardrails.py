"""
Test script for Output Guardrails
Tests guardrail functionality without requiring Databricks dependencies
"""

import re
from typing import List, Dict

def test_pii_detection():
    """Test PII detection patterns"""
    test_cases = [
        ("My SSN is 123-45-6789", True, "SSN"),
        ("Card number: 1234567890123456", True, "Credit Card"),
        ("Email me at test@example.com", True, "Email"),
        ("Call me at 555-123-4567", True, "Phone"),
        ("Here are your products!", False, None)
    ]
    
    pii_patterns = [
        (r'\b\d{3}-\d{2}-\d{4}\b', 'SSN'),
        (r'\b\d{16}\b', 'Credit Card'),
        (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', 'Email'),
        (r'\b\d{3}-\d{3}-\d{4}\b', 'Phone'),
    ]
    
    print("Testing PII Detection:")
    print("=" * 60)
    
    for text, should_detect, pii_type in test_cases:
        detected = False
        detected_type = None
        
        for pattern, name in pii_patterns:
            if re.search(pattern, text):
                detected = True
                detected_type = name
                break
        
        status = "✓" if detected == should_detect else "✗"
        print(f"{status} Text: '{text[:50]}...'")
        print(f"   Expected: {should_detect} ({pii_type}), Got: {detected} ({detected_type})")
        print()

def test_length_validation():
    """Test response length limits"""
    print("\nTesting Length Validation:")
    print("=" * 60)
    
    short_response = "OK"
    normal_response = "Here are some great products for you!"
    long_response = "A" * 2500  # Over 2000 char limit
    
    min_length = 10
    max_length = 2000
    
    # Test short
    if len(short_response) < min_length:
        print(f"✓ Short response detected: {len(short_response)} chars (< {min_length})")
    else:
        print(f"✗ Short response not detected")
    
    # Test normal
    if min_length <= len(normal_response) <= max_length:
        print(f"✓ Normal response accepted: {len(normal_response)} chars")
    else:
        print(f"✗ Normal response rejected")
    
    # Test long
    if len(long_response) > max_length:
        print(f"✓ Long response detected: {len(long_response)} chars (> {max_length})")
    else:
        print(f"✗ Long response not detected")

def test_product_count_validation():
    """Test product count hallucination detection"""
    print("\nTesting Product Count Validation:")
    print("=" * 60)
    
    test_cases = [
        ("I found 5 products for you!", 5, True),  # Correct
        ("I found 10 products for you!", 3, False),  # Hallucinated
        ("Here are 2 great bags!", 2, True),  # Correct
        ("Check out these 15 options!", 4, False),  # Hallucinated
    ]
    
    for response, actual_count, should_pass in test_cases:
        # Extract mentioned count
        number_mentions = re.findall(r'\b(\d+)\s+(?:products?|items?|bags?|options?)\b', response.lower())
        
        has_error = False
        if number_mentions:
            for num_str in number_mentions:
                num = int(num_str)
                if num != actual_count and num > actual_count * 1.5:
                    has_error = True
        
        passed = not has_error
        status = "✓" if passed == should_pass else "✗"
        
        print(f"{status} Response: '{response}'")
        print(f"   Actual products: {actual_count}, Passed: {passed}, Expected: {should_pass}")
        print()

def test_sensitive_keywords():
    """Test sensitive keyword detection"""
    print("\nTesting Sensitive Keyword Detection:")
    print("=" * 60)
    
    test_cases = [
        ("Here's a great bag!", False),
        ("Enter your password here", True),
        ("Use your credit card to buy", True),
        ("Social security number required", True),
        ("Beautiful leather backpack", False),
    ]
    
    harmful_phrases = [
        'password', 'credit card', 'ssn', 'social security',
        'bank account', 'routing number'
    ]
    
    for text, should_detect in test_cases:
        detected = any(phrase.lower() in text.lower() for phrase in harmful_phrases)
        status = "✓" if detected == should_detect else "✗"
        
        print(f"{status} Text: '{text}'")
        print(f"   Expected detection: {should_detect}, Got: {detected}")
        print()

def test_guardrail_response_parsing():
    """Test parsing of guardrail LLM responses"""
    print("\nTesting Guardrail Response Parsing:")
    print("=" * 60)
    
    test_response = """
SAFETY_STATUS: WARNING
ISSUES: 
- Minor pricing inconsistency
- Product name slightly off
CORRECTED_RESPONSE: Here are 3 great leather backpacks for you!
"""
    
    # Parse status
    status_match = re.search(r'SAFETY_STATUS:\s*(PASS|FAIL|WARNING)', test_response, re.IGNORECASE)
    status = status_match.group(1).upper() if status_match else "UNKNOWN"
    
    # Parse issues
    issues_match = re.search(r'ISSUES:\s*(.+?)(?=CORRECTED_RESPONSE:|$)', test_response, re.DOTALL | re.IGNORECASE)
    issues = []
    if issues_match:
        issues_text = issues_match.group(1).strip()
        if issues_text.lower() != "none":
            issues = [i.strip() for i in issues_text.split('\n') if i.strip() and not i.strip().startswith('CORRECTED')]
    
    # Parse corrected response
    corrected_match = re.search(r'CORRECTED_RESPONSE:\s*(.+)', test_response, re.DOTALL | re.IGNORECASE)
    corrected = corrected_match.group(1).strip() if corrected_match else ""
    
    print(f"Status: {status}")
    print(f"Issues: {issues}")
    print(f"Corrected: {corrected}")
    print()
    
    if status == "WARNING" and len(issues) == 2 and corrected:
        print("✓ Parsing successful!")
    else:
        print("✗ Parsing failed!")

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("OUTPUT GUARDRAIL UNIT TESTS")
    print("=" * 60)
    
    test_pii_detection()
    test_length_validation()
    test_product_count_validation()
    test_sensitive_keywords()
    test_guardrail_response_parsing()
    
    print("\n" + "=" * 60)
    print("TEST SUITE COMPLETE")
    print("=" * 60)

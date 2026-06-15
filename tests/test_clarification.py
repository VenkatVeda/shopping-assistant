"""
Test script for improved clarification system
Demonstrates the new START_FRESH, MERGE, REPLACE functionality
"""

def test_clarification_response_detection():
    """Test detection of user clarification responses"""
    
    test_cases = [
        # Numeric responses
        ("1", "START_FRESH"),
        ("2", "MERGE"),
        ("3", "REPLACE"),
        ("one", "START_FRESH"),
        ("option 2", "MERGE"),
        
        # Keyword responses
        ("start fresh", "START_FRESH"),
        ("new search", "START_FRESH"),
        ("start over", "START_FRESH"),
        ("reset", "START_FRESH"),
        
        ("merge them", "MERGE"),
        ("combine both", "MERGE"),
        ("show me both", "MERGE"),
        ("add them", "MERGE"),
        
        ("replace it", "REPLACE"),
        ("update that", "REPLACE"),
        ("change it", "REPLACE"),
        ("switch to this", "REPLACE"),
        ("just this one", "REPLACE"),
        
        # Non-matching responses
        ("show me bags", None),
        ("what do you think", None),
    ]
    
    print("=" * 70)
    print("CLARIFICATION RESPONSE DETECTION TESTS")
    print("=" * 70)
    
    passed = 0
    failed = 0
    
    for query, expected_action in test_cases:
        detected_action = detect_action(query)
        
        if detected_action == expected_action:
            status = "✓ PASS"
            passed += 1
        else:
            status = "✗ FAIL"
            failed += 1
        
        expected_str = str(expected_action) if expected_action else "None"
        detected_str = str(detected_action) if detected_action else "None"
        print(f"{status} | Query: '{query:30s}' | Expected: {expected_str:15s} | Got: {detected_str}")
    
    print("=" * 70)
    print(f"Results: {passed} passed, {failed} failed out of {len(test_cases)} tests")
    print("=" * 70)


def detect_action(query: str) -> str:
    """Replicate the _detect_clarification_response logic"""
    query_lower = query.lower().strip()
    
    # Check for numeric choices
    if query_lower in ["1", "1️⃣", "one", "option 1", "first"]:
        return "START_FRESH"
    elif query_lower in ["2", "2️⃣", "two", "option 2", "second"]:
        return "MERGE"
    elif query_lower in ["3", "3️⃣", "three", "option 3", "third"]:
        return "REPLACE"
    
    # Check for keyword-based choices
    start_fresh_keywords = ["start fresh", "new search", "start over", "clear", "reset", "forget"]
    merge_keywords = ["merge", "combine", "both", "add", "include"]
    replace_keywords = ["replace", "update", "change", "switch", "instead", "just"]
    
    # Check for START_FRESH
    if any(keyword in query_lower for keyword in start_fresh_keywords):
        return "START_FRESH"
    
    # Check for MERGE
    if any(keyword in query_lower for keyword in merge_keywords):
        return "MERGE"
    
    # Check for REPLACE
    if any(keyword in query_lower for keyword in replace_keywords):
        return "REPLACE"
    
    # No clear choice detected
    return None


def demonstrate_clarification_scenarios():
    """Show example clarification scenarios"""
    
    print("\n" + "=" * 70)
    print("CLARIFICATION SCENARIOS - HOW IT WORKS")
    print("=" * 70)
    
    scenarios = [
        {
            "title": "Scenario 1: Category Change",
            "previous": "blue backpacks under $100",
            "new_query": "show me red tote bags",
            "llm_decision": "START_FRESH (completely different)",
            "clarification": """I see you were looking at blue backpacks. It looks like you want something different now.

Please choose:
1️⃣ START FRESH - Clear all previous filters and search only for this
2️⃣ MERGE - Keep previous filters AND add these new ones
3️⃣ REPLACE - Update specific filters while keeping others

Just reply with the number or tell me what you'd prefer!""",
            "user_response_options": [
                ("1", "START_FRESH → Shows only red tote bags"),
                ("2", "MERGE → Shows blue backpacks + red tote bags"),
                ("3", "REPLACE → Shows red bags (replaces color and category)")
            ]
        },
        {
            "title": "Scenario 2: Adding Filter",
            "previous": "leather bags",
            "new_query": "also show crossbody style",
            "llm_decision": "MERGE (clear merge intent)",
            "clarification": "None needed - automatically merges",
            "result": "Shows leather crossbody bags (merged filters)"
        },
        {
            "title": "Scenario 3: Replacing Attribute",
            "previous": "red bags under $50",
            "new_query": "just make it blue",
            "llm_decision": "REPLACE (clear replace intent)",
            "clarification": "None needed - automatically replaces color",
            "result": "Shows blue bags under $50 (replaced color, kept price)"
        },
        {
            "title": "Scenario 4: Ambiguous Request",
            "previous": "blue crossbody bags",
            "new_query": "show clutches",
            "llm_decision": "Needs clarification (ambiguous)",
            "clarification": """I see you were looking at crossbody bags. Not sure if you want clutches too or instead.

Please choose:
1️⃣ START FRESH - Clear all previous filters and search only for this
2️⃣ MERGE - Keep previous filters AND add these new ones
3️⃣ REPLACE - Update specific filters while keeping others

Just reply with the number or tell me what you'd prefer!""",
            "user_response_options": [
                ("start fresh", "Shows only clutches"),
                ("merge them", "Shows crossbody bags + clutches"),
                ("just clutches", "Shows only clutches")
            ]
        }
    ]
    
    for scenario in scenarios:
        print(f"\n{'─' * 70}")
        print(f"📌 {scenario['title']}")
        print(f"{'─' * 70}")
        print(f"Previous Search: {scenario['previous']}")
        print(f"New Query: \"{scenario['new_query']}\"")
        print(f"LLM Decision: {scenario['llm_decision']}")
        
        if "clarification" in scenario:
            print(f"\nClarification Message:")
            print(scenario['clarification'])
        
        if "user_response_options" in scenario:
            print(f"\nUser Response Options:")
            for response, outcome in scenario['user_response_options']:
                print(f"  • \"{response}\" → {outcome}")
        
        if "result" in scenario:
            print(f"\nAutomatic Result: {scenario['result']}")
    
    print("\n" + "=" * 70)


def show_benefits():
    """Show benefits of new system"""
    
    print("\n" + "=" * 70)
    print("✨ BENEFITS OF NEW CLARIFICATION SYSTEM")
    print("=" * 70)
    
    benefits = [
        {
            "title": "Clear User Intent",
            "description": "Users explicitly choose START_FRESH, MERGE, or REPLACE",
            "before": "System guessed based on keywords (often wrong)",
            "after": "User tells us exactly what they want (1, 2, or 3)"
        },
        {
            "title": "Reduced Confusion",
            "description": "No more ambiguous 'instead' or 'also' interpretations",
            "before": "User says 'show red bags' - unclear if replacing or adding",
            "after": "System asks explicitly with 3 clear options"
        },
        {
            "title": "Smarter Defaults",
            "description": "LLM decides when clarification is actually needed",
            "before": "Always asked for any preference change",
            "after": "Only asks when intent is truly ambiguous"
        },
        {
            "title": "Efficient Flow",
            "description": "No clarification for obvious cases",
            "before": "'Also show blue' → System still asked for confirmation",
            "after": "'Also show blue' → Automatically merges (obvious intent)"
        }
    ]
    
    for i, benefit in enumerate(benefits, 1):
        print(f"\n{i}. {benefit['title']}")
        print(f"   {benefit['description']}")
        print(f"   Before: {benefit['before']}")
        print(f"   After:  {benefit['after']}")
    
    print("\n" + "=" * 70)


if __name__ == "__main__":
    test_clarification_response_detection()
    demonstrate_clarification_scenarios()
    show_benefits()

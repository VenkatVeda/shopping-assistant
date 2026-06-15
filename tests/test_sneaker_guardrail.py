"""
Quick test to verify guardrails correctly reject shoe/sneaker queries
"""

import os
from core.workflow import ShoppingAssistantWorkflow
from databricks_langchain import ChatDatabricks

def test_sneaker_rejection():
    """Test that 'blue sneaker' query is properly rejected"""
    
    print("=" * 80)
    print("TESTING GUARDRAIL FOR SHOE/SNEAKER QUERIES")
    print("=" * 80)
    
    # Initialize workflow
    chat_model = ChatDatabricks(
        endpoint=os.getenv("DATABRICKS_ENDPOINT", "databricks-meta-llama-3-1-70b-instruct"),
        temperature=0.1,
        max_tokens=500
    )
    
    workflow = ShoppingAssistantWorkflow(
        chat_model=chat_model,
        vector_store=None  # Not needed for input guardrail test
    )
    
    test_queries = [
        "Show me blue sneakers",
        "I need running shoes for the gym",
        "blue sneaker",
        "Find me Nike shoes",
        "What boots do you have?",
    ]
    
    print("\nTesting queries that should be REJECTED:\n")
    
    for query in test_queries:
        print(f"┌{'─' * 78}┐")
        print(f"│ Query: {query:<71}│")
        print(f"└{'─' * 78}┘")
        
        # Test the input safety check
        result = workflow._check_input_safety(query)
        
        print(f"  Status:   {result['status']}")
        print(f"  Category: {result['category']}")
        print(f"  Reason:   {result['reason']}")
        
        if result['status'] == 'UNSAFE':
            print(f"\n  🚫 BLOCKED - Response to user:")
            print(f"  \"{result['decline_message']}\"")
            print(f"\n  ✅ TEST PASSED - Query correctly rejected!\n")
        else:
            print(f"\n  ❌ TEST FAILED - Query was not blocked!\n")
    
    print("\n" + "=" * 80)
    print("Testing queries that should be ALLOWED:\n")
    
    allowed_queries = [
        "Show me black leather wallets",
        "I need a laptop bag for work",
        "Blue backpack",
        "Find me a handbag"
    ]
    
    for query in allowed_queries:
        print(f"┌{'─' * 78}┐")
        print(f"│ Query: {query:<71}│")
        print(f"└{'─' * 78}┘")
        
        result = workflow._check_input_safety(query)
        
        print(f"  Status:   {result['status']}")
        print(f"  Category: {result['category']}")
        
        if result['status'] == 'SAFE':
            print(f"\n  ✅ TEST PASSED - Query correctly allowed!\n")
        else:
            print(f"\n  ❌ TEST FAILED - Query was incorrectly blocked!")
            print(f"  Reason: {result['reason']}\n")
    
    print("=" * 80)

if __name__ == "__main__":
    test_sneaker_rejection()

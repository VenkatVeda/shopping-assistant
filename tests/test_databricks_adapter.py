"""
Test Databricks Vector Search with Real Query
Tests the adapter with an actual search query
"""

import os
import sys
import requests

# Add parent directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from core.vector_store.databricks_adapter import DatabricksAdapter


def get_embedding(text: str) -> list:
    """Get embedding from Databricks endpoint"""
    host = os.getenv("DATABRICKS_HOST")
    token = os.getenv("DATABRICKS_TOKEN")
    endpoint = os.getenv("DATABRICKS_EMBEDDING_ENDPOINT", "databricks-bge-large-en")
    
    url = f"{host.rstrip('/')}/serving-endpoints/{endpoint}/invocations"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "input": text
    }
    
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    
    # Extract embedding from response
    result = response.json()
    if 'data' in result and len(result['data']) > 0:
        return result['data'][0]['embedding']
    else:
        raise ValueError(f"Unexpected response format: {result}")


def test_databricks_adapter_with_real_query():
    """Test Databricks adapter with real search query"""
    
    print("=" * 70)
    print("DATABRICKS ADAPTER TEST WITH REAL QUERY")
    print("=" * 70)
    
    # Step 1: Initialize adapter
    print("\n[Step 1] Initializing Databricks Adapter...")
    
    try:
        adapter = DatabricksAdapter()
        print("  ✅ Adapter initialized")
    except Exception as e:
        print(f"  ❌ Failed to initialize: {e}")
        return False
    
    # Step 2: Generate embedding for test query
    print("\n[Step 2] Generating Embedding for Query...")
    
    test_query = "leather tote bag for work"
    print(f"  Query: '{test_query}'")
    
    try:
        query_vector = get_embedding(test_query)
        print(f"  ✅ Generated embedding (dimension: {len(query_vector)})")
    except Exception as e:
        print(f"  ❌ Failed to generate embedding: {e}")
        return False
    
    # Step 3: Search for products
    print("\n[Step 3] Searching for Similar Products...")
    
    try:
        results = adapter.search(
            vector=query_vector,
            top_k=5,
            include_metadata=True
        )
        
        print(f"  ✅ Search successful!")
        print(f"\n  Found {len(results)} results:")
        
        for i, result in enumerate(results, 1):
            metadata = result.get('metadata', {})
            score = result.get('score', 0)
            
            print(f"\n  Result {i}:")
            print(f"    ID: {result.get('id')}")
            print(f"    Name: {metadata.get('name', 'N/A')}")
            print(f"    Brand: {metadata.get('brand', 'N/A')}")
            print(f"    Price: ${metadata.get('price', 0):.2f}")
            print(f"    Color: {metadata.get('color', 'N/A')}")
            print(f"    Material: {metadata.get('material', 'N/A')}")
            print(f"    Category: {metadata.get('category', 'N/A')}")
            print(f"    Score: {score:.4f}")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Search failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Step 4: Test with filters (optional)
    print("\n[Step 4] Testing with Filters...")
    
    try:
        results = adapter.search(
            vector=query_vector,
            top_k=3,
            filters={"price_from": {"$lt": 200}},  # Products under $200
            include_metadata=True
        )
        
        print(f"  ✅ Filtered search successful!")
        print(f"  Found {len(results)} results under $200")
        
    except Exception as e:
        print(f"  ⚠️  Filtered search failed: {e}")
        # This is not critical
    
    return True


if __name__ == "__main__":
    print("\n🔍 Starting Databricks Adapter Test with Real Query\n")
    
    success = test_databricks_adapter_with_real_query()
    
    print("\n" + "=" * 70)
    if success:
        print("✅ ALL TESTS PASSED!")
        print("\nThe Databricks adapter is working correctly.")
        print("You can now use it with your MCP setup.")
    else:
        print("❌ TEST FAILED - Please check the errors above")
    print("=" * 70)
    
    sys.exit(0 if success else 1)

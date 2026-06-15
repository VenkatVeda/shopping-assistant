"""
Standalone Test for Databricks Vector Search Adapter
Does not import workflow or other dependencies
"""

import os
import sys
import requests
from typing import List, Dict, Any, Optional
from databricks.vector_search.client import VectorSearchClient


class SimpleDatabricksAdapter:
    """Simplified Databricks adapter for testing"""
    
    def __init__(self):
        self.host = os.getenv("DATABRICKS_HOST")
        self.token = os.getenv("DATABRICKS_TOKEN")
        self.index_name = os.getenv("DATABRICKS_VECTOR_INDEX", "sandbox.venkat.bags_embeddings_index")
        self.endpoint_name = os.getenv("DATABRICKS_VECTOR_ENDPOINT")
        
        print(f"Connecting to: {self.host}")
        print(f"Index: {self.index_name}")
        print(f"Endpoint: {self.endpoint_name}\n")
        
        # Initialize client
        self.client = VectorSearchClient(
            workspace_url=self.host,
            personal_access_token=self.token,
            disable_notice=True
        )
        
        self.index = self.client.get_index(
            endpoint_name=self.endpoint_name,
            index_name=self.index_name
        )
    
    def search(self, vector: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """Search for similar vectors"""
        
        columns = [
            "product_id", "name_clean", "brand_clean", "category_clean",
            "gender", "price_from", "price_tier", "primary_color",
            "material_type", "is_available", "on_sale", "primary_image_url",
            "embedding_text"
        ]
        
        results = self.index.similarity_search(
            query_vector=vector,
            columns=columns,
            num_results=top_k
        )
        
        # Parse results
        standardized = []
        data_array = results.get('result', {}).get('data_array', [])
        
        for row in data_array:
            metadata = {
                'product_id': str(row[0]) if len(row) > 0 else "",
                'name': str(row[1]) if len(row) > 1 else "",
                'brand': str(row[2]) if len(row) > 2 else "",
                'category': str(row[3]) if len(row) > 3 else "",
                'gender': str(row[4]) if len(row) > 4 else "",
                'price': float(row[5]) if len(row) > 5 else 0.0,
                'price_tier': str(row[6]) if len(row) > 6 else "",
                'color': str(row[7]) if len(row) > 7 else "",
                'material': str(row[8]) if len(row) > 8 else "",
                'available': bool(row[9]) if len(row) > 9 else True,
                'on_sale': bool(row[10]) if len(row) > 10 else False,
                'image_url': str(row[11]) if len(row) > 11 else "",
                'description': str(row[12]) if len(row) > 12 else ""
            }
            
            score = float(row[-1]) if row else 0.0
            
            standardized.append({
                'id': metadata['product_id'],
                'score': score,
                'metadata': metadata
            })
        
        return standardized


def get_embedding(text: str) -> List[float]:
    """Get embedding from Databricks"""
    host = os.getenv("DATABRICKS_HOST")
    token = os.getenv("DATABRICKS_TOKEN")
    endpoint = os.getenv("DATABRICKS_EMBEDDING_ENDPOINT", "databricks-bge-large-en")
    
    url = f"{host.rstrip('/')}/serving-endpoints/{endpoint}/invocations"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    response = requests.post(url, headers=headers, json={"input": text})
    response.raise_for_status()
    
    result = response.json()
    if 'data' in result and len(result['data']) > 0:
        return result['data'][0]['embedding']
    raise ValueError(f"Unexpected response: {result}")


def main():
    print("=" * 70)
    print("DATABRICKS ADAPTER TEST WITH REAL QUERY")
    print("=" * 70)
    
    # Test 1: Initialize adapter
    print("\n[Test 1] Initializing Adapter...")
    adapter = SimpleDatabricksAdapter()
    print("✅ Adapter initialized\n")
    
    # Test 2: Generate embedding
    print("[Test 2] Generating Query Embedding...")
    query = "leather tote bag for work"
    print(f"Query: '{query}'")
    embedding = get_embedding(query)
    print(f"✅ Embedding generated (dimension: {len(embedding)})\n")
    
    # Test 3: Search
    print("[Test 3] Searching for Similar Products...")
    results = adapter.search(embedding, top_k=5)
    print(f"✅ Found {len(results)} results\n")
    
    for i, result in enumerate(results, 1):
        meta = result['metadata']
        print(f"Result {i}:")
        print(f"  Name: {meta['name']}")
        print(f"  Brand: {meta['brand']}")
        print(f"  Price: ${meta['price']:.2f}")
        print(f"  Color: {meta['color']}")
        print(f"  Material: {meta['material']}")
        print(f"  Score: {result['score']:.4f}\n")
    
    print("=" * 70)
    print("✅ ALL TESTS PASSED!")
    print("\nThe Databricks vector search adapter is working correctly.")
    print("You can now integrate it with your MCP setup.")
    print("=" * 70)


if __name__ == "__main__":
    main()

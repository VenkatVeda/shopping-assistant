"""
Direct Vector Store Client
Bypasses MCP for environments where subprocess spawning isn't supported (like Databricks Apps)
"""

import os
import sys
from typing import List, Dict, Any, Optional


class DirectVectorClient:
    """
    Direct vector store client that uses adapters without MCP
    Use this in Databricks Apps where MCP subprocess spawning doesn't work
    """
    
    def __init__(self):
        """Initialize direct vector client based on VECTOR_STORE_TYPE"""
        vector_store_type = os.getenv("VECTOR_STORE_TYPE", "pinecone").lower()
        
        print(f"[DIRECT CLIENT] Initializing with {vector_store_type} adapter", file=sys.stderr)
        
        if vector_store_type == "databricks":
            from .databricks_adapter import DatabricksAdapter
            self.adapter = DatabricksAdapter()
        elif vector_store_type == "pinecone":
            from .pinecone_adapter import PineconeAdapter
            self.adapter = PineconeAdapter()
        else:
            raise ValueError(f"Unsupported vector store type: {vector_store_type}")
        
        print(f"[DIRECT CLIENT] Adapter initialized successfully", file=sys.stderr)
    
    def search(
        self,
        vector: List[float],
        top_k: int = 10,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Search for similar vectors
        
        Args:
            vector: Query embedding
            top_k: Number of results
            filters: Metadata filters
            
        Returns:
            List of matching products
        """
        try:
            results = self.adapter.search(
                vector=vector,
                top_k=top_k,
                filters=filters
            )
            return results
        except Exception as e:
            print(f"[DIRECT CLIENT] Search error: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return []
    
    def fetch_by_id(self, product_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a product by ID
        
        Args:
            product_id: Product ID to fetch
            
        Returns:
            Product data or None
        """
        try:
            result = self.adapter.fetch_by_id(product_id)
            return result
        except Exception as e:
            print(f"[DIRECT CLIENT] Fetch error: {e}", file=sys.stderr)
            return None
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get vector store statistics
        
        Returns:
            Dictionary with stats
        """
        try:
            return self.adapter.get_stats()
        except Exception as e:
            print(f"[DIRECT CLIENT] Stats error: {e}", file=sys.stderr)
            return {"error": str(e)}

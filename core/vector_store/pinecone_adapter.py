"""
Pinecone Vector Store Adapter
Implements VectorStoreInterface for Pinecone
"""

import os
import json
from typing import List, Dict, Any, Optional
from pinecone import Pinecone
from .base import VectorStoreInterface


class PineconeAdapter(VectorStoreInterface):
    """Pinecone implementation of VectorStoreInterface"""
    
    def __init__(self, api_key: Optional[str] = None, index_name: Optional[str] = None):
        """
        Initialize Pinecone adapter
        
        Args:
            api_key: Pinecone API key (defaults to env var)
            index_name: Index name (defaults to env var)
        """
        self.api_key = api_key or os.getenv("PINECONE_API_KEY")
        if not self.api_key:
            raise ValueError("PINECONE_API_KEY is required")
        
        self.index_name = index_name or os.getenv("PINECONE_INDEX_NAME", "bags-index")
        
        # Initialize Pinecone client
        self.pc = Pinecone(api_key=self.api_key)
        self.index = self.pc.Index(self.index_name)
        
        # Log to stderr to avoid interfering with MCP protocol
        import sys
        print(f"[PINECONE ADAPTER] Connected to index: {self.index_name}", file=sys.stderr)
    
    def search(
        self,
        vector: List[float],
        top_k: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        include_metadata: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Search Pinecone index for similar vectors
        
        Args:
            vector: Query embedding
            top_k: Number of results
            filters: Metadata filters
            include_metadata: Include metadata in response
            
        Returns:
            List of matches with standardized format
        """
        try:
            search_results = self.index.query(
                vector=vector,
                top_k=top_k,
                include_metadata=include_metadata,
                filter=filters
            )
            
            # Standardize format
            results = []
            for match in search_results.get('matches', []):
                results.append({
                    'id': match.get('id'),
                    'score': match.get('score', 0),
                    'metadata': match.get('metadata', {})
                })
            
            return results
            
        except Exception as e:
            import sys
            print(f"[PINECONE ADAPTER] Search error: {e}", file=sys.stderr)
            return []
    
    def fetch_by_id(self, vector_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a single product from Pinecone by ID
        
        Args:
            vector_id: Product ID to fetch
            
        Returns:
            Product data or None
        """
        try:
            # Try multiple ID formats
            id_variants = [
                str(vector_id),
                str(int(vector_id)) if str(vector_id).isdigit() else None,
                f"{int(vector_id):010d}" if str(vector_id).isdigit() else None,
                f"{int(vector_id):012d}" if str(vector_id).isdigit() else None,
            ]
            id_variants = [v for v in id_variants if v is not None]
            
            result = self.index.fetch(ids=id_variants)
            
            if result and 'vectors' in result:
                for id_variant in id_variants:
                    if id_variant in result['vectors']:
                        vector_data = result['vectors'][id_variant]
                        return {
                            'id': str(vector_id),
                            'values': vector_data.get('values', []),
                            'metadata': vector_data.get('metadata', {})
                        }
            
            return None
            
        except Exception as e:
            import sys
            print(f"[PINECONE ADAPTER] Fetch error for {vector_id}: {e}", file=sys.stderr)
            return None
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get Pinecone index statistics
        
        Returns:
            Dict with index stats
        """
        try:
            stats = self.index.describe_index_stats()
            return {
                "total_vectors": stats.total_vector_count,
                "dimension": stats.dimension,
                "index_fullness": getattr(stats, 'index_fullness', 'N/A'),
                "provider": "pinecone",
                "index_name": self.index_name
            }
        except Exception as e:
            import sys
            print(f"[PINECONE ADAPTER] Stats error: {e}", file=sys.stderr)
            return {
                "total_vectors": 0,
                "dimension": 0,
                "provider": "pinecone",
                "index_name": self.index_name,
                "error": str(e)
            }
    
    def upsert(self, vectors: List[Dict[str, Any]]) -> None:
        """
        Upload vectors to Pinecone
        
        Args:
            vectors: List of vectors with id, values, and metadata
        """
        try:
            pinecone_vectors = []
            for v in vectors:
                clean_metadata = {}
                for key, value in v['metadata'].items():
                    if value is not None:
                        clean_metadata[key] = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
                
                pinecone_vectors.append({
                    "id": v['id'],
                    "values": v['values'],
                    "metadata": clean_metadata
                })
            
            self.index.upsert(vectors=pinecone_vectors)
            import sys
            print(f"[PINECONE ADAPTER] Upserted {len(pinecone_vectors)} vectors", file=sys.stderr)
            
        except Exception as e:
            import sys
            print(f"[PINECONE ADAPTER] Upsert error: {e}", file=sys.stderr)
            raise
    
    def delete(self, ids: List[str]) -> None:
        """
        Delete vectors from Pinecone
        
        Args:
            ids: List of vector IDs to delete
        """
        try:
            self.index.delete(ids=ids)
            import sys
            print(f"[PINECONE ADAPTER] Deleted {len(ids)} vectors", file=sys.stderr)
        except Exception as e:
            import sys
            print(f"[PINECONE ADAPTER] Delete error: {e}", file=sys.stderr)
            raise

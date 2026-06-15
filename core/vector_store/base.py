"""
Abstract Base Class for Vector Store Operations
Provides a unified interface for different vector databases
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class VectorStoreInterface(ABC):
    """
    Abstract interface for vector store operations.
    All vector store adapters must implement this interface.
    """
    
    @abstractmethod
    def search(
        self,
        vector: List[float],
        top_k: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        include_metadata: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Search for similar vectors
        
        Args:
            vector: Query vector embedding
            top_k: Number of results to return
            filters: Metadata filters to apply
            include_metadata: Whether to include metadata in results
            
        Returns:
            List of matching results with id, score, and metadata
        """
        pass
    
    @abstractmethod
    def fetch_by_id(self, vector_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a single vector by ID
        
        Args:
            vector_id: The ID of the vector to fetch
            
        Returns:
            Vector data with metadata, or None if not found
        """
        pass
    
    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the vector store
        
        Returns:
            Dict with stats like total_vectors, dimension, etc.
        """
        pass
    
    @abstractmethod
    def upsert(self, vectors: List[Dict[str, Any]]) -> None:
        """
        Insert or update vectors
        
        Args:
            vectors: List of vector dicts with id, values, and metadata
        """
        pass
    
    @abstractmethod
    def delete(self, ids: List[str]) -> None:
        """
        Delete vectors by IDs
        
        Args:
            ids: List of vector IDs to delete
        """
        pass

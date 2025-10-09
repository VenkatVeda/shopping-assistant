# services/search_service.py
from langchain_core.documents import Document
from models.preferences import UserPreferences
from utils.validators import matches_preferences
from typing import List

class SearchService:
    def __init__(self, vector_service, data_loader):
        self.vector_service = vector_service
        self.data_loader = data_loader
    
    def build_search_query_with_preferences(self, user_question: str, preferences: UserPreferences) -> str:
        query_parts = [user_question]
        
        if preferences.materials:
            query_parts.extend(preferences.materials)
        if preferences.colors:
            query_parts.extend(preferences.colors)
        if preferences.categories:
            query_parts.extend(preferences.categories)
        if preferences.brands:
            query_parts.extend(preferences.brands)
        
        return " ".join(query_parts)
    
    def search_products(self, query: str, preferences: UserPreferences, max_results: int = 6) -> List[Document]:
        if not self.vector_service.is_available():
            return []
        
        # Check if we should use database-first filtering approach
        if preferences.price_min is not None or preferences.price_max is not None:
            print(f"🔄 Using database-first filtering for price query: min=${preferences.price_min}, max=${preferences.price_max}")
            return self._search_with_database_first_filtering(query, preferences, max_results)
        
        # For non-price searches, use standard semantic-first approach
        return self._search_with_semantic_first(query, preferences, max_results)
    
    def search_all_products(self, query: str, preferences: UserPreferences, max_results: int = 50) -> List[Document]:
        """Search for all matching products without strict limiting for pagination support"""
        if not self.vector_service.is_available():
            return []
        
        # Use a higher limit for pagination scenarios
        if preferences.price_min is not None or preferences.price_max is not None:
            print(f"🔄 Using database-first filtering for paginated query: min=${preferences.price_min}, max=${preferences.price_max}")
            return self._search_with_database_first_filtering(query, preferences, max_results)
        
        # For non-price searches, use semantic-first with higher limit
        return self._search_with_semantic_first(query, preferences, max_results)
    
    def _search_with_database_first_filtering(self, query: str, preferences: UserPreferences, max_results: int = 6) -> List[Document]:
        """Database-first approach: Filter database first, then apply semantic search to filtered results"""
        
        # Step 1: Get all products from vector database that match price criteria
        print("📊 Step 1: Filtering database by price criteria...")
        all_docs = self.vector_service.get_all_documents()  # We'll need to implement this
        
        # Step 2: Apply preference filters (especially price) to all documents
        price_filtered_docs = []
        for doc in all_docs:
            if matches_preferences(doc, preferences):
                try:
                    price = float(doc.metadata.get('price', 0))
                    doc.metadata['price'] = price
                    price_filtered_docs.append(doc)
                except (ValueError, TypeError):
                    continue
        
        print(f"📊 Step 2: Found {len(price_filtered_docs)} products matching price criteria")
        
        # Step 3: Apply URL filter
        url_filtered_docs = []
        for doc in price_filtered_docs:
            if doc.metadata.get('url') in self.data_loader.url_to_image:
                url_filtered_docs.append(doc)
        
        print(f"📊 Step 3: {len(url_filtered_docs)} products have images")
        
        # Step 4: If we have many results, use semantic search to rank them
        if len(url_filtered_docs) > max_results:
            print(f"📊 Step 4: Ranking {len(url_filtered_docs)} products with semantic search...")
            enhanced_query = self.build_search_query_with_preferences(query, preferences)
            
            # Create a temporary vector store from filtered docs for semantic ranking
            ranked_docs = self._rank_documents_semantically(enhanced_query, url_filtered_docs)
        else:
            # If we have few results, just sort by price
            ranked_docs = url_filtered_docs
        
        # Step 5: Sort by price descending and limit results
        ranked_docs.sort(key=lambda x: float(x.metadata.get('price', 0)), reverse=True)
        final_results = ranked_docs[:max_results]
        
        print(f"📊 Step 5: Returning {len(final_results)} final results")
        return final_results
    
    def _search_with_semantic_first(self, query: str, preferences: UserPreferences, max_results: int = 6) -> List[Document]:
        """Standard semantic-first approach for non-price queries"""
        enhanced_query = self.build_search_query_with_preferences(query, preferences)
        
        # Use higher search_k for pagination scenarios
        search_k = max(30, max_results * 2) if max_results > 6 else 30
        docs = self.vector_service.search(enhanced_query, k=search_k)
        
        # Filter and sort results
        filtered_docs = []
        for doc in docs:
            if (doc.metadata.get('url') in self.data_loader.url_to_image and 
                matches_preferences(doc, preferences)):
                try:
                    price = float(doc.metadata.get('price', 0))
                    doc.metadata['price'] = price
                    filtered_docs.append(doc)
                except (ValueError, TypeError):
                    continue
        
        # Sort by price descending and limit results
        filtered_docs.sort(key=lambda x: float(x.metadata.get('price', 0)), reverse=True)
        return filtered_docs[:max_results]
    
    def _rank_documents_semantically(self, query: str, docs: List[Document]) -> List[Document]:
        """Rank a set of documents by semantic similarity to query"""
        if not docs:
            return docs
        
        try:
            # Simple ranking by text similarity for now
            # In a full implementation, we'd use the embedding model to score similarity
            query_lower = query.lower()
            query_terms = set(query_lower.split())
            
            scored_docs = []
            for doc in docs:
                # Calculate a simple similarity score
                doc_text = f"{doc.page_content} {doc.metadata.get('name', '')} {doc.metadata.get('brand', '')}".lower()
                doc_terms = set(doc_text.split())
                
                # Jaccard similarity
                intersection = len(query_terms.intersection(doc_terms))
                union = len(query_terms.union(doc_terms))
                similarity = intersection / union if union > 0 else 0
                
                scored_docs.append((similarity, doc))
            
            # Sort by similarity score descending
            scored_docs.sort(key=lambda x: x[0], reverse=True)
            return [doc for _, doc in scored_docs]
        
        except Exception as e:
            print(f"Error in semantic ranking: {e}")
            return docs
    
    def should_search_products(self, user_input: str, has_preferences: bool) -> bool:
        user_input_lower = user_input.lower()
        
        search_keywords = [
            'show', 'find', 'search', 'look', 'recommend', 'suggest', 'want', 'need',
            'display', 'see', 'browse', 'shopping', 'buy', 'purchase', 'get me',
            'what about', 'how about', 'any', 'do you have'
        ]
        
        product_terms = [
            'bag', 'handbag', 'purse', 'tote', 'backpack', 'clutch', 'wallet',
            'crossbody', 'shoulder', 'messenger', 'satchel', 'briefcase'
        ]
        
        refinement_terms = ['leather', 'canvas', 'black', 'brown', 'cheap', 'expensive', 'small', 'large']
        
        # Check for explicit search intent
        for keyword in search_keywords:
            if keyword in user_input_lower:
                return True
        
        # Check if it's a refinement with active preferences
        if has_preferences:
            for term in refinement_terms:
                if term in user_input_lower:
                    return True
            if len(user_input.split()) <= 3:
                return True
        
        # Check for product terms
        for term in product_terms:
            if term in user_input_lower:
                return True
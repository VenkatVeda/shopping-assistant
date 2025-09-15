# services/search_service.py
from langchain.schema import Document
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
        
        enhanced_query = self.build_search_query_with_preferences(query, preferences)
        docs = self.vector_service.search(enhanced_query, k=30)
        
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
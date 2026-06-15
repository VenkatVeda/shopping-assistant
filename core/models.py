from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class UserQuery(BaseModel):
    """Represents the user's raw query and extracted intent."""
    raw_query: str
    intent: str = "chat"  # chat, shopping, preference_update
    completeness_score: float = 0.0
    preferences: Optional['SearchPreferences'] = None
    
class SearchPreferences(BaseModel):
    """Structured preferences extracted from the query."""
    # Price filters
    price_min: float = 0.0
    price_max: Optional[float] = None
    
    # Basic filters
    brands: List[str] = Field(default_factory=list)
    categories: List[str] = Field(default_factory=list)
    colors: List[str] = Field(default_factory=list)
    materials: List[str] = Field(default_factory=list)
    features: List[str] = Field(default_factory=list)
    
    # Extended filters (NEW)
    closure_types: List[str] = Field(default_factory=list)  # zipper, magnetic, clasp, flap, snap, buckle, drawstring
    strap_types: List[str] = Field(default_factory=list)    # adjustable, removable, chain, shoulder_strap, crossbody_strap, leather_strap
    sizes: List[str] = Field(default_factory=list)          # small, medium, large, mini
    has_zipper: Optional[bool] = None                       # Convenience field for closure_type=zipper
    
    # Exclusion filters
    excluded_colors: List[str] = Field(default_factory=list)
    excluded_brands: List[str] = Field(default_factory=list)
    excluded_categories: List[str] = Field(default_factory=list)
    excluded_materials: List[str] = Field(default_factory=list)

    # Brands the user mentioned that are not in our catalogue (for user messaging only)
    unknown_brands: List[str] = Field(default_factory=list)

class Product(BaseModel):
    """Represents a product returned from search."""
    id: str
    name: str
    brand: str
    category: str
    price: float
    description: str
    url: str
    image_url: str
    relevance_score: float = 0.0
    rerank_score: Optional[float] = None  # LLM-based reranking score (0-100)
    rerank_reason: Optional[str] = None   # Explanation for rerank score
    metadata: Dict[str, Any] = Field(default_factory=dict)

class AgentState(BaseModel):
    """Tracks the state of the conversation and search."""
    session_id: str
    turn_count: int = 0
    history: List[Dict[str, str]] = Field(default_factory=list)
    current_query: Optional[UserQuery] = None
    current_preferences: SearchPreferences = Field(default_factory=SearchPreferences)
    search_results: List[Product] = Field(default_factory=list)
    last_node: str = "START"
    clarification_needed: bool = False
    clarification_question: Optional[str] = None
    
    def update_preferences(self, new_prefs: dict):
        """Update current preferences with new values (merge logic)."""
        # Simple merge for lists, overwrite for scalars if not None
        for key, value in new_prefs.items():
            if value is None:
                continue
            
            if isinstance(value, list):
                current_list = getattr(self.current_preferences, key)
                # Add unique new items
                for item in value:
                    if item not in current_list:
                        current_list.append(item)
            else:
                setattr(self.current_preferences, key, value)

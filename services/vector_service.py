# services/vector_service.py
from langchain_community.vectorstores import Chroma
from chromadb import PersistentClient
from langchain_core.documents import Document
from config.settings import VECTOR_CONFIG

class VectorService:
    def __init__(self, embeddings):
        self.vectorstore = None
        self.embeddings = embeddings
        self._initialize_vectorstore()
    
    def _initialize_vectorstore(self):
        if not self.embeddings:
            return
            
        try:
            client = PersistentClient(path=VECTOR_CONFIG["chroma_dir"])
            self.vectorstore = Chroma(
                client=client,
                collection_name=VECTOR_CONFIG["collection_name"],
                embedding_function=self.embeddings
            )
            print("Vector database loaded successfully")
        except Exception as e:
            print(f"Vector database not available: {e}")
    
    def search(self, query: str, k: int = 30) -> list[Document]:
        if not self.vectorstore:
            return []
        return self.vectorstore.similarity_search(query, k=k)
    
    def get_all_documents(self) -> list[Document]:
        """Get all documents from the vector database"""
        if not self.vectorstore:
            return []
        
        try:
            # Get all documents by doing a very broad search with high k
            # This is a workaround since ChromaDB doesn't have a direct "get_all" method
            # We search with a generic term and high k to get all results
            all_docs = self.vectorstore.similarity_search("bag handbag tote", k=10000)
            print(f"Retrieved {len(all_docs)} documents from vector database")
            return all_docs
        except Exception as e:
            print(f"Error retrieving all documents: {e}")
            return []
    
    def is_available(self) -> bool:
        return self.vectorstore is not None

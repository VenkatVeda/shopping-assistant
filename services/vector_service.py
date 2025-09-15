# services/vector_service.py
from langchain.vectorstores.chroma import Chroma
from chromadb import PersistentClient
from langchain.schema import Document
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
    
    def is_available(self) -> bool:
        return self.vectorstore is not None

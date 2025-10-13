# services/azure_service.py
import time
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
from langchain_core.prompts import PromptTemplate
from langchain.chains import LLMChain
from langchain_community.callbacks.manager import get_openai_callback
from langsmith import Client
from config.settings import AZURE_CONFIG, LANGSMITH_CONFIG
from config.prompts import PREFERENCE_PROMPT, GENERAL_CONVERSATION_PROMPT

class AzureService:
    def __init__(self):
        self.llm = None
        self.embeddings = None
        self.preference_chain = None
        self.conversation_chain = None
        
        # LangSmith client for evaluation
        self.langsmith_client = None
        if LANGSMITH_CONFIG["api_key"]:
            try:
                self.langsmith_client = Client()
                print(f"🔍 LangSmith client initialized for project: {LANGSMITH_CONFIG['project']}")
            except Exception as e:
                print(f"⚠️ LangSmith client initialization failed: {e}")
        
        self._initialize_azure()
    
    def _initialize_azure(self):
        try:
            self.embeddings = AzureOpenAIEmbeddings(
                deployment=AZURE_CONFIG["embedding_deployment"],
                api_key=AZURE_CONFIG["api_key"],
                api_version=AZURE_CONFIG["api_version"],
                azure_endpoint=AZURE_CONFIG["azure_endpoint"]
            )

            self.llm = AzureChatOpenAI(
                deployment_name=AZURE_CONFIG["llm_deployment"],
                api_key=AZURE_CONFIG["api_key"],
                api_version=AZURE_CONFIG["api_version"],
                azure_endpoint=AZURE_CONFIG["azure_endpoint"],
                temperature=0.5
            )

            # Initialize chains with LangSmith auto-tracing
            self.preference_chain = LLMChain(llm=self.llm, prompt=PREFERENCE_PROMPT, verbose=False)
            self.conversation_chain = LLMChain(llm=self.llm, prompt=GENERAL_CONVERSATION_PROMPT, verbose=False)
            
            # Store last metrics for UI access
            self.last_metrics = None
            
        except Exception as e:
            print(f"Warning: Azure OpenAI not configured: {e}")

    def run_with_tracking(self, chain, inputs):
        """Run LLM chain with token and latency tracking + LangSmith integration"""
        start_time = time.time()
        
        try:
            with get_openai_callback() as cb:
                # This will be automatically tracked by LangSmith AND give us local metrics
                result = chain.invoke(inputs)
                
                # Calculate metrics for local display
                latency = time.time() - start_time
                tokens = cb.total_tokens
                cost = cb.total_cost
                
                # Log performance metrics for console monitoring
                timestamp = time.strftime("%H:%M:%S")
                print(f"🤖 [{timestamp}] LLM Call | Tokens: {tokens} | Latency: {latency:.2f}s | Cost: ${cost:.4f}")
                
                # Store metrics for UI access
                self.last_metrics = {
                    'tokens': tokens,
                    'latency': latency,
                    'cost': cost,
                    'timestamp': timestamp
                }
                
                return result, self.last_metrics
                
        except Exception as e:
            latency = time.time() - start_time
            print(f"❌ [{time.strftime('%H:%M:%S')}] LLM Error | Latency: {latency:.2f}s | Error: {e}")
            return None, {'error': str(e), 'latency': latency}

    def is_available(self) -> bool:
        return self.llm is not None
    
    def is_langsmith_enabled(self) -> bool:
        return self.langsmith_client is not None
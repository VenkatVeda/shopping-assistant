# services/azure_service.py
from langchain.chat_models import AzureChatOpenAI
from langchain_openai import AzureOpenAIEmbeddings
from langchain.prompts import PromptTemplate
from langchain.chains.llm import LLMChain
from config.settings import AZURE_CONFIG
from config.prompts import PREFERENCE_PROMPT, GENERAL_CONVERSATION_PROMPT

class AzureService:
    def __init__(self):
        self.llm = None
        self.embeddings = None
        self.preference_chain = None
        self.conversation_chain = None
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

            # Initialize chains
            self.preference_chain = LLMChain(llm=self.llm, prompt=PREFERENCE_PROMPT, verbose=True)
            self.conversation_chain = LLMChain(llm=self.llm, prompt=GENERAL_CONVERSATION_PROMPT)
            
        except Exception as e:
            print(f"Warning: Azure OpenAI not configured: {e}")

    def is_available(self) -> bool:
        return self.llm is not None
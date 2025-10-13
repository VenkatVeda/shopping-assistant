# models/state.py
from dataclasses import dataclass
from typing import List, Union, Dict, Any
from langchain_core.messages import HumanMessage, AIMessage

@dataclass
class BotState(dict):
    chat_history: List[Union[HumanMessage, AIMessage]]
    question: str
    answer: str
    should_retrieve: bool
    metrics: Dict[str, Any] = None
"""
Enhanced Memory Manager for Shopping Assistant
Implements smart memory management with automatic summarization
"""

import sys
from typing import List, Dict, Optional
from databricks_langchain import ChatDatabricks
from .prompt_loader import load_prompt
from dotenv import load_dotenv
import os

load_dotenv()

class MemoryManager:
    """
    Manages short-term memory with automatic summarization
    
    Limits:
    - Max 20 turns per session
    - Max 32-64 KB per session
    - When exceeded: Summarize older context, keep last 3-5 raw turns
    """
    
    MAX_TURNS = 20
    MAX_BYTES = 48 * 1024  # 48 KB (middle of 32-64 KB range)
    KEEP_RAW_TURNS = 5  # Keep last 5 turns raw
    
    def __init__(self, chat_endpoint: str = None):
        """Initialize memory manager with Databricks ChatDatabricks"""
        # Use provided endpoint or get from environment
        endpoint = chat_endpoint or os.getenv("DATABRICKS_CHAT_ENDPOINT", "databricks-meta-llama-3-1-8b-instruct")
        
        self.llm = ChatDatabricks(
            endpoint=endpoint,
            temperature=0.3,
            max_tokens=500
        )

    
    def get_memory_size(self, history: List[str]) -> int:
        """Calculate memory size in bytes"""
        return sys.getsizeof(str(history))
    
    def should_summarize(self, history: List[str]) -> bool:
        """Check if memory should be summarized"""
        turn_count = len(history)
        memory_size = self.get_memory_size(history)
        
        return turn_count > self.MAX_TURNS or memory_size > self.MAX_BYTES
    
    def summarize_history(self, history: List[str]) -> tuple[str, List[str]]:
        """
        Summarize older context and keep recent turns
        
        Returns:
            tuple: (summary_text, recent_raw_turns)
        """
        if len(history) <= self.KEEP_RAW_TURNS:
            return "", history
        
        # Split history
        older_turns = history[:-self.KEEP_RAW_TURNS]
        recent_turns = history[-self.KEEP_RAW_TURNS:]
        
        # Create summary prompt using template
        context = "\n".join(older_turns)
        prompt = load_prompt("memory_summarization", {"context": context})
        
        try:
            # Use ChatDatabricks to summarize
            response = self.llm.invoke(prompt)
            summary = response.content.strip() if hasattr(response, 'content') else str(response).strip()
            
            return summary, recent_turns
        except Exception as e:
            print(f"Error summarizing history: {e}")
            # Fallback: just keep recent turns
            return "", recent_turns
    
    def manage_memory(self, history: List[str], summary: Optional[str] = None) -> Dict:
        """
        Main memory management function
        
        Args:
            history: List of conversation turns
            summary: Existing summary (if any)
        
        Returns:
            dict: {
                "history": managed history,
                "summary": updated summary,
                "was_summarized": bool
            }
        """
        if not self.should_summarize(history):
            return {
                "history": history,
                "summary": summary or "",
                "was_summarized": False
            }
        
        # Summarize
        new_summary, recent_turns = self.summarize_history(history)
        
        # Combine with existing summary if present
        if summary:
            combined_summary = f"{summary}\n\nRecent updates:\n{new_summary}"
        else:
            combined_summary = new_summary
        
        return {
            "history": recent_turns,
            "summary": combined_summary,
            "was_summarized": True
        }
    
    def format_context_for_llm(self, history: List[str], summary: Optional[str] = None) -> str:
        """
        Format memory for LLM consumption
        
        Returns formatted string with summary + recent history
        """
        parts = []
        
        if summary:
            parts.append(f"Previous Context Summary:\n{summary}\n")
        
        if history:
            parts.append(f"Recent Conversation:\n" + "\n".join(history))
        
        return "\n".join(parts)
    
    def get_memory_stats(self, history: List[str], summary: Optional[str] = None) -> Dict:
        """Get memory statistics"""
        return {
            "turn_count": len(history),
            "memory_bytes": self.get_memory_size(history),
            "summary_length": len(summary) if summary else 0,
            "needs_summarization": self.should_summarize(history),
            "max_turns": self.MAX_TURNS,
            "max_bytes": self.MAX_BYTES
        }


# Example usage
if __name__ == "__main__":
    manager = MemoryManager()
    
    # Simulate conversation
    history = [
        "User: I'm looking for leather bags",
        "Assistant: I found 10 leather bags",
        "User: Show me crossbody style",
        "Assistant: Here are 5 crossbody leather bags",
        "User: Under $100",
        "Assistant: Filtered to 3 bags under $100",
    ]
    
    # Check stats
    stats = manager.get_memory_stats(history)
    print("Memory Stats:", stats)
    
    # Manage memory
    result = manager.manage_memory(history)
    print("\nManaged Memory:")
    print(f"Was summarized: {result['was_summarized']}")
    print(f"Summary: {result['summary']}")
    print(f"Recent turns: {len(result['history'])}")

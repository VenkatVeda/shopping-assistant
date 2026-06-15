"""
Prompt Loader Utility for Shopping Assistant
Loads prompts from the prompts/ directory with template variable substitution
"""

import os
from typing import Dict, Any, Optional
from pathlib import Path

# Cache for loaded prompts
_prompt_cache: Dict[str, str] = {}

# Base directory for prompts
PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(prompt_name: str, variables: Optional[Dict[str, Any]] = None) -> str:
    """
    Load a prompt from the prompts directory
    
    Args:
        prompt_name: Name of the prompt file (without .txt extension)
        variables: Optional dictionary of variables to substitute in the prompt
        
    Returns:
        The loaded prompt with variables substituted
        
    Example:
        prompt = load_prompt("intent_classification")
        prompt = load_prompt("response_generation", {
            "query": "black leather tote",
            "preferences_text": "Categories: tote bags",
            "products_text": "Product 1: ..."
        })
    """
    # Load from cache or file
    if prompt_name not in _prompt_cache:
        prompt_path = PROMPTS_DIR / f"{prompt_name}.txt"
        
        if not prompt_path.exists():
            raise FileNotFoundError(
                f"Prompt file not found: {prompt_path}. "
                f"Available prompts: {list_available_prompts()}"
            )
        
        with open(prompt_path, 'r', encoding='utf-8') as f:
            _prompt_cache[prompt_name] = f.read()
    
    prompt = _prompt_cache[prompt_name]
    
    # Substitute variables if provided
    if variables:
        try:
            prompt = prompt.format(**variables)
        except KeyError as e:
            print(f"Warning: Missing variable in prompt template: {e}")
            # Return prompt with unsubstituted variables
    
    return prompt


def list_available_prompts() -> list:
    """List all available prompt files"""
    if not PROMPTS_DIR.exists():
        return []
    
    return [
        p.stem for p in PROMPTS_DIR.glob("*.txt")
        if p.stem != "README"
    ]


def reload_prompts():
    """Clear the prompt cache to force reload from files"""
    global _prompt_cache
    _prompt_cache.clear()
    print(f"Prompt cache cleared. {len(list_available_prompts())} prompts available.")


def get_prompt_info() -> Dict[str, Dict[str, Any]]:
    """Get information about all available prompts"""
    prompts_info = {}
    
    for prompt_name in list_available_prompts():
        prompt_path = PROMPTS_DIR / f"{prompt_name}.txt"
        
        with open(prompt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        prompts_info[prompt_name] = {
            "path": str(prompt_path),
            "size_bytes": len(content.encode('utf-8')),
            "line_count": len(content.splitlines()),
            "char_count": len(content)
        }
    
    return prompts_info


# Example usage and testing
if __name__ == "__main__":
    print("Prompt Loader Utility")
    print("=" * 50)
    
    # List available prompts
    prompts = list_available_prompts()
    print(f"\nAvailable prompts ({len(prompts)}):")
    for prompt in prompts:
        print(f"  - {prompt}")
    
    # Get prompt info
    print("\nPrompt Information:")
    for name, info in get_prompt_info().items():
        print(f"\n{name}:")
        print(f"  Lines: {info['line_count']}")
        print(f"  Characters: {info['char_count']}")
        print(f"  Size: {info['size_bytes']} bytes")
    
    # Test loading a prompt
    print("\n" + "=" * 50)
    print("Testing prompt loading...")
    
    try:
        intent_prompt = load_prompt("intent_classification")
        print(f"\n✓ Loaded 'intent_classification' prompt ({len(intent_prompt)} chars)")
        print(f"  First 100 chars: {intent_prompt[:100]}...")
    except Exception as e:
        print(f"\n✗ Error loading prompt: {e}")
    
    # Test with variables
    try:
        response_prompt = load_prompt("response_generation", {
            "query": "black leather tote",
            "preferences_text": "Categories: tote bags\nColors: black\nMaterials: leather",
            "context_text": "This is a new conversation.",
            "products_text": "Product 1: Sample Tote - $99"
        })
        print(f"\n✓ Loaded 'response_generation' with variables ({len(response_prompt)} chars)")
    except Exception as e:
        print(f"\n✗ Error loading prompt with variables: {e}")

"""
Minimal Test Runner - Testing LangSmith Integration with Reduced Dependencies
"""

import os
import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

print("🔍 Starting minimal test with LangSmith integration...")

try:
    # Test basic imports
    from langsmith import Client
    print("✅ LangSmith import successful")
    
    # Test environment variables
    from config.settings import LANGSMITH_CONFIG
    print(f"✅ LangSmith config loaded: {LANGSMITH_CONFIG}")
    
    # Test LangSmith client initialization
    if LANGSMITH_CONFIG.get('tracing', False):
        client = Client(
            api_key=LANGSMITH_CONFIG['api_key'],
            api_url=LANGSMITH_CONFIG.get('endpoint', 'https://api.smith.langchain.com')
        )
        print(f"✅ LangSmith client initialized for project: {LANGSMITH_CONFIG['project']}")
    else:
        print("⚠️ LangSmith tracing disabled in configuration")
        
    print("🎉 Minimal test completed successfully!")
    
except Exception as e:
    print(f"❌ Error in minimal test: {str(e)}")
    import traceback
    traceback.print_exc()
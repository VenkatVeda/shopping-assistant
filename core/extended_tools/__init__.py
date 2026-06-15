"""
Extended Tools - Unified Interface
Automatically chooses between HTTP client (local) and direct import (Databricks Apps)
"""

import os
from typing import Optional, Protocol


class ExtendedToolsProtocol(Protocol):
    """Common interface for extended tools"""
    
    def get_stock_info(self, symbol: str) -> str:
        """Get stock market information"""
        ...
    
    def run_databricks_query(self, sql_query: str) -> dict:
        """Execute SQL query on Databricks"""
        ...
    
    def health_check(self) -> bool:
        """Check if tools are available"""
        ...


def is_databricks_app() -> bool:
    """
    Detect if running in Databricks Apps environment
    
    Returns:
        True if running in Databricks Apps, False otherwise
    """
    return (
        os.getenv('DATABRICKS_RUNTIME_VERSION') is not None or
        os.getenv('DB_IS_DRIVER') is not None or
        os.getenv('DATABRICKS_HOST') is not None or
        '/databricks/' in os.getcwd().lower() or
        '/app/python/' in os.getcwd().lower()  # Databricks Apps working dir
    )


def get_extended_tools() -> Optional[ExtendedToolsProtocol]:
    """
    Get extended tools instance based on environment
    
    This function automatically determines whether to use:
    - HTTP client (local development with separate MCP server)
    - Direct imports (Databricks Apps to avoid OAuth consent issues)
    
    Returns:
        ExtendedTools instance or None if disabled
    """
    # Check if enabled via environment variable
    enabled = os.getenv("ENABLE_EXTENDED_MCP_TOOLS", "false").lower() == "true"
    
    if not enabled:
        print("[EXTENDED TOOLS] Disabled via ENABLE_EXTENDED_MCP_TOOLS=false")
        return None
    
    # Detect environment
    in_databricks = is_databricks_app()
    
    if in_databricks:
        # Databricks Apps: Use direct imports to avoid OAuth consent redirect
        print("[EXTENDED TOOLS] Databricks Apps detected - using direct imports")
        try:
            from .direct_tools import DirectExtendedTools
            return DirectExtendedTools()
        except ImportError as e:
            print(f"[EXTENDED TOOLS] Failed to import direct tools: {e}")
            print("[EXTENDED TOOLS] Make sure mcp-server-custom-code is included in deployment")
            return None
    else:
        # Local development: Use HTTP client with separate MCP server
        print("[EXTENDED TOOLS] Local environment detected - using HTTP client")
        try:
            # Import from existing HTTP client
            import sys
            from pathlib import Path
            
            # Ensure parent directory is in path
            parent_dir = Path(__file__).parent.parent
            if str(parent_dir) not in sys.path:
                sys.path.insert(0, str(parent_dir))
            
            from mcp_client.extended_tools_client import ExtendedToolsClient
            
            server_url = os.getenv("MCP_SERVER_URL", "http://localhost:8000")
            client = ExtendedToolsClient(server_url)
            
            # Check if server is accessible
            if client.health_check():
                print(f"[EXTENDED TOOLS] Connected to MCP server: {server_url}")
                return client
            else:
                print(f"[EXTENDED TOOLS] MCP server not accessible at {server_url}")
                print("[EXTENDED TOOLS] Start server with: cd mcp-server-custom-code && python run_server.py")
                return None
                
        except ImportError as e:
            print(f"[EXTENDED TOOLS] Failed to import HTTP client: {e}")
            return None


# Singleton instance
_extended_tools_instance: Optional[ExtendedToolsProtocol] = None


def get_extended_tools_singleton() -> Optional[ExtendedToolsProtocol]:
    """
    Get singleton instance of extended tools
    
    Returns:
        Cached ExtendedTools instance or None if disabled
    """
    global _extended_tools_instance
    
    if _extended_tools_instance is None:
        _extended_tools_instance = get_extended_tools()
    
    return _extended_tools_instance

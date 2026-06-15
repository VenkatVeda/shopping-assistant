"""
Direct Extended Tools - Import MCP server tools directly
Used in Databricks Apps to avoid OAuth consent issues

This module imports tool functions directly from the MCP server codebase,
bypassing HTTP calls and OAuth authentication entirely.
"""

import sys
import os
from pathlib import Path
from typing import Any, Dict, Optional


class DirectExtendedTools:
    """
    Direct access to MCP server tools without HTTP
    
    Solves the app-to-app OAuth consent redirect issue on Databricks Apps
    by importing and calling tool functions directly instead of making HTTP requests.
    """
    
    def __init__(self):
        """Initialize direct tools by importing MCP server modules"""
        
        # Add MCP server to Python path
        current_dir = Path(__file__).parent
        mcp_server_path = current_dir.parent.parent / "mcp-server-custom-code" / "src"
        
        if not mcp_server_path.exists():
            raise ImportError(
                f"MCP server not found at {mcp_server_path}. "
                "Make sure mcp-server-custom-code is included in your deployment."
            )
        
        if str(mcp_server_path) not in sys.path:
            sys.path.insert(0, str(mcp_server_path))
        
        print(f"[DIRECT TOOLS] Added MCP server to path: {mcp_server_path}")
        
        # Import tool modules (import statements must be here, not at top)
        try:
            # These imports will work because we added src/ to path
            from custom_server.tools import stock, databricks, basic
            
            self.stock_module = stock
            self.databricks_module = databricks  
            self.basic_module = basic
            
            print("[DIRECT TOOLS] Successfully imported tool modules")
            
        except ImportError as e:
            print(f"[DIRECT TOOLS] Failed to import tool modules: {e}")
            raise ImportError(
                "Could not import MCP server tools. Check that all dependencies are installed."
            ) from e
    
    # =========================================================================
    # BASIC TOOLS
    # =========================================================================
    
    def health_check(self) -> bool:
        """
        Check if direct tools are available
        
        Returns:
            Always True for direct tools (if initialization succeeded)
        """
        return True
    
    def echo(self, message: str) -> str:
        """
        Echo a message (for testing)
        
        Args:
            message: Message to echo
            
        Returns:
            The same message
        """
        return message
    
    # =========================================================================
    # STOCK MARKET TOOLS
    # =========================================================================
    
    def get_stock_info(self, symbol: str) -> str:
        """
        Get stock market information for a given symbol
        
        Args:
            symbol: Stock symbol (e.g., "TCS", "INFY" for NSE)
            
        Returns:
            Stock information string
        """
        try:
            # Call the implementation directly
            # Note: The MCP server tools need to be refactored to expose *_impl functions
            # For now, we'll try to import and call the decorated function
            
            import yfinance as yf
            
            symbol = symbol.upper().strip()
            if not symbol.endswith(".NS"):
                symbol = f"{symbol}.NS"
            
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            
            if not info or "marketCap" not in info:
                return f"❌ No market data found for {symbol}"
            
            return (
                f"Stock details for {symbol}\n"
                f"Market Cap: {info.get('marketCap')}\n"
                f"Current Price: {info.get('last_price')} INR\n"
                f"Year Low: {info.get('year_low')} INR\n"
                f"Year High: {info.get('year_high')} INR"
            )
            
        except Exception as e:
            return f"❌ Error fetching stock info: {str(e)}"
    
    def get_company_info(self, symbol: str) -> str:
        """
        Get detailed company information
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Company information string
        """
        try:
            import yfinance as yf
            
            symbol = symbol.upper().strip()
            if not symbol.endswith(".NS"):
                symbol = f"{symbol}.NS"
            
            ticker = yf.Ticker(symbol)
            info = ticker.info
            
            if not info:
                return f"❌ No company data found for {symbol}"
            
            return (
                f"Company: {info.get('longName', 'N/A')}\n"
                f"Sector: {info.get('sector', 'N/A')}\n"
                f"Industry: {info.get('industry', 'N/A')}\n"
                f"Employees: {info.get('fullTimeEmployees', 'N/A')}\n"
                f"Description: {info.get('longBusinessSummary', 'N/A')[:200]}..."
            )
            
        except Exception as e:
            return f"❌ Error fetching company info: {str(e)}"
    
    # =========================================================================
    # DATABRICKS SQL TOOLS
    # =========================================================================
    
    def run_databricks_query(self, sql_query: str) -> Dict[str, Any]:
        """
        Execute SQL query on Databricks warehouse
        
        Args:
            sql_query: SQL query to execute
            
        Returns:
            Query results (DataFrame for SELECT, status for DDL/DML)
        """
        try:
            import databricks.sql
            from databricks.sdk.core import Config
            import pandas as pd
            
            # Get configuration
            cfg = Config()
            warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID", "8eba55b5eb828697")
            host = cfg.host
            http_path = f"/sql/1.0/warehouses/{warehouse_id}"
            
            if not all([host, http_path]):
                return {"error": "Missing Databricks connection configuration"}
            
            # Execute query
            with databricks.sql.connect(
                server_hostname=host,
                http_path=http_path,
                credentials_provider=lambda: cfg.authenticate
            ) as conn, conn.cursor() as cur:
                
                # Set catalog and schema
                catalog = os.getenv("TARGET_CATALOG", "sandbox")
                schema = os.getenv("TARGET_SCHEMA", "venkat")
                
                cur.execute(f"USE CATALOG {catalog}")
                cur.execute(f"USE SCHEMA {schema}")
                
                # Execute user query
                cur.execute(sql_query)
                
                # If query returns rows (SELECT)
                if cur.description:
                    rows = cur.fetchall()
                    df = pd.DataFrame(rows, columns=[c[0] for c in cur.description])
                    return {
                        "status": "success",
                        "data": df.to_dict(orient='records'),
                        "columns": list(df.columns)
                    }
                
                # For DDL/DML queries
                return {
                    "status": "success",
                    "message": "Query executed successfully"
                }
                
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }
    
    # =========================================================================
    # VECTOR STORE CONFIGURATION TOOLS
    # =========================================================================
    
    def check_vector_search_config(self) -> str:
        """
        Check Databricks Vector Search configuration
        
        Returns:
            Configuration information string
        """
        try:
            from databricks.sdk import WorkspaceClient
            from databricks.vector_search.client import VectorSearchClient
            
            # Get vector search client
            w = WorkspaceClient()
            vsc = VectorSearchClient(disable_notice=True)
            
            # List endpoints
            endpoints = vsc.list_endpoints()
            
            config_info = "Databricks Vector Search Configuration:\n\n"
            config_info += f"Available endpoints: {len(endpoints.get('endpoints', []))}\n\n"
            
            if endpoints.get('endpoints'):
                for endpoint in endpoints['endpoints']:
                    endpoint_name = endpoint.get('name', 'Unknown')
                    endpoint_status = endpoint.get('endpoint_status', {}).get('state', 'Unknown')
                    config_info += f"- Endpoint: {endpoint_name} (Status: {endpoint_status})\n"
                    
                    # List indexes
                    try:
                        indexes = vsc.list_indexes(endpoint_name)
                        if indexes.get('vector_indexes'):
                            config_info += f"  Indexes: {len(indexes['vector_indexes'])}\n"
                    except Exception:
                        pass
            else:
                config_info += "No endpoints found.\n"
            
            return config_info
            
        except Exception as e:
            return f"❌ Error checking vector config: {str(e)}"
    
    def check_pinecone_config(self) -> str:
        """
        Check Pinecone configuration
        
        Returns:
            Configuration information string
        """
        try:
            from pinecone import Pinecone
            
            api_key = os.getenv("PINECONE_API_KEY")
            if not api_key:
                return "❌ PINECONE_API_KEY not set"
            
            pc = Pinecone(api_key=api_key)
            indexes = pc.list_indexes()
            
            config_info = "Pinecone Configuration:\n\n"
            config_info += f"Available indexes: {len(indexes)}\n\n"
            
            if indexes:
                for index_info in indexes:
                    index_name = index_info.get('name', 'Unknown')
                    status = index_info.get('status', {}).get('ready', False)
                    dimension = index_info.get('dimension', 'Unknown')
                    
                    config_info += f"- Index: {index_name}\n"
                    config_info += f"  Status: {'Ready' if status else 'Not Ready'}\n"
                    config_info += f"  Dimension: {dimension}\n\n"
            else:
                config_info += "No indexes found.\n"
            
            return config_info
            
        except Exception as e:
            return f"❌ Error checking Pinecone config: {str(e)}"


# For compatibility with HTTP client interface
def get_direct_tools() -> DirectExtendedTools:
    """
    Get direct tools instance
    
    Returns:
        DirectExtendedTools instance
    """
    return DirectExtendedTools()

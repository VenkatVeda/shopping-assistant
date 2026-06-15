"""
Check Databricks Table Schema
Retrieves column names from the bags_embeddings table
"""

import os
from databricks import sql

# Load environment
host = os.getenv("DATABRICKS_HOST")
token = os.getenv("DATABRICKS_TOKEN")
warehouse_id = "8eba55b5eb828697"  # From your databricks.yml
table_name = "sandbox.venkat.bags_embeddings"

print(f"Connecting to {host}")
print(f"Table: {table_name}\n")

try:
    # Connect to Databricks SQL
    with sql.connect(
        server_hostname=host.replace("https://", "").rstrip("/"),
        http_path=f"/sql/1.0/warehouses/{warehouse_id}",
        access_token=token
    ) as connection:
        
        with connection.cursor() as cursor:
            # Get table schema
            print("Fetching table schema...")
            cursor.execute(f"DESCRIBE {table_name}")
            
            columns = cursor.fetchall()
            
            print("\n✅ Table Schema:")
            print("-" * 60)
            for col in columns:
                col_name = col[0]
                col_type = col[1]
                print(f"  {col_name:30} {col_type}")
            print("-" * 60)
            
            # Get sample data
            print("\n\nFetching sample row...")
            cursor.execute(f"SELECT * FROM {table_name} LIMIT 1")
            
            row = cursor.fetchone()
            if row:
                print("\n✅ Sample Row:")
                print("-" * 60)
                for i, col in enumerate(columns):
                    col_name = col[0]
                    value = row[i]
                    
                    # Truncate long values
                    if isinstance(value, str) and len(value) > 100:
                        value = value[:100] + "..."
                    elif isinstance(value, list) and len(value) > 10:
                        value = f"[array of {len(value)} elements]"
                    
                    print(f"  {col_name}: {value}")
                print("-" * 60)
            
            print("\n\n📋 Available columns for vector search:")
            non_embedding_cols = [col[0] for col in columns if col[0] != 'embedding']
            for col in non_embedding_cols:
                print(f"  • {col}")
                
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()

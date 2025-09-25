import os
from pathlib import Path
import pandas as pd
from openai import AzureOpenAI
from chromadb import PersistentClient
import time

CHROMA_DIR = "chroma_db_numeric"
COLLECTION_NAME = "bags"

# Avoid reprocessing
if Path(f"{CHROMA_DIR}/index").exists():
    print("✅ ChromaDB already exists. Skipping generation.")
    exit(0)

# === Load Environment ===
from dotenv import load_dotenv
load_dotenv()

client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)
EMBED_MODEL = os.getenv("AZURE_OPENAI_EMBED_MODEL")

# === Read Excel ===
df = pd.read_excel("bags.xlsx")
total_rows = len(df)
print(f"📊 Total products to process: {total_rows}")

# === Prepare ChromaDB ===
dbclient = PersistentClient(path=CHROMA_DIR)
collection = dbclient.get_or_create_collection(COLLECTION_NAME)

# === Embed & Store ===
def embed_text(text):
    try:
        response = client.embeddings.create(input=[text], model=EMBED_MODEL)
        return response.data[0].embedding
    except Exception as e:
        print(f"❌ Error embedding text: {e}")
        raise

processed_count = 0
for idx, row in df.iterrows():
    try:
        print(f"🔍 Processing row {idx + 1}/{total_rows}: {row['Product Name'][:50]}...")
        product_id = str(row['Product ID'])
        
        # Check if already exists
        try:
            existing = collection.get(ids=[f"prod_{product_id}"])
            if existing['ids']:
                print(f"⏭️  Product {product_id} already exists, skipping...")
                continue
        except:
            pass  # Product doesn't exist, continue processing
            
        brand = row['Brand']
        name = row['Product Name']
        price = row['Price']
        short_desc = row['description']
        full_desc = f"{name} by {brand}. Price: {price}. {short_desc}"

        embedding = embed_text(full_desc)

        collection.add(
            documents=[full_desc],
            embeddings=[embedding],
            metadatas=[{
                "product_id": product_id,
                "brand": brand,
                "price": price,
                "name": name,
                "url": row["Product Page URL"]
            }],
            ids=[f"prod_{product_id}"]
        )
        
        processed_count += 1
        print(f"✅ Added product {product_id}")
        
    except Exception as e:
        print(f"❌ Error processing product {idx}: {e}")
        continue

print("✅ All products embedded successfully.")

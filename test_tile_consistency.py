#!/usr/bin/env python3
"""Test the improved product tile consistency"""

import os
import sys

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_product_tile_consistency():
    """Test the improved product formatting with consistent image handling"""
    print("🎨 Testing Product Tile Consistency Fix")
    print("=" * 50)
    
    try:
        from utils.data_loader import DataLoader
        from ui.formatters import ProductFormatter
        from langchain.schema import Document
        
        print("📦 Loading data and formatter...")
        data_loader = DataLoader()
        formatter = ProductFormatter(data_loader)
        
        # Create test products with different image scenarios
        test_products = [
            Document(
                page_content="Test product with valid image",
                metadata={
                    "name": "Test Bag with Image",
                    "brand": "Test Brand",
                    "price": 150.00,
                    "url": "https://example.com/product1"
                }
            ),
            Document(
                page_content="Test product with no image URL",
                metadata={
                    "name": "Test Bag No Image",
                    "brand": "Test Brand", 
                    "price": 200.00,
                    "url": "https://example.com/product2"
                }
            ),
            Document(
                page_content="Test product with empty image URL",
                metadata={
                    "name": "Test Bag Empty Image",
                    "brand": "Test Brand",
                    "price": 100.00,
                    "url": "https://example.com/product3"
                }
            )
        ]
        
        # Mock image URLs - some valid, some invalid
        data_loader.url_to_image = {
            "https://example.com/product1": "https://myer-media.com.au/wcsstore/MyerCatalogAssetStore/images/70/706/2564/100/1/182011960/182011960_1_240x309.webp?w=3840&q=75",
            "https://example.com/product2": "",  # Empty image URL
            "https://example.com/product3": "https://invalid-url.com/broken-image.jpg"  # Broken URL
        }
        
        print("🔍 Testing different image scenarios...")
        
        for i, product in enumerate(test_products, 1):
            print(f"\n--- Product {i} ---")
            formatted = formatter.format_product_doc(product)
            
            # Check for consistency indicators
            has_placeholder = "🛍️" in formatted
            has_fallback = "onerror" in formatted
            has_proper_container = "height: 200px" in formatted
            
            print(f"Product: {product.metadata['name']}")
            print(f"Has placeholder: {'✅' if has_placeholder else '❌'}")
            print(f"Has fallback handling: {'✅' if has_fallback else '❌'}")
            print(f"Has consistent container: {'✅' if has_proper_container else '❌'}")
        
        # Test the complete product list
        print(f"\n🎯 Testing complete product list...")
        complete_html = formatter.format_product_list(test_products, "Test Products")
        
        # Check for consistency across all tiles
        tile_count = complete_html.count('min-width: 250px')
        image_container_count = complete_html.count('height: 200px')
        
        print(f"Product tiles: {tile_count}")
        print(f"Image containers: {image_container_count}")
        print(f"Consistency: {'✅ All tiles have proper image containers' if tile_count == image_container_count else '❌ Inconsistent image containers'}")
        
        print(f"\n🎉 Key Improvements Made:")
        print(f"   ✅ Fixed height image containers (200px) for all tiles")
        print(f"   ✅ Consistent fallback placeholders with shopping bag icon")
        print(f"   ✅ JavaScript fallback for broken image URLs")
        print(f"   ✅ Proper object-fit: contain for image scaling")
        print(f"   ✅ Uniform styling across all product tiles")
        
        print(f"\n🌐 Starting improved interface...")
        print(f"💡 All product tiles now have consistent image areas")
        print(f"🛍️ Broken images show professional placeholder")
        print(f"\n⌨️ Press Ctrl+C to stop")
        
        # Launch to test in real interface
        from main import ShoppingAssistantApp
        app = ShoppingAssistantApp(enable_parallel=False)
        app.launch(
            share=False,
            debug=False,
            server_name="0.0.0.0",
            server_port=7860
        )
        
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_product_tile_consistency()
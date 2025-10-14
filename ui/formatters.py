from langchain.schema import Document
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from utils.data_loader import DataLoader

class ProductFormatter:
    """Formats product documents for display in the UI"""
    
    def __init__(self, data_loader):
        self.data_loader = data_loader
    
    def format_product_doc(self, doc: Document) -> str:
        """Format a single product document as HTML"""
        meta = doc.metadata
        product_url = meta.get("url", "")
        image_url = self.data_loader.url_to_image.get(product_url, "")
        
        # Format price properly
        price = meta.get('price', 'N/A')
        if isinstance(price, (int, float)):
            price_display = f"${price:.2f}"
        else:
            price_display = f"${price}" if price != 'N/A' else 'N/A'
        
        brand = meta.get('brand', 'N/A')
        product_name = self._format_product_name(meta.get('name', 'Product'))
        
        return f"""
        <div style="
            flex: 1;
            min-width: 250px;
            max-width: 300px;
            min-height: 400px;
            margin: 10px;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 10px;
            box-shadow: 2px 2px 5px rgba(0,0,0,0.1);
            text-align: center;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        ">
            <div style="flex-grow: 1;">
                <h4 style="margin: 0 0 10px 0; font-size: 1em; height: 2.4em; line-height: 1.2em; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; text-overflow: ellipsis;">{product_name}</h4>
                <p style="margin: 0 0 10px 0;">
                    <strong>Brand:</strong> {brand}<br>
                    <strong>Price:</strong> {price_display}
                </p>
                {self._render_product_image(image_url, product_name)}
            </div>
            <div style="margin-top: 10px;">
                👉 <a href="{product_url}" target="_blank" rel="noopener noreferrer">View Product</a>
            </div>
        </div>
        """
    
    def format_product_list(self, docs: list, title: str = "") -> str:
        """Format a list of products with optional title"""
        if not docs:
            return "<p>No products found matching your criteria.</p>"
        
        product_displays = [self.format_product_doc(doc) for doc in docs]
        
        title_html = f"<h3>{title}</h3>" if title else ""
        
        return f"""
        {title_html}
        <div style="display: flex; flex-wrap: wrap; gap: 20px; justify-content: center;">
            {''.join(product_displays)}
        </div>
        """
    
    def _render_product_image(self, image_url: str, product_name: str) -> str:
        """Render product image with consistent fallback handling"""
        if not image_url or image_url.strip() == "":
            # No image URL - show consistent placeholder
            return self._create_image_placeholder(product_name)
        
        # Create image with fallback handling
        return f"""
        <div style="width: 100%; height: 200px; display: flex; align-items: center; justify-content: center; margin: 10px 0; background-color: #f8f9fa; border: 1px solid #e9ecef; border-radius: 5px; overflow: hidden;">
            <img 
                src="{image_url}" 
                style="max-width: 100%; max-height: 100%; object-fit: contain;" 
                alt="{product_name}"
                onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';"
            >
            <div style="display: none; flex-direction: column; align-items: center; justify-content: center; height: 100%; color: #6c757d; text-align: center; padding: 20px;">
                <div style="font-size: 3em; margin-bottom: 10px;">🛍️</div>
                <div style="font-size: 0.9em;">Image not available</div>
            </div>
        </div>
        """
    
    def _create_image_placeholder(self, product_name: str) -> str:
        """Create a consistent placeholder for missing images"""
        return f"""
        <div style="width: 100%; height: 200px; display: flex; flex-direction: column; align-items: center; justify-content: center; margin: 10px 0; background-color: #f8f9fa; border: 1px solid #e9ecef; border-radius: 5px; color: #6c757d; text-align: center;">
            <div style="font-size: 3em; margin-bottom: 10px;">🛍️</div>
            <div style="font-size: 0.9em; padding: 0 20px;">No image available</div>
        </div>
        """
    
    def _format_product_name(self, product_name: str) -> str:
        """Format product name with consistent length handling"""
        # Escape HTML special characters
        import html
        product_name = html.escape(product_name)
        
        # If name is very long, provide a reasonable truncation as backup
        # The CSS will handle the display, but this ensures reasonable lengths
        if len(product_name) > 80:
            product_name = product_name[:77] + "..."
            
        return product_name
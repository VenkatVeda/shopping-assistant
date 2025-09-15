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
        product_name = meta.get('name', 'Product')
        
        return f"""
        <div style="
            flex: 1;
            min-width: 250px;
            max-width: 300px;
            margin: 10px;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 10px;
            box-shadow: 2px 2px 5px rgba(0,0,0,0.1);
            text-align: center;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            height: 100%;
        ">
            <div style="flex-grow: 1;">
                <h4 style="margin: 0 0 10px 0; font-size: 1em;">{product_name}</h4>
                <p style="margin: 0 0 10px 0;">
                    <strong>Brand:</strong> {brand}<br>
                    <strong>Price:</strong> {price_display}
                </p>
                {f'<img src="{image_url}" style="max-width: 100%; height: auto; margin: 10px auto;" alt="{product_name}">' if image_url else ''}
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
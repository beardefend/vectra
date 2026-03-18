# utils.py
# Utility functions for document building and freshness checking.

from datetime import datetime, timedelta

def build_document_string(product: dict) -> str:
    """
    Constructs the ChromaDB document string by combining product fields.
    Format: {product_name} by {brand}. Category: {category}, {subcategory}. Specs: {specs}. Description: {description}. Best for: {use_case}. Price: {price} {currency}
    """
    return (
        f"{product['product_name']} by {product['brand']}. "
        f"Category: {product['category']}, {product['subcategory']}. "
        f"Specs: {product['specs']}. "
        f"Description: {product['description']}. "
        f"Best for: {product['use_case']}. "
        f"Price: {product['price']} {product['currency']}"
    )

def check_freshness(last_updated: str, threshold_days: int) -> bool:
    """
    Returns True if the date is older than threshold_days from today.
    Uses YYYY-MM-DD format.
    """
    try:
        product_date = datetime.strptime(last_updated, "%Y-%m-%d").date()
        today = datetime.now().date()
        days_diff = (today - product_date).days
        return days_diff > threshold_days
    except ValueError:
        # If date format is invalid, consider it stale
        return True

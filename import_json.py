#!/usr/bin/env python3
"""
Import JSON product data into the ChromaDB middleware application.
This script imports products from a JSON file into both SQLite and ChromaDB
for a specific client.

Usage:
    python import_json.py --client-id CLIENT_ID --auth-code AUTH_CODE --json-file FILE.json

The JSON file should be in the format produced by convert_utils.py:
[
    {
        "product_id": "PROD001",
        "product_name": "Product Name",
        "category": "Category",
        "subcategory": "Subcategory",
        "brand": "Brand",
        "price": 99.99,
        "currency": "USD",
        "specs": "Specifications",
        "description": "Description",
        "use_case": "use case",
        "product_url": "https://example.com/product",
        "last_updated": "2024-01-15"
    },
    ...
]
"""

import json
import sqlite3
import argparse
import sys
from datetime import datetime

# Import the application modules
import config
import chromadb
import utils


def import_products(client_id: str, auth_code: str, json_file: str, quiet: bool = False) -> int:
    """
    Import products from JSON file into SQLite and ChromaDB for a specific client.
    
    Args:
        client_id: The client ID
        auth_code: The client's authentication code
        json_file: Path to JSON file containing products
        quiet: If True, suppress progress output
    
    Returns:
        Number of products imported
    """
    if not quiet:
        print(f"Importing products from {json_file} for client {client_id}...")
    
    # Load products from JSON
    with open(json_file, 'r', encoding='utf-8') as f:
        products = json.load(f)
    
    if not isinstance(products, list):
        raise ValueError("JSON file must contain a list of products")
    
    # Connect to SQLite
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    cursor = conn.cursor()
    
    # Verify client exists and get table/collection names
    cursor.execute(
        "SELECT table_name, collection_name FROM clients WHERE client_id = ? AND auth_code = ?",
        (client_id, auth_code)
    )
    result = cursor.fetchone()
    
    if not result:
        raise ValueError(f"Client {client_id} not found or invalid authentication")
    
    table_name, collection_name = result
    
    # Connect to ChromaDB
    chroma_client = chromadb.PersistentClient(path=config.CHROMADB_PATH)
    collection = chroma_client.get_or_create_collection(name=collection_name)
    
    imported_count = 0
    skipped_count = 0
    
    for product_num, product in enumerate(products, start=1):
        try:
            # Validate product data
            errors = []
            
            # Check required fields
            required_fields = [
                'product_id', 'product_name', 'category', 'subcategory', 'brand',
                'price', 'currency', 'specs', 'description', 'use_case',
                'product_url', 'last_updated'
            ]
            
            for field in required_fields:
                if field not in product:
                    errors.append(f"Missing field: {field}")
            
            if errors:
                if not quiet:
                    print(f"Warning: Product {product_num} - {', '.join(errors)}")
                skipped_count += 1
                continue
            
            # Validate date format
            try:
                datetime.strptime(product['last_updated'], '%Y-%m-%d')
            except ValueError:
                if not quiet:
                    print(f"Warning: Product {product_num} - Invalid date format: {product['last_updated']}")
                skipped_count += 1
                continue
            
            # Check if product already exists
            cursor.execute(
                f"SELECT product_id FROM {table_name} WHERE product_id = ?",
                (product['product_id'],)
            )
            if cursor.fetchone():
                if not quiet:
                    print(f"Warning: Product {product_num} - Product ID {product['product_id']} already exists, skipping")
                skipped_count += 1
                continue
            
            # Insert into SQLite
            cursor.execute(f"""
                INSERT INTO {table_name}
                (product_id, product_name, category, subcategory, brand, price, currency,
                 specs, description, use_case, product_url, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                product['product_id'],
                product['product_name'],
                product['category'],
                product['subcategory'],
                product['brand'],
                float(product['price']),
                product['currency'],
                product['specs'],
                product['description'],
                product['use_case'],
                product['product_url'],
                product['last_updated']
            ))
            
            # Insert into ChromaDB
            document_string = utils.build_document_string(product)
            
            collection.add(
                ids=[product['product_id']],
                documents=[document_string],
                metadatas=[{
                    'product_id': product['product_id'],
                    'category': product['category'],
                    'subcategory': product['subcategory'],
                    'brand': product['brand'],
                    'price': float(product['price']),
                    'currency': product['currency'],
                    'use_case': product['use_case'],
                    'product_url': product['product_url'],
                    'last_updated': product['last_updated']
                }]
            )
            
            imported_count += 1
            
            if not quiet and imported_count % 20 == 0:
                print(f"  Imported {imported_count} products...")
        
        except Exception as e:
            if not quiet:
                print(f"Error importing product {product_num}: {e}")
            skipped_count += 1
            continue
    
    conn.commit()
    conn.close()
    
    if not quiet:
        print(f"\nImport complete!")
        print(f"  Successfully imported: {imported_count}")
        print(f"  Skipped: {skipped_count}")
    
    return imported_count


def main():
    parser = argparse.ArgumentParser(
        description='Import JSON product data into the ChromaDB middleware application',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python import_json.py --client-id myclient --auth-code secret123 --json-file products.json
  python import_json.py -c myclient -a secret123 -f products.json -q

The JSON file should contain a list of product objects with all required fields.
        '''
    )
    
    parser.add_argument('--client-id', '-c', required=True,
                       help='Client ID for the target client')
    parser.add_argument('--auth-code', '-a', required=True,
                       help='Authentication code for the client')
    parser.add_argument('--json-file', '-f', required=True,
                       help='Path to JSON file containing products')
    parser.add_argument('--quiet', '-q', action='store_true',
                       help='Suppress progress output')
    
    args = parser.parse_args()
    
    try:
        count = import_products(args.client_id, args.auth_code, args.json_file, args.quiet)
        if not args.quiet:
            print(f"\nSuccessfully imported {count} products for client {args.client_id}")
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

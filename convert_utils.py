#!/usr/bin/env python3
"""
CSV <-> JSON Converter for Product Data
This script converts product data between CSV and JSON formats
with the exact fields expected by the ChromaDB middleware application.

Database fields expected:
- product_id (TEXT PRIMARY KEY)
- product_name (TEXT NOT NULL)
- category (TEXT NOT NULL)
- subcategory (TEXT NOT NULL)
- brand (TEXT NOT NULL)
- price (REAL NOT NULL)
- currency (TEXT NOT NULL)
- specs (TEXT NOT NULL)
- description (TEXT NOT NULL)
- use_case (TEXT NOT NULL)
- product_url (TEXT NOT NULL)
- last_updated (TEXT NOT NULL) - Format: YYYY-MM-DD
"""

import csv
import json
import sys
import argparse
from typing import List, Dict, Any
from datetime import datetime


# Expected field names in order
EXPECTED_FIELDS = [
    'product_id',
    'product_name',
    'category',
    'subcategory',
    'brand',
    'price',
    'currency',
    'specs',
    'description',
    'use_case',
    'product_url',
    'last_updated'
]


def validate_product_data(product: Dict[str, Any]) -> List[str]:
    """
    Validate product data against expected fields and data types.
    Returns list of validation errors.
    """
    errors = []
    
    # Check required fields
    for field in EXPECTED_FIELDS:
        if field not in product:
            errors.append(f"Missing required field: {field}")
    
    if errors:
        return errors
    
    # Validate data types
    try:
        float(product['price'])
    except (ValueError, TypeError):
        errors.append(f"Invalid price value: {product['price']}")
    
    # Validate date format
    try:
        datetime.strptime(product['last_updated'], '%Y-%m-%d')
    except (ValueError, TypeError):
        errors.append(f"Invalid date format for last_updated: {product['last_updated']}. Expected YYYY-MM-DD")
    
    # Check for empty strings in required fields
    for field in ['product_id', 'product_name', 'category', 'subcategory', 'brand', 
                  'currency', 'specs', 'description', 'use_case', 'product_url']:
        if not product[field] or str(product[field]).strip() == '':
            errors.append(f"Empty value for required field: {field}")
    
    return errors


def csv_to_json(csv_file: str, json_file: str, quiet: bool = False) -> int:
    """
    Convert CSV file to JSON file.
    
    Args:
        csv_file: Path to input CSV file
        json_file: Path to output JSON file
        quiet: If True, suppress progress output
    
    Returns:
        Number of products converted
    """
    if not quiet:
        print(f"Converting {csv_file} to {json_file}...")
    
    products = []
    error_count = 0
    
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        # Check if all required fields are present
        csv_fields = reader.fieldnames
        missing_fields = set(EXPECTED_FIELDS) - set(csv_fields)
        if missing_fields:
            raise ValueError(f"CSV file is missing required fields: {missing_fields}")
        
        for row_num, row in enumerate(reader, start=2):  # start=2 because row 1 is header
            product = {field: row[field] for field in EXPECTED_FIELDS}
            
            # Convert price to float
            try:
                product['price'] = float(product['price'])
            except (ValueError, TypeError) as e:
                if not quiet:
                    print(f"Warning: Row {row_num} - Invalid price: {product['price']}")
                error_count += 1
                continue
            
            # Validate the product data
            errors = validate_product_data(product)
            if errors:
                if not quiet:
                    print(f"Warning: Row {row_num} - Validation errors: {errors}")
                error_count += 1
                continue
            
            products.append(product)
    
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    
    if not quiet:
        print(f"Successfully converted {len(products)} products to JSON")
        if error_count > 0:
            print(f"Skipped {error_count} products with errors")
    
    return len(products)


def json_to_csv(json_file: str, csv_file: str, quiet: bool = False) -> int:
    """
    Convert JSON file to CSV file.
    
    Args:
        json_file: Path to input JSON file
        csv_file: Path to output CSV file
        quiet: If True, suppress progress output
    
    Returns:
        Number of products converted
    """
    if not quiet:
        print(f"Converting {json_file} to {csv_file}...")
    
    with open(json_file, 'r', encoding='utf-8') as f:
        products = json.load(f)
    
    if not isinstance(products, list):
        raise ValueError("JSON file must contain a list of products")
    
    error_count = 0
    valid_products = []
    
    for product_num, product in enumerate(products, start=1):
        # Validate the product data
        errors = validate_product_data(product)
        if errors:
            if not quiet:
                print(f"Warning: Product {product_num} - Validation errors: {errors}")
            error_count += 1
            continue
        
        # Ensure all fields are present
        product_data = {field: product.get(field, '') for field in EXPECTED_FIELDS}
        valid_products.append(product_data)
    
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=EXPECTED_FIELDS)
        writer.writeheader()
        writer.writerows(valid_products)
    
    if not quiet:
        print(f"Successfully converted {len(valid_products)} products to CSV")
        if error_count > 0:
            print(f"Skipped {error_count} products with errors")
    
    return len(valid_products)


def main():
    parser = argparse.ArgumentParser(
        description='Convert product data between CSV and JSON formats',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s --csv-to-json products.csv products.json
  %(prog)s --json-to-csv products.json products.csv
  %(prog)s --csv-to-json input.csv output.json --quiet

Data format:
  CSV columns: product_id, product_name, category, subcategory, brand, price,
               currency, specs, description, use_case, product_url, last_updated
  JSON format: List of objects with the same fields
  Price: Numeric value (will be converted to float)
  last_updated: Date in YYYY-MM-DD format
        '''
    )
    
    parser.add_argument('--csv-to-json', metavar='CSV_FILE', 
                       help='Convert CSV file to JSON file')
    parser.add_argument('--json-to-csv', metavar='JSON_FILE',
                       help='Convert JSON file to CSV file')
    parser.add_argument('--output', '-o', metavar='OUTPUT_FILE',
                       help='Output file path')
    parser.add_argument('--quiet', '-q', action='store_true',
                       help='Suppress progress output')
    
    args = parser.parse_args()
    
    if not args.csv_to_json and not args.json_to_csv:
        parser.print_help()
        sys.exit(1)
    
    try:
        if args.csv_to_json:
            if not args.output:
                # Generate output filename
                if args.csv_to_json.endswith('.csv'):
                    args.output = args.csv_to_json[:-4] + '.json'
                else:
                    args.output = args.csv_to_json + '.json'
            
            count = csv_to_json(args.csv_to_json, args.output, args.quiet)
            if not args.quiet:
                print(f"Output: {args.output}")
        
        elif args.json_to_csv:
            if not args.output:
                # Generate output filename
                if args.json_to_csv.endswith('.json'):
                    args.output = args.json_to_csv[:-5] + '.csv'
                else:
                    args.output = args.json_to_csv + '.csv'
            
            count = json_to_csv(args.json_to_csv, args.output, args.quiet)
            if not args.quiet:
                print(f"Output: {args.output}")
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

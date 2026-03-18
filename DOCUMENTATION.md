# Vectra - BearDefend Documentation

## Project Overview

This is a production-ready multi-tenant FastAPI application serving as middleware between ChromaDB and external clients. It provides isolated data storage for each client using SQLite for structured data and ChromaDB for vector embeddings and semantic search.

## Environment Variables

All configuration can be set via environment variables:

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `ADMIN_API_KEY` | Secret key for admin endpoints | `maksud2026` | Yes (change in production!) |
| `FRESHNESS_THRESHOLD_DAYS` | Days before a product is considered stale | `7` | No |
| `SQLITE_DB_PATH` | SQLite database file path | `./data/sqlite/clients.db` | No |
| `CHROMADB_PATH` | ChromaDB persistent storage directory | `./data/chroma` | No |
| `PORT` | Server port | `8000` | No |
| `HOST` | Server host | `0.0.0.0` | No |

## Deployment Options

### Option 1: Docker Compose (Recommended)

1. **Create environment file:**
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

2. **Start the service:**
   ```bash
   docker-compose up -d
   ```

3. **Check logs:**
   ```bash
   docker-compose logs -f
   ```

4. **Stop the service:**
   ```bash
   docker-compose down
   ```

### Option 2: Docker Build & Run

1. **Build the image:**
   ```bash
   docker build -t chromadb-middleware .
   ```

2. **Run the container:**
   ```bash
   docker run -d \
     --name chromadb-middleware \
     -p 8000:8000 \
     -e ADMIN_API_KEY=your-secure-key \
     -e SQLITE_DB_PATH=/app/data/sqlite/clients.db \
     -e CHROMADB_PATH=/app/data/chroma \
     -v $(pwd)/data/sqlite:/app/data/sqlite \
     -v $(pwd)/data/chroma:/app/data/chroma \
     chromadb-middleware
   ```

### Option 3: Manual Installation (Python)

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Set environment variables:**
   ```bash
   export ADMIN_API_KEY=your-secure-key
   export SQLITE_DB_PATH=./data/sqlite/clients.db
   export CHROMADB_PATH=./data/chroma
   export PORT=8000
   ```

3. **Create data directories:**
   ```bash
   mkdir -p ./data/sqlite ./data/chroma
   ```

4. **Run the application:**
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000
   ```

## Persistence Configuration

The application is designed to persist data across restarts:

### SQLite Database
- **Default path:** `./data/sqlite/clients.db` (relative to application directory)
- **Environment variable:** `SQLITE_DB_PATH`
- **Docker volume:** Maps to host directory `./data/sqlite`

### ChromaDB Storage
- **Default path:** `./data/chroma` (relative to application directory)
- **Environment variable:** `CHROMADB_PATH`
- **Docker volume:** Maps to host directory `./data/chroma`

Both databases are stored on the host filesystem, ensuring data survives container restarts.

## Client Onboarding Process

1. **Admin creates a client** using the `/admin/client` endpoint:
   ```bash
   curl -X POST "http://localhost:8000/admin/client" \
     -H "X-Admin-Key: your-admin-key" \
     -H "Content-Type: application/json" \
     -d '{"client_id": "mycompany", "auth_code": "secret123", "operation": "create"}'
   ```

2. **Note the returned `table_name` and `collection_name`** - these are derived from the client_id but include hash suffixes for uniqueness.

3. **Use the provided credentials** for all subsequent API calls:
   - `X-Client-ID`: The client_id you provided
   - `X-Auth`: The auth_code you provided

## API Endpoints

### Client Authentication Endpoints

All endpoints except `/admin/client` require client authentication headers:
- `X-Client-ID`: Your client identifier
- `X-Auth`: Your authentication code

### 1. POST /search
**Protected by client authentication**

Search products using semantic search with optional filters.

**Request Body:**
```json
{
  "query": "wireless headphones",
  "max_result": 5,
  "max_price": 100.0,
  "min_price": 10.0,
  "category": "Electronics",
  "brand": "Sony",
  "use_case": "gaming"
}
```

**Parameters:**
- `query` (required): Search text
- `max_result` (optional, default: 1): Maximum number of results to return
- `max_price` (optional): Maximum price filter
- `min_price` (optional): Minimum price filter
- `category` (optional): Category filter
- `brand` (optional): Brand filter
- `use_case` (optional): Use case filter

**Response:**
```json
{
  "success": true,
  "message": "Search completed successfully",
  "results": [
    {
      "product_id": "prod123",
      "product_name": "Sony WH-1000XM4",
      "category": "Electronics",
      "subcategory": "Audio",
      "brand": "Sony",
      "price": 249.99,
      "currency": "USD",
      "use_case": "gaming",
      "product_url": "https://example.com/product",
      "freshness_warning": false,
      "score": 0.85
    }
  ],
  "count": 1
}
```

**Example curl:**
```bash
# Default: 1 result
curl -X POST "http://localhost:8000/search" \
  -H "X-Client-ID: mycompany" \
  -H "X-Auth: secret123" \
  -H "Content-Type: application/json" \
  -d '{"query": "wireless headphones", "max_price": 100.0}'

# Custom: 5 results
curl -X POST "http://localhost:8000/search" \
  -H "X-Client-ID: mycompany" \
  -H "X-Auth: secret123" \
  -H "Content-Type: application/json" \
  -d '{"query": "wireless headphones", "max_result": 5, "max_price": 100.0}'
```

### 2. POST /product
**Protected by client authentication**

Add a new product to both SQLite and ChromaDB.

**Request Body:**
```json
{
  "product_id": "prod123",
  "product_name": "Wireless Headphones",
  "category": "Electronics",
  "subcategory": "Audio",
  "brand": "Sony",
  "price": 99.99,
  "currency": "USD",
  "specs": "Bluetooth 5.0, 30hr battery",
  "description": "High-quality wireless headphones with noise cancellation",
  "use_case": "music",
  "product_url": "https://example.com/product",
  "last_updated": "2024-01-15"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Product created successfully",
  "product_id": "prod123"
}
```

**Example curl:**
```bash
curl -X POST "http://localhost:8000/product" \
  -H "X-Client-ID: mycompany" \
  -H "X-Auth: secret123" \
  -H "Content-Type: application/json" \
  -d '{"product_id": "prod123", "product_name": "Wireless Headphones", "category": "Electronics", "subcategory": "Audio", "brand": "Sony", "price": 99.99, "currency": "USD", "specs": "Bluetooth 5.0", "description": "High-quality headphones", "use_case": "music", "product_url": "https://example.com", "last_updated": "2024-01-15"}'
```

### 3. DELETE /product/{product_id}
**Protected by client authentication**

Delete a single product from both SQLite and ChromaDB.

**Response:**
```json
{
  "success": true,
  "message": "Product deleted successfully",
  "product_id": "prod123"
}
```

**Example curl:**
```bash
curl -X DELETE "http://localhost:8000/product/prod123" \
  -H "X-Client-ID: mycompany" \
  -H "X-Auth: secret123"
```

### 4. DELETE /products
**Protected by client authentication**

Delete multiple products from both SQLite and ChromaDB.

**Request Body:**
```json
{
  "product_ids": ["prod123", "prod456", "prod789"]
}
```

**Response:**
```json
{
  "success": true,
  "message": "Deleted 3 products successfully",
  "deleted_count": 3,
  "deleted_ids": ["prod123", "prod456", "prod789"]
}
```

**Example curl:**
```bash
curl -X DELETE "http://localhost:8000/products" \
  -H "X-Client-ID: mycompany" \
  -H "X-Auth: secret123" \
  -H "Content-Type: application/json" \
  -d '{"product_ids": ["prod123", "prod456"]}'
```

### 5. DELETE /products/all
**Protected by client authentication**

Delete all products for the client from both SQLite and ChromaDB.

**Response:**
```json
{
  "success": true,
  "message": "Deleted 42 products successfully",
  "deleted_count": 42
}
```

**Example curl:**
```bash
curl -X DELETE "http://localhost:8000/products/all" \
  -H "X-Client-ID: mycompany" \
  -H "X-Auth: secret123"
```

### 6. GET /health
**Protected by client authentication**

Get server status and product counts for the client.

**Response:**
```json
{
  "status": "healthy",
  "total_sqlite_products": 42,
  "total_chromadb_products": 42
}
```

**Example curl:**
```bash
curl -X GET "http://localhost:8000/health" \
  -H "X-Client-ID: mycompany" \
  -H "X-Auth: secret123"
```

### 7. POST /admin/client
**Protected by admin authentication only**

Create a new client in the registry.

**Request Headers:**
- `X-Admin-Key`: Admin API key from config.py

**Request Body:**
```json
{
  "client_id": "mycompany",
  "auth_code": "secret123"
}
```

**Response:**
```json
{
  "client_id": "mycompany",
  "table_name": "products_mycompany_a1b2c3d4",
  "collection_name": "client_mycompany_a1b2c3d4"
}
```

**Example curl:**
```bash
curl -X POST "http://localhost:8000/admin/client" \
  -H "X-Admin-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{"client_id": "mycompany", "auth_code": "secret123"}'
```

### 8. POST /rebuild
**Protected by client authentication**

Rebuild the ChromaDB collection from the SQLite database for a client. This is useful when products are added directly to SQLite (e.g., via CSV import or database manipulation) and the ChromaDB collection needs to be synchronized.

**Note**: If the SQLite database is empty, this endpoint will clear the ChromaDB collection and return a success message.

**Response (with products):**
```json
{
  "success": true,
  "message": "ChromaDB collection rebuilt successfully with 82 products",
  "count": 82,
  "collection_name": "client_mycompany_abc123def"
}
```

**Response (empty database):**
```json
{
  "success": true,
  "message": "ChromaDB collection cleared successfully (no products in SQLite database)",
  "count": 0,
  "collection_name": "client_mycompany_abc123def"
}
```

**Example curl:**
```bash
curl -X POST "http://localhost:8000/rebuild" \
  -H "X-Client-ID: mycompany" \
  -H "X-Auth: secret123"
```

### 9. GET /getAllProducts
**Protected by client authentication**

Get all products for the client from the database.

**Response:**
```json
{
  "success": true,
  "message": "Retrieved 80 products",
  "products": [
    {
      "product_id": "LAPTOP001",
      "product_name": "MacBook Pro 16-inch",
      "category": "Laptops",
      "subcategory": "Professional",
      "brand": "Apple",
      "price": 2499.99,
      "currency": "USD",
      "specs": "M3 Pro, 18GB RAM, 512GB SSD",
      "description": "Apple's most powerful laptop",
      "use_case": "work",
      "product_url": "https://apple.com/macbook-pro",
      "last_updated": "2024-01-15"
    }
  ],
  "count": 80
}
```

**Example curl:**
```bash
curl -X GET "http://localhost:8000/getAllProducts" \
  -H "X-Client-ID: mycompany" \
  -H "X-Auth: secret123"
```

### 10. POST /product (Updated - Bulk Support)
**Protected by client authentication**

Add one or more products to both SQLite and ChromaDB. Accepts either a single product object or a bulk request with multiple products.

**Single Product Request:**
```json
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
}
```

**Bulk Products Request:**
```json
{
  "products": [
    {
      "product_id": "PROD001",
      "product_name": "Product 1",
      "category": "Electronics",
      "subcategory": "Test",
      "brand": "TestBrand",
      "price": 49.99,
      "currency": "USD",
      "specs": "Test specs",
      "description": "Test description",
      "use_case": "test",
      "product_url": "https://example.com/product1",
      "last_updated": "2024-03-15"
    },
    {
      "product_id": "PROD002",
      "product_name": "Product 2",
      "category": "Electronics",
      "subcategory": "Test",
      "brand": "TestBrand",
      "price": 59.99,
      "currency": "USD",
      "specs": "Test specs",
      "description": "Test description",
      "use_case": "test",
      "product_url": "https://example.com/product2",
      "last_updated": "2024-03-15"
    }
  ]
}
```

**Response (Single):**
```json
{
  "success": true,
  "message": "Product created successfully",
  "product_id": "PROD001"
}
```

**Response (Bulk):**
```json
{
  "success": true,
  "message": "Bulk product creation completed. 2 products inserted, 0 skipped.",
  "inserted_count": 2,
  "skipped_count": 0,
  "skipped_products": []
}
```

**Example curl (Single):**
```bash
curl -X POST "http://localhost:8000/product" \
  -H "X-Client-ID: mycompany" \
  -H "X-Auth: secret123" \
  -H "Content-Type: application/json" \
  -d '{"product_id": "PROD001", "product_name": "Product 1", ...}'
```

**Example curl (Bulk):**
```bash
curl -X POST "http://localhost:8000/product" \
  -H "X-Client-ID: mycompany" \
  -H "X-Auth: secret123" \
  -H "Content-Type: application/json" \
  -d '{"products": [{"product_id": "PROD001", ...}, {"product_id": "PROD002", ...}]}'
```

### 11. PUT /editProduct/{product_id}
**Protected by client authentication**

Edit an existing product in both SQLite and ChromaDB. Only provided fields are updated.

**Request Body (partial update allowed):**
```json
{
  "product_name": "Updated Product Name",
  "price": 79.99,
  "description": "Updated description"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Product PROD001 updated successfully",
  "product_id": "PROD001"
}
```

**Example curl:**
```bash
curl -X PUT "http://localhost:8000/editProduct/PROD001" \
  -H "X-Client-ID: mycompany" \
  -H "X-Auth: secret123" \
  -H "Content-Type: application/json" \
  -d '{"product_name": "Updated Name", "price": 79.99}'
```

### 12. GET /admin/status
**Protected by admin authentication only**

Get detailed ChromaDB and SQLite status for admin monitoring. Shows client counts, collection counts, product counts, and sync status.

**Note**: ChromaDB collections cannot be deleted, only their contents can be cleared. Empty orphaned collections (with 0 documents) are NOT reported as problems - only collections with products that have no SQLite record are flagged.

**Response:**
```json
{
  "success": true,
  "message": "ChromaDB status retrieved successfully. Overall health: warning",
  "chromadb": {
    "total_collections": 9,
    "total_products": 85,
    "collections": [
      {
        "name": "client_finalclitest_2768c094",
        "product_count": 83
      }
    ],
    "orphaned_collections": [
      {
        "collection_name": "test",
        "product_count": 0
      }
    ],
    "health": "warning"
  },
  "sqlite": {
    "total_clients": 6,
    "total_products": 85,
    "clients": [
      {
        "client_id": "finalclitest",
        "table_name": "products_finalclitest_2768c094",
        "collection_name": "client_finalclitest_2768c094",
        "product_count": 83
      }
    ],
    "health": "healthy"
  },
  "clients": [
    {
      "client_id": "finalclitest",
      "table_name": "products_finalclitest_2768c094",
      "collection_name": "client_finalclitest_2768c094",
      "product_count": 83
    }
  ],
  "sync_status": {
    "total_clients": 6,
    "synced_clients": 5,
    "unsynced_clients": 1,
    "sync_rate": "83.3%",
    "issues": [
      {
        "client_id": "testclient2",
        "sqlite_count": 0,
        "chromadb_count": 0,
        "difference": 0,
        "issue": "ChromaDB collection not found"
      }
    ],
    "health": "warning"
  }
}
```

**Example curl:**
```bash
curl -X GET "http://localhost:8000/admin/status" \
  -H "X-Admin-Key: your-admin-key"
```

**Health Statuses:**
- `healthy`: All systems normal
- `warning`: Some issues detected (orphaned collections, sync issues)

### 13. POST /fixDBissues
**Protected by admin authentication only**

Fix common database issues in ChromaDB and SQLite. This endpoint:
- Clears orphaned ChromaDB collections that have products but no SQLite record
- Fixes sync mismatches between SQLite and ChromaDB
- Rebuilds missing ChromaDB collections from SQLite data

**Note**: Empty orphaned collections (with 0 documents) are normal and not considered issues since ChromaDB collections cannot be fully deleted.

**Response:**
```json
{
  "success": true,
  "message": "No database issues found. All systems healthy.",
  "summary": {
    "orphaned_collections_removed": 0,
    "sync_mismatches_fixed": 0,
    "empty_collections_cleaned": 0,
    "total_actions": 3,
    "actions": [
      {
        "action": "skipped_empty_orphaned_collection",
        "collection": "client_testdelete_c0dbe4ea",
        "product_count": 0,
        "status": "skipped"
      }
    ]
  }
}
```

**Example curl:**
```bash
curl -X POST "http://localhost:8000/fixDBissues" \
  -H "X-Admin-Key: your-admin-key"
```

**Action Types:**
- `cleared_orphaned_collection`: Successfully cleared documents from orphaned collection
- `fixed_sync_mismatch`: Rebuilt ChromaDB collection to match SQLite data
- `rebuilt_missing_collection`: Created ChromaDB collection for client with SQLite data
- `skipped_empty_orphaned_collection`: Orphaned collection already empty
- `failed_to_*`: Action failed with error details

### 14. POST /admin/client (Updated)
**Protected by admin authentication only**

Create or delete a client in the registry. Supports two operations:

#### Create Operation
Create a new client with SQLite table and ChromaDB collection.

**Request Body:**
```json
{
  "client_id": "mycompany",
  "auth_code": "secret123",
  "operation": "create"
}
```

**Response:**
```json
{
  "client_id": "mycompany",
  "table_name": "products_mycompany_abc123def",
  "collection_name": "client_mycompany_abc123def"
}
```

#### Delete Operation
Delete a client, including all products and the ChromaDB collection data.

**Note**: ChromaDB doesn't support deleting entire collections directly, so all documents within the collection are deleted. The collection metadata may remain but will be empty.

**Request Body:**
```json
{
  "client_id": "mycompany",
  "auth_code": "secret123",
  "operation": "delete"
}
```

**Response:**
```json
{
  "client_id": "mycompany",
  "table_name": "products_mycompany_abc123def",
  "collection_name": "client_mycompany_abc123def"
}
```

**Example curl (create):**
```bash
curl -X POST "http://localhost:8000/admin/client" \
  -H "X-Admin-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{"client_id": "mycompany", "auth_code": "secret123", "operation": "create"}'
```

**Example curl (delete):**
```bash
curl -X POST "http://localhost:8000/admin/client" \
  -H "X-Admin-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{"client_id": "mycompany", "auth_code": "secret123", "operation": "delete"}'
```

## How the Freshness Warning Works

The freshness warning is a boolean flag (`freshness_warning`) in search results that indicates whether a product's `last_updated` date is older than the configured threshold (default: 7 days).

- **Logic**: `(today - last_updated_date) > threshold_days`
- **Format**: Uses YYYY-MM-DD date format
- **Usage**: Helps users identify potentially outdated product information
- **Configuration**: Set `FRESHNESS_THRESHOLD_DAYS` in `config.py`

## Multi-Tenant Isolation Explanation

The application provides complete data isolation between clients:

### 1. SQLite Isolation
- Each client gets their own table: `products_<client_id>_<hash>`
- Table names include a SHA-256 hash suffix to prevent collisions
- Registry table (`clients`) stores mappings between client_id and their table/collection names

### 2. ChromaDB Isolation
- Each client gets their own collection: `client_<client_id>_<hash>`
- Collection names include a SHA-256 hash suffix to prevent collisions
- Collections are automatically created when a client is registered

### 3. Authentication & Authorization
- Client authentication uses `X-Client-ID` and `X-Auth` headers
- Admin endpoints use `X-Admin-Key` header
- All lookups use the registry table to validate credentials
- Table/collection names are never constructed directly from user input - always derived safely

### 4. Data Synchronization
- All write operations (create/delete) update both SQLite and ChromaDB
- If one operation fails, the other is rolled back
- Health check verifies both systems are in sync

## Error Handling

All endpoints use try-except blocks and return 500 errors with meaningful messages on unexpected failures. Common error responses:

```json
{
  "detail": "Error message describing the failure"
}
```

## Database Schema

### Clients Registry Table
```sql
CREATE TABLE clients (
    client_id TEXT PRIMARY KEY,
    auth_code TEXT NOT NULL,
    table_name TEXT NOT NULL,
    collection_name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

### Client Product Table
```sql
CREATE TABLE products_<client_id>_<hash> (
    product_id TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT NOT NULL,
    brand TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    specs TEXT NOT NULL,
    description TEXT NOT NULL,
    use_case TEXT NOT NULL,
    product_url TEXT NOT NULL,
    last_updated TEXT NOT NULL
)
```

## ChromaDB Document Format

### Document String
```
{product_name} by {brand}. Category: {category}, {subcategory}. Specs: {specs}. Description: {description}. Best for: {use_case}. Price: {price} {currency}
```

### Metadata Fields
- `product_id`
- `category`
- `subcategory`
- `brand`
- `price` (float)
- `currency`
- `use_case`
- `product_url`
- `last_updated`

## Security Notes

1. **Admin API Key**: Change the default key in `config.py` before production use
2. **Authentication**: All non-admin endpoints require client authentication
3. **SQL Injection**: Table names are derived using sanitization and hash suffixes
4. **Data Validation**: All inputs are validated using Pydantic models
5. **Date Format**: Only YYYY-MM-DD format is accepted

## Troubleshooting

### ChromaDB Connection Issues
- Ensure ChromaDB is running on `localhost:8000` (or configured host/port)
- Check ChromaDB logs for connection errors

### SQLite Issues
- The database file `clients.db` is created automatically
- File permissions must allow write access

### Common Errors
- **401 Unauthorized**: Check your `X-Client-ID` and `X-Auth` headers
- **404 Not Found**: Product or client doesn't exist
- **409 Conflict**: Client or product already exists
- **500 Internal Server Error**: Check application logs for details

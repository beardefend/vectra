# main.py
# Production-ready multi-tenant FastAPI middleware between ChromaDB and external clients.

import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional, Union
import os
import tempfile
import csv

try:
    import chromadb
    HAS_CHROMADB = True
except ImportError:
    # Fallback to mock implementation
    import chromadb_mock as chromadb
    HAS_CHROMADB = False

from fastapi import FastAPI, Depends, HTTPException, status, Request, Response, Header, UploadFile, File, Form, Body
from fastapi.responses import JSONResponse
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import base64
import uuid

import config
import utils
from auth import client_dependency, admin_dependency, ClientContext, derive_table_name, derive_collection_name


# Pydantic models for request bodies
class Product(BaseModel):
    product_id: str
    product_name: str
    category: str
    subcategory: str
    brand: str
    price: float
    currency: str
    specs: str
    description: str
    use_case: str
    product_url: str
    last_updated: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")


class SearchRequest(BaseModel):
    query: str
    max_result: int = 1
    max_price: Optional[float] = None
    min_price: Optional[float] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    use_case: Optional[str] = None


class BulkDeleteRequest(BaseModel):
    product_ids: List[str]


class BulkProductRequest(BaseModel):
    products: List[Product]


class EditProductRequest(BaseModel):
    product_name: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    brand: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    specs: Optional[str] = None
    description: Optional[str] = None
    use_case: Optional[str] = None
    product_url: Optional[str] = None
    last_updated: Optional[str] = None


class GetAllProductsResponse(BaseModel):
    success: bool
    message: str
    products: List[dict]
    count: int


class ClearAllProductsRequest(BaseModel):
    auth_code: Optional[str] = None


class AdminStatusResponse(BaseModel):
    success: bool
    message: str
    chromadb: dict
    sqlite: dict
    clients: List[dict]
    sync_status: dict


class AdminClientRequest(BaseModel):
    client_id: str
    auth_code: str
    operation: str = "create"  # "create" or "delete"


class AdminClientResponse(BaseModel):
    success: bool = True
    message: str = "Operation completed successfully"
    client_id: str
    table_name: str
    collection_name: str


class HealthResponse(BaseModel):
    status: str
    total_sqlite_products: int
    total_chromadb_products: int


# Global variables for ChromaDB client
chroma_client = None
templates = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan manager for application startup and shutdown.
    Initializes ChromaDB client and creates SQLite registry table.
    """
    global chroma_client
    # Initialize ChromaDB client using persistent mode (local storage)
    # This doesn't require a separate ChromaDB server running
    chroma_client = chromadb.PersistentClient(path=config.CHROMADB_PATH)

    # Initialize SQLite registry table
    init_sqlite_registry()

    # Mount static files directory
    app.mount("/static", StaticFiles(directory="static"), name="static")
    
    # Initialize templates
    global templates
    templates = Jinja2Templates(directory="templates")

    yield

    # Cleanup on shutdown
    if chroma_client:
        chroma_client = None


def init_sqlite_registry():
    """
    Creates the clients registry table on startup if it doesn't exist.
    """
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            client_id TEXT PRIMARY KEY,
            auth_code TEXT NOT NULL,
            table_name TEXT NOT NULL,
            collection_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def get_sqlite_connection():
    """
    Helper to get a fresh SQLite connection.
    """
    return sqlite3.connect(config.SQLITE_DB_PATH)


def execute_with_rollback(conn, queries, params_list=None):
    """
    Execute multiple queries with rollback on failure.
    Used to keep SQLite and ChromaDB in sync.
    """
    try:
        cursor = conn.cursor()
        if params_list is None:
            for query in queries:
                cursor.execute(query)
        else:
            for query, params in zip(queries, params_list):
                cursor.execute(query, params)
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        raise e


app = FastAPI(
    title="Vectra - BearDefend",
    description="Production-ready multi-tenant semantic search Engine",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)


@app.post("/search", response_model=dict)
async def search_products(
    request: SearchRequest,
    client: ClientContext = Depends(client_dependency)
):
    """
    Search products using ChromaDB semantic search with optional filters.
    Protected by client auth.
    """
    try:
        collection = chroma_client.get_or_create_collection(name=client.collection_name)

        # Build where clause dynamically
        filters = []
        
        if request.max_price is not None:
            filters.append({"price": {"$lte": request.max_price}})
        if request.min_price is not None:
            filters.append({"price": {"$gte": request.min_price}})
        if request.category:
            filters.append({"category": request.category})
        if request.brand:
            filters.append({"brand": request.brand})
        if request.use_case:
            filters.append({"use_case": request.use_case})

        # Build the where clause
        where_clause = {}
        if len(filters) == 1:
            where_clause = filters[0]
        elif len(filters) > 1:
            where_clause = {"$and": filters}

        # Perform search
        if where_clause:
            results = collection.query(
                query_texts=request.query,
                where=where_clause,
                n_results=request.max_result
            )
        else:
            results = collection.query(
                query_texts=request.query,
                n_results=request.max_result
            )

        # Process results and add freshness warnings
        processed_results = []
        if results["documents"] and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                metadata = results["metadatas"][0][i]
                freshness_warning = utils.check_freshness(
                    metadata["last_updated"],
                    config.FRESHNESS_THRESHOLD_DAYS
                )

                processed_results.append({
                    "product_id": metadata["product_id"],
                    "product_name": doc,
                    "category": metadata["category"],
                    "subcategory": metadata["subcategory"],
                    "brand": metadata["brand"],
                    "price": metadata["price"],
                    "currency": metadata["currency"],
                    "use_case": metadata["use_case"],
                    "product_url": metadata["product_url"],
                    "freshness_warning": freshness_warning,
                    "score": results["distances"][0][i] if results["distances"] else None
                })

        return {
            "success": True,
            "message": "Search completed successfully",
            "results": processed_results,
            "count": len(processed_results)
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}"
        )


@app.post("/product", response_model=dict)
async def create_product(
    request: Union[Product, BulkProductRequest],
    client: ClientContext = Depends(client_dependency)
):
    """
    Add one or more products to both SQLite and ChromaDB.
    Accepts either a single product object or a bulk request with multiple products.
    Protected by client auth.
    """
    conn = None
    try:
        # Determine if it's a single product or bulk request
        if isinstance(request, Product):
            products = [request]
            is_bulk = False
        else:
            products = request.products
            is_bulk = True
        
        # Validate and collect products to insert
        products_to_insert = []
        for product in products:
            # Validate date format
            try:
                datetime.strptime(product.last_updated, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid date format for product {product.product_id}. Use YYYY-MM-DD."
                )
            
            products_to_insert.append(product)
        
        # Connect to database
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        
        # Track results
        inserted_count = 0
        skipped_products = []
        
        # Process each product
        for product in products_to_insert:
            # Check if product already exists in SQLite
            cursor.execute(
                f"SELECT product_id FROM {client.table_name} WHERE product_id = ?",
                (product.product_id,)
            )
            if cursor.fetchone():
                skipped_products.append({
                    "product_id": product.product_id,
                    "reason": "Product already exists"
                })
                continue
            
            # Insert into SQLite
            cursor.execute(f"""
                INSERT INTO {client.table_name}
                (product_id, product_name, category, subcategory, brand, price, currency,
                 specs, description, use_case, product_url, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                product.product_id, product.product_name, product.category,
                product.subcategory, product.brand, product.price, product.currency,
                product.specs, product.description, product.use_case,
                product.product_url, product.last_updated
            ))
            
            # Insert into ChromaDB
            collection = chroma_client.get_or_create_collection(name=client.collection_name)
            document_string = utils.build_document_string(product.dict())
            
            collection.add(
                ids=[product.product_id],
                documents=[document_string],
                metadatas=[{
                    "product_id": product.product_id,
                    "category": product.category,
                    "subcategory": product.subcategory,
                    "brand": product.brand,
                    "price": product.price,
                    "currency": product.currency,
                    "use_case": product.use_case,
                    "product_url": product.product_url,
                    "last_updated": product.last_updated
                }]
            )
            
            inserted_count += 1
        
        conn.commit()
        
        # Build response
        if is_bulk:
            return {
                "success": True,
                "message": f"Bulk product creation completed. {inserted_count} products inserted, {len(skipped_products)} skipped.",
                "inserted_count": inserted_count,
                "skipped_count": len(skipped_products),
                "skipped_products": skipped_products
            }
        else:
            return {
                "success": True,
                "message": "Product created successfully",
                "product_id": products_to_insert[0].product_id
            }

    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        # Clean up ChromaDB if SQLite failed
        try:
            collection = chroma_client.get_or_create_collection(name=client.collection_name)
            # Delete all inserted products from this batch
            if 'products_to_insert' in locals():
                for product in products_to_insert:
                    collection.delete(ids=[product.product_id])
        except:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create product(s): {str(e)}"
        )
    finally:
        if conn:
            conn.close()


@app.put("/editProduct/{product_id}", response_model=dict)
async def edit_product(
    product_id: str,
    request: EditProductRequest,
    session_id: str = None,
    x_client_id: Optional[str] = Header(None, alias="X-Client-ID"),
    x_auth: Optional[str] = Header(None, alias="X-Auth")
):
    """
    Edit an existing product in both SQLite and ChromaDB.
    Protected by client auth (headers) or session auth (session_id).
    """
    # Determine client context based on authentication method
    client = None
    if session_id:
        # Session-based auth
        validate_session(session_id)
        session = active_sessions[session_id]
        
        if session["user_type"] == "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admins must use the admin edit endpoint"
            )
        
        # For regular clients, use their session client_id
        client_id = session["client_id"]
        table_name = derive_table_name(client_id)
        collection_name = derive_collection_name(client_id)
        client = ClientContext(client_id, table_name, collection_name)
    elif x_client_id and x_auth:
        # Header-based auth
        # Validate headers
        if not x_client_id or not x_auth:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing client ID or authentication header"
            )
        
        # Import here to avoid circular dependency
        import sqlite3
        import config
        
        conn = sqlite3.connect(config.SQLITE_DB_PATH)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "SELECT auth_code FROM clients WHERE client_id = ?",
                (x_client_id,)
            )
            row = cursor.fetchone()
            
            if not row or row[0] != x_auth:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid client ID or authentication"
                )
            
            table_name = derive_table_name(x_client_id)
            collection_name = derive_collection_name(x_client_id)
            client = ClientContext(
                client_id=x_client_id,
                table_name=table_name,
                collection_name=collection_name
            )
        finally:
            conn.close()
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required (session_id or X-Client-ID/X-Auth headers)"
        )
    
    conn = None
    try:
        # Connect to database
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        
        # Check if product exists
        cursor.execute(
            f"SELECT product_id FROM {client.table_name} WHERE product_id = ?",
            (product_id,)
        )
        if not cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product {product_id} not found"
            )
        
        # Get current product data
        cursor.execute(f"""
            SELECT product_id, product_name, category, subcategory, brand, price, currency,
                   specs, description, use_case, product_url, last_updated
            FROM {client.table_name}
            WHERE product_id = ?
        """, (product_id,))
        
        current_data = cursor.fetchone()
        
        # Build update fields and values
        update_fields = []
        update_values = []
        
        if request.product_name is not None:
            update_fields.append("product_name = ?")
            update_values.append(request.product_name)
        
        if request.category is not None:
            update_fields.append("category = ?")
            update_values.append(request.category)
        
        if request.subcategory is not None:
            update_fields.append("subcategory = ?")
            update_values.append(request.subcategory)
        
        if request.brand is not None:
            update_fields.append("brand = ?")
            update_values.append(request.brand)
        
        if request.price is not None:
            update_fields.append("price = ?")
            update_values.append(request.price)
        
        if request.currency is not None:
            update_fields.append("currency = ?")
            update_values.append(request.currency)
        
        if request.specs is not None:
            update_fields.append("specs = ?")
            update_values.append(request.specs)
        
        if request.description is not None:
            update_fields.append("description = ?")
            update_values.append(request.description)
        
        if request.use_case is not None:
            update_fields.append("use_case = ?")
            update_values.append(request.use_case)
        
        if request.product_url is not None:
            update_fields.append("product_url = ?")
            update_values.append(request.product_url)
        
        if request.last_updated is not None:
            # Validate date format
            try:
                datetime.strptime(request.last_updated, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid date format. Use YYYY-MM-DD."
                )
            update_fields.append("last_updated = ?")
            update_values.append(request.last_updated)
        
        # Check if there are any fields to update
        if not update_fields:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields provided for update"
            )
        
        # Add product_id for WHERE clause
        update_values.append(product_id)
        
        # Update SQLite
        update_sql = f"UPDATE {client.table_name} SET {', '.join(update_fields)} WHERE product_id = ?"
        cursor.execute(update_sql, update_values)
        
        # Get the complete updated product data from database
        cursor.execute(f"""
            SELECT product_id, product_name, category, subcategory, brand, price, currency,
                   specs, description, use_case, product_url, last_updated
            FROM {client.table_name}
            WHERE product_id = ?
        """, (product_id,))
        
        updated_row = cursor.fetchone()
        
        # Build updated product dict for ChromaDB
        updated_product = {
            'product_name': updated_row[1],
            'brand': updated_row[4],
            'category': updated_row[2],
            'subcategory': updated_row[3],
            'specs': updated_row[7],
            'description': updated_row[8],
            'use_case': updated_row[9],
            'price': updated_row[5],
            'currency': updated_row[6]
        }
        
        # Update ChromaDB
        collection = chroma_client.get_or_create_collection(name=client.collection_name)
        
        # Delete old document
        collection.delete(ids=[product_id])
        
        # Add updated document
        document_string = utils.build_document_string(updated_product)
        
        collection.add(
            ids=[product_id],
            documents=[document_string],
            metadatas=[{
                "product_id": product_id,
                "category": updated_row[2],
                "subcategory": updated_row[3],
                "brand": updated_row[4],
                "price": float(updated_row[5]),
                "currency": updated_row[6],
                "use_case": updated_row[9],
                "product_url": updated_row[10],
                "last_updated": updated_row[11]
            }]
        )
        
        conn.commit()
        
        return {
            "success": True,
            "message": f"Product {product_id} updated successfully",
            "product_id": product_id
        }

    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update product: {str(e)}"
        )
    finally:
        if conn:
            conn.close()


@app.get("/getAllProducts", response_model=GetAllProductsResponse)
async def get_all_products(
    client: ClientContext = Depends(client_dependency)
):
    """
    Get all products for the client from both SQLite and ChromaDB.
    Protected by client auth.
    """
    conn = None
    try:
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        
        # Get all products from SQLite
        cursor.execute(f"""
            SELECT 
                product_id, product_name, category, subcategory, brand, price, currency,
                specs, description, use_case, product_url, last_updated
            FROM {client.table_name}
            ORDER BY product_name
        """)
        
        products = cursor.fetchall()
        
        # Format products as list of dictionaries
        product_list = []
        for product in products:
            product_list.append({
                "product_id": product[0],
                "product_name": product[1],
                "category": product[2],
                "subcategory": product[3],
                "brand": product[4],
                "price": product[5],
                "currency": product[6],
                "specs": product[7],
                "description": product[8],
                "use_case": product[9],
                "product_url": product[10],
                "last_updated": product[11]
            })
        
        return GetAllProductsResponse(
            success=True,
            message=f"Retrieved {len(product_list)} products",
            products=product_list,
            count=len(product_list)
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve products: {str(e)}"
        )
    finally:
        if conn:
            conn.close()


@app.delete("/product/{product_id}", response_model=dict)
async def delete_product(
    product_id: str,
    client: ClientContext = Depends(client_dependency)
):
    """
    Delete a single product from both SQLite and ChromaDB.
    Protected by client auth.
    """
    conn = None
    try:
        conn = get_sqlite_connection()
        cursor = conn.cursor()

        # Check if product exists in SQLite
        cursor.execute(
            f"SELECT product_id FROM {client.table_name} WHERE product_id = ?",
            (product_id,)
        )
        if not cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found"
            )

        # Delete from SQLite
        cursor.execute(
            f"DELETE FROM {client.table_name} WHERE product_id = ?",
            (product_id,)
        )

        # Delete from ChromaDB
        collection = chroma_client.get_or_create_collection(name=client.collection_name)
        collection.delete(ids=[product_id])

        conn.commit()

        return {
            "success": True,
            "message": "Product deleted successfully",
            "product_id": product_id
        }

    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete product: {str(e)}"
        )
    finally:
        if conn:
            conn.close()


@app.delete("/products", response_model=dict)
async def bulk_delete_products(
    request: BulkDeleteRequest,
    client: ClientContext = Depends(client_dependency)
):
    """
    Delete multiple products from both SQLite and ChromaDB.
    Protected by client auth.
    """
    conn = None
    try:
        conn = get_sqlite_connection()
        cursor = conn.cursor()

        # Prepare placeholders for SQLite query
        placeholders = ",".join("?" * len(request.product_ids))

        # Check which products exist
        cursor.execute(
            f"SELECT product_id FROM {client.table_name} WHERE product_id IN ({placeholders})",
            request.product_ids
        )
        existing_ids = [row[0] for row in cursor.fetchall()]

        if not existing_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No products found with the provided IDs"
            )

        # Delete from SQLite
        cursor.execute(
            f"DELETE FROM {client.table_name} WHERE product_id IN ({placeholders})",
            request.product_ids
        )

        # Delete from ChromaDB
        collection = chroma_client.get_or_create_collection(name=client.collection_name)
        collection.delete(ids=request.product_ids)

        conn.commit()

        return {
            "success": True,
            "message": f"Deleted {len(existing_ids)} products successfully",
            "deleted_count": len(existing_ids),
            "deleted_ids": existing_ids
        }

    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete products: {str(e)}"
        )
    finally:
        if conn:
            conn.close()


@app.delete("/products/all", response_model=dict)
async def delete_all_products(
    client: ClientContext = Depends(client_dependency)
):
    """
    Delete all products for the client from both SQLite and ChromaDB.
    Protected by client auth.
    """
    conn = None
    try:
        conn = get_sqlite_connection()
        cursor = conn.cursor()

        # Get count before deletion
        cursor.execute(f"SELECT COUNT(*) FROM {client.table_name}")
        count = cursor.fetchone()[0]

        # Delete from SQLite
        cursor.execute(f"DELETE FROM {client.table_name}")

        # Delete from ChromaDB collection
        collection = chroma_client.get_or_create_collection(name=client.collection_name)
        # Get all IDs and delete them
        existing_ids = collection.get()['ids']
        if existing_ids:
            collection.delete(ids=existing_ids)

        conn.commit()

        return {
            "success": True,
            "message": f"Deleted {count} products successfully",
            "deleted_count": count
        }

    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete all products: {str(e)}"
        )
    finally:
        if conn:
            conn.close()


@app.get("/health", response_model=HealthResponse)
async def get_health(
    client: ClientContext = Depends(client_dependency)
):
    """
    Get server status and product counts for the client.
    Protected by client auth.
    """
    try:
        # Get SQLite product count
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {client.table_name}")
        sqlite_count = cursor.fetchone()[0]
        conn.close()

        # Get ChromaDB product count
        collection = chroma_client.get_or_create_collection(name=client.collection_name)
        chromadb_count = collection.count()

        return HealthResponse(
            status="healthy",
            total_sqlite_products=sqlite_count,
            total_chromadb_products=chromadb_count
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Health check failed: {str(e)}"
        )


async def manage_client_helper(request: AdminClientRequest):
    """
    Core logic to create or delete a client in the registry.
    
    Operation types:
    - "create": Creates a new client with SQLite table and ChromaDB collection
    - "delete": Deletes a client, including all products and the ChromaDB collection
    """
    conn = None
    try:
        table_name = derive_table_name(request.client_id)
        collection_name = derive_collection_name(request.client_id)

        if request.operation == "create":
            # Check if client already exists
            conn = get_sqlite_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT client_id FROM clients WHERE client_id = ?",
                (request.client_id,)
            )
            if cursor.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Client already exists"
                )

            # Create client's product table in SQLite
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
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
            """)

            # Initialize empty ChromaDB collection for the client
            collection = chroma_client.get_or_create_collection(name=collection_name)

            # Insert client record into registry
            cursor.execute(
                "INSERT INTO clients (client_id, auth_code, table_name, collection_name) VALUES (?, ?, ?, ?)",
                (request.client_id, request.auth_code, table_name, collection_name)
            )

            conn.commit()

            return AdminClientResponse(
                success=True,
                message=f"Client {request.client_id} created successfully",
                client_id=request.client_id,
                table_name=table_name,
                collection_name=collection_name
            )

        elif request.operation == "delete":
            # Check if client exists
            conn = get_sqlite_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT table_name, collection_name FROM clients WHERE client_id = ?",
                (request.client_id,)
            )
            result = cursor.fetchone()
            
            if not result:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Client not found"
                )
            
            existing_table_name, existing_collection_name = result

            # Delete the client's product table from SQLite
            cursor.execute(f"DROP TABLE IF EXISTS {existing_table_name}")

            # Delete all documents from the ChromaDB collection
            # Note: ChromaDB doesn't support deleting collections directly,
            # so we delete all documents from the collection
            try:
                collection = chroma_client.get_or_create_collection(name=existing_collection_name)
                # Get all IDs and delete them
                existing_ids = collection.get()['ids']
                if existing_ids:
                    collection.delete(ids=existing_ids)
            except Exception as e:
                # ChromaDB collection might not exist, log but continue
                pass

            # Remove client record from registry
            cursor.execute(
                "DELETE FROM clients WHERE client_id = ?",
                (request.client_id,)
            )

            conn.commit()

            return AdminClientResponse(
                success=True,
                message=f"Client {request.client_id} deleted successfully",
                client_id=request.client_id,
                table_name=existing_table_name,
                collection_name=existing_collection_name
            )

        elif request.operation == "update":
            # Update client's auth code
            if not request.auth_code:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Auth code is required for update operation"
                )
            
            # Check if client exists
            conn = get_sqlite_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT table_name, collection_name FROM clients WHERE client_id = ?",
                (request.client_id,)
            )
            result = cursor.fetchone()
            
            if not result:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Client not found"
                )
            
            existing_table_name, existing_collection_name = result

            # Update the auth code
            cursor.execute(
                "UPDATE clients SET auth_code = ? WHERE client_id = ?",
                (request.auth_code, request.client_id)
            )

            conn.commit()

            return AdminClientResponse(
                success=True,
                message=f"Auth code updated for client {request.client_id}",
                client_id=request.client_id,
                table_name=existing_table_name,
                collection_name=existing_collection_name
            )

        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid operation. Use 'create', 'delete', or 'update'."
            )

    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to {request.operation} client: {str(e)}"
        )
    finally:
        if conn:
            conn.close()


@app.post("/admin/client", response_model=AdminClientResponse)
async def manage_client(
    request: AdminClientRequest,
    admin: None = Depends(admin_dependency)
):
    """
    Create or delete a client in the registry.
    Protected by admin dependency only.
    
    Operation types:
    - "create": Creates a new client with SQLite table and ChromaDB collection
    - "delete": Deletes a client, including all products and the ChromaDB collection
    """
    return await manage_client_helper(request)


def rebuild_collection_helper(client: ClientContext):
    """
    Helper function to rebuild a client's ChromaDB collection from SQLite.
    This contains the core rebuild logic that can be reused by both
    the core API endpoint and the dashboard API endpoint.
    
    Args:
        client: ClientContext object with client_id, table_name, and collection_name
        
    Returns:
        dict: Success response with rebuild details
        
    Raises:
        HTTPException: If rebuild fails
    """
    conn = None
    try:
        conn = get_sqlite_connection()
        cursor = conn.cursor()

        # Get all products from the client's SQLite table
        cursor.execute(f"""
            SELECT 
                product_id, product_name, category, subcategory, brand, 
                price, currency, specs, description, use_case, product_url, last_updated
            FROM {client.table_name}
        """)
        products = cursor.fetchall()

        # Get the ChromaDB collection
        collection = chroma_client.get_or_create_collection(name=client.collection_name)

        # Delete existing collection data
        # Get all IDs and delete them
        existing_ids = collection.get()['ids']
        if existing_ids:
            collection.delete(ids=existing_ids)

        # If no products in SQLite, return success with empty collection
        if not products:
            return {
                "success": True,
                "message": "ChromaDB collection cleared successfully (no products in SQLite database)",
                "count": 0,
                "collection_name": client.collection_name
            }

        # Rebuild the collection from SQLite data
        batch_size = 100
        for i in range(0, len(products), batch_size):
            batch = products[i:i + batch_size]
            
            ids = []
            documents = []
            metadatas = []

            for product in batch:
                (
                    product_id, product_name, category, subcategory, brand,
                    price, currency, specs, description, use_case, product_url, last_updated
                ) = product

                # Build document string using the utils function
                product_dict = {
                    'product_name': product_name,
                    'brand': brand,
                    'category': category,
                    'subcategory': subcategory,
                    'specs': specs,
                    'description': description,
                    'use_case': use_case,
                    'price': price,
                    'currency': currency
                }
                document_string = utils.build_document_string(product_dict)

                ids.append(product_id)
                documents.append(document_string)
                metadatas.append({
                    'product_id': product_id,
                    'category': category,
                    'subcategory': subcategory,
                    'brand': brand,
                    'price': float(price),
                    'currency': currency,
                    'use_case': use_case,
                    'product_url': product_url,
                    'last_updated': last_updated
                })

            # Add batch to ChromaDB
            collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas
            )

        return {
            "success": True,
            "message": f"ChromaDB collection rebuilt successfully with {len(products)} products",
            "count": len(products),
            "collection_name": client.collection_name
        }

    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to rebuild collection: {str(e)}"
        )
    finally:
        if conn:
            conn.close()


@app.post("/rebuild", response_model=dict)
async def rebuild_collection(
    client: ClientContext = Depends(client_dependency)
):
    """
    Rebuild the ChromaDB collection from SQLite database for a client.
    This is useful when products are added directly to SQLite (e.g., via CSV import)
    and the ChromaDB collection needs to be synchronized.
    
    Protected by client auth.
    """
    return rebuild_collection_helper(client)


@app.get("/admin/status", response_model=AdminStatusResponse)
async def admin_status(
    admin: None = Depends(admin_dependency)
):
    """
    Get detailed ChromaDB and SQLite status for admin monitoring.
    Shows client counts, collection counts, product counts, and sync status.
    Protected by admin dependency only.
    """
    conn = None
    try:
        # Connect to SQLite
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        
        # Get all clients from registry
        cursor.execute("SELECT client_id, table_name, collection_name FROM clients ORDER BY client_id")
        clients_data = cursor.fetchall()
        
        # ChromaDB status
        chromadb_collections = []
        chromadb_total_products = 0
        chromadb_total_collections = 0
        
        try:
            collections = chroma_client.list_collections()
            for collection in collections:
                try:
                    count = collection.count()
                    chromadb_total_collections += 1
                    chromadb_total_products += count
                    chromadb_collections.append({
                        "name": collection.name,
                        "product_count": count
                    })
                except Exception as e:
                    chromadb_collections.append({
                        "name": collection.name,
                        "product_count": 0,
                        "error": str(e)
                    })
        except Exception as e:
            chromadb_collections.append({
                "error": f"Failed to list collections: {str(e)}"
            })
        
        # SQLite status
        sqlite_clients = []
        sqlite_total_products = 0
        
        for client_id, table_name, collection_name in clients_data:
            try:
                # Get product count for this client's table
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                product_count = cursor.fetchone()[0]
                sqlite_total_products += product_count
                
                sqlite_clients.append({
                    "client_id": client_id,
                    "table_name": table_name,
                    "collection_name": collection_name,
                    "product_count": product_count
                })
            except Exception as e:
                sqlite_clients.append({
                    "client_id": client_id,
                    "table_name": table_name,
                    "collection_name": collection_name,
                    "product_count": 0,
                    "error": str(e)
                })
        
        # Check sync status between SQLite and ChromaDB
        sync_issues = []
        synced_clients = 0
        unsynced_clients = 0
        
        for client_info in sqlite_clients:
            client_id = client_info["client_id"]
            sqlite_count = client_info["product_count"]
            
            # Find corresponding ChromaDB collection
            chromadb_collection_name = client_info["collection_name"]
            chromadb_collection_info = next(
                (c for c in chromadb_collections if c["name"] == chromadb_collection_name),
                None
            )
            
            if chromadb_collection_info:
                chromadb_count = chromadb_collection_info["product_count"]
                if sqlite_count == chromadb_count:
                    synced_clients += 1
                else:
                    unsynced_clients += 1
                    sync_issues.append({
                        "client_id": client_id,
                        "sqlite_count": sqlite_count,
                        "chromadb_count": chromadb_count,
                        "difference": sqlite_count - chromadb_count
                    })
            else:
                unsynced_clients += 1
                sync_issues.append({
                    "client_id": client_id,
                    "sqlite_count": sqlite_count,
                    "chromadb_count": 0,
                    "difference": sqlite_count,
                    "issue": "ChromaDB collection not found"
                })
        
        # Check for orphaned ChromaDB collections (collections without SQLite records)
        # Only report collections that have products (empty orphaned collections are not a problem)
        orphaned_collections = []
        for chromadb_collection in chromadb_collections:
            collection_name = chromadb_collection["name"]
            product_count = chromadb_collection["product_count"]
            found_in_sqlite = any(
                c["collection_name"] == collection_name 
                for c in sqlite_clients
            )
            # Only report orphaned collections that have products
            if not found_in_sqlite and product_count > 0:
                orphaned_collections.append({
                    "collection_name": collection_name,
                    "product_count": product_count
                })
        
        # Build response - only report orphaned collections that have products
        # Empty orphaned collections are not a problem (they can't be deleted from ChromaDB)
        chromadb_status = {
            "total_collections": chromadb_total_collections,
            "total_products": chromadb_total_products,
            "collections": chromadb_collections,
            "orphaned_collections": orphaned_collections,
            "health": "healthy" if len(orphaned_collections) == 0 else "warning"
        }
        
        sqlite_status = {
            "total_clients": len(sqlite_clients),
            "total_products": sqlite_total_products,
            "clients": sqlite_clients,
            "health": "healthy"
        }
        
        sync_status = {
            "total_clients": len(sqlite_clients),
            "synced_clients": synced_clients,
            "unsynced_clients": unsynced_clients,
            "sync_rate": f"{(synced_clients / len(sqlite_clients) * 100):.1f}%" if sqlite_clients else "100%",
            "issues": sync_issues,
            "health": "healthy" if unsynced_clients == 0 else "warning"
        }
        
        overall_health = "healthy"
        if sync_status["health"] == "warning" or chromadb_status["health"] == "warning":
            overall_health = "warning"
        
        return AdminStatusResponse(
            success=True,
            message=f"ChromaDB status retrieved successfully. Overall health: {overall_health}",
            chromadb=chromadb_status,
            sqlite=sqlite_status,
            clients=sqlite_clients,
            sync_status=sync_status
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve admin status: {str(e)}"
        )
    finally:
        if conn:
            conn.close()


@app.post("/fixDBissues", response_model=dict)
async def fix_db_issues(
    admin: None = Depends(admin_dependency)
):
    """
    Fix common database issues:
    - Remove orphaned ChromaDB collections (collections without SQLite records)
    - Fix sync mismatches between SQLite and ChromaDB
    - Clean up empty collections that have no products
    
    Protected by admin dependency only.
    """
    conn = None
    try:
        # Connect to SQLite
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        
        # Get all registered clients from SQLite
        cursor.execute("SELECT client_id, table_name, collection_name FROM clients")
        registered_clients = {row[2]: row for row in cursor.fetchall()}
        
        # Issues fixed counters
        orphaned_collections_removed = 0
        sync_mismatches_fixed = 0
        empty_collections_cleaned = 0
        
        # Track actions taken
        actions_taken = []
        
        # Get all ChromaDB collections
        try:
            collections = chroma_client.list_collections()
            for collection_wrapper in collections:
                collection_name = collection_wrapper.name
                collection = chroma_client.get_or_create_collection(name=collection_name)
                
                # Skip if it's the "test" collection (used for testing)
                if collection_name == "test":
                    continue
                
                # Check if collection is orphaned (no corresponding SQLite record)
                if collection_name not in registered_clients:
                    # This is an orphaned collection
                    try:
                        collection_count = collection.count()
                        existing_ids = collection.get()['ids']
                        if existing_ids:
                            collection.delete(ids=existing_ids)
                            orphaned_collections_removed += 1
                            actions_taken.append({
                                "action": "cleared_orphaned_collection",
                                "collection": collection_name,
                                "product_count": collection_count,
                                "status": "success"
                            })
                        else:
                            # Collection is already empty, just report it
                            actions_taken.append({
                                "action": "skipped_empty_orphaned_collection",
                                "collection": collection_name,
                                "product_count": 0,
                                "status": "skipped"
                            })
                    except Exception as e:
                        actions_taken.append({
                            "action": "failed_to_clear_orphaned",
                            "collection": collection_name,
                            "error": str(e),
                            "status": "failed"
                        })
                else:
                    # Collection exists in SQLite, check for sync issues
                    client_id, table_name, _ = registered_clients[collection_name]
                    
                    # Get product count from SQLite
                    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                    sqlite_count = cursor.fetchone()[0]
                    
                    # Get product count from ChromaDB
                    chromadb_count = collection.count()
                    
                    if sqlite_count != chromadb_count:
                        # Sync mismatch - rebuild collection from SQLite
                        try:
                            # Get all products from SQLite
                            cursor.execute(f"""
                                SELECT 
                                    product_id, product_name, category, subcategory, brand, 
                                    price, currency, specs, description, use_case, product_url, last_updated
                                FROM {table_name}
                            """)
                            products = cursor.fetchall()
                            
                            # Clear ChromaDB collection
                            existing_ids = collection.get()['ids']
                            if existing_ids:
                                collection.delete(ids=existing_ids)
                            
                            # Rebuild collection
                            for product in products:
                                (
                                    product_id, product_name, category, subcategory, brand,
                                    price, currency, specs, description, use_case, product_url, last_updated
                                ) = product
                                
                                product_dict = {
                                    'product_name': product_name,
                                    'brand': brand,
                                    'category': category,
                                    'subcategory': subcategory,
                                    'specs': specs,
                                    'description': description,
                                    'use_case': use_case,
                                    'price': price,
                                    'currency': currency
                                }
                                document_string = utils.build_document_string(product_dict)
                                
                                collection.add(
                                    ids=[product_id],
                                    documents=[document_string],
                                    metadatas=[{
                                        'product_id': product_id,
                                        'category': category,
                                        'subcategory': subcategory,
                                        'brand': brand,
                                        'price': float(price),
                                        'currency': currency,
                                        'use_case': use_case,
                                        'product_url': product_url,
                                        'last_updated': last_updated
                                    }]
                                )
                            
                            sync_mismatches_fixed += 1
                            actions_taken.append({
                                "action": "fixed_sync_mismatch",
                                "client_id": client_id,
                                "collection": collection_name,
                                "sqlite_count": sqlite_count,
                                "chromadb_count_before": chromadb_count,
                                "status": "success"
                            })
                        except Exception as e:
                            actions_taken.append({
                                "action": "failed_to_fix_sync",
                                "client_id": client_id,
                                "collection": collection_name,
                                "error": str(e),
                                "status": "failed"
                            })
                    elif sqlite_count == 0 and chromadb_count == 0:
                        # Both are empty, ensure collection is clean
                        existing_ids = collection.get()['ids']
                        if existing_ids:
                            collection.delete(ids=existing_ids)
                            empty_collections_cleaned += 1
                            actions_taken.append({
                                "action": "cleaned_empty_collection",
                                "collection": collection_name,
                                "status": "success"
                            })
        
        except Exception as e:
            actions_taken.append({
                "action": "failed_to_list_collections",
                "error": str(e),
                "status": "failed"
            })
        
        # Also check for SQLite tables without ChromaDB collections (rare but possible)
        for client_id, table_name, collection_name in registered_clients.values():
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                sqlite_count = cursor.fetchone()[0]
                 
                # Verify collection exists
                try:
                    collection = chroma_client.get_or_create_collection(name=collection_name)
                    chromadb_count = collection.count()
                except:
                    # Collection might have issues, try to rebuild
                    if sqlite_count > 0:
                        # Rebuild collection from SQLite
                        cursor.execute(f"""
                            SELECT 
                                product_id, product_name, category, subcategory, brand, 
                                price, currency, specs, description, use_case, product_url, last_updated
                            FROM {table_name}
                        """)
                        products = cursor.fetchall()
                        
                        # Clear and rebuild
                        try:
                            collection.delete(ids=collection.get()['ids'])
                        except:
                            pass
                        
                        for product in products:
                            (
                                product_id, product_name, category, subcategory, brand,
                                price, currency, specs, description, use_case, product_url, last_updated
                            ) = product
                            
                            product_dict = {
                                'product_name': product_name,
                                'brand': brand,
                                'category': category,
                                'subcategory': subcategory,
                                'specs': specs,
                                'description': description,
                                'use_case': use_case,
                                'price': price,
                                'currency': currency
                            }
                            document_string = utils.build_document_string(product_dict)
                            
                            collection.add(
                                ids=[product_id],
                                documents=[document_string],
                                metadatas=[{
                                    'product_id': product_id,
                                    'category': category,
                                    'subcategory': subcategory,
                                    'brand': brand,
                                    'price': float(price),
                                    'currency': currency,
                                    'use_case': use_case,
                                    'product_url': product_url,
                                    'last_updated': last_updated
                                }]
                            )
                        
                        actions_taken.append({
                            "action": "rebuilt_missing_collection",
                            "client_id": client_id,
                            "collection": collection_name,
                            "product_count": sqlite_count,
                            "status": "success"
                        })
            except Exception as e:
                actions_taken.append({
                    "action": "failed_to_check_table",
                    "client_id": client_id,
                    "table": table_name,
                    "error": str(e),
                    "status": "failed"
                })
        
        # Build summary
        summary = {
            "orphaned_collections_cleared": orphaned_collections_removed,
            "sync_mismatches_fixed": sync_mismatches_fixed,
            "empty_collections_cleaned": empty_collections_cleaned,
            "total_actions": len(actions_taken),
            "actions": actions_taken
        }
        
        # Determine overall status
        if orphaned_collections_removed > 0 or sync_mismatches_fixed > 0 or empty_collections_cleaned > 0:
            message = f"Database issues fixed: {orphaned_collections_removed} orphaned collections cleared, {sync_mismatches_fixed} sync mismatches fixed, {empty_collections_cleaned} empty collections cleaned."
        else:
            message = "No database issues found. All systems healthy. (Note: Empty orphaned collections are normal and not a problem)"
        
        # Note about orphaned collections
        if orphaned_collections_removed > 0:
            message += " Note: ChromaDB collections cannot be deleted, only their contents are cleared."
        
        return {
            "success": True,
            "message": message,
            "summary": summary
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fix database issues: {str(e)}"
        )
    finally:
        if conn:
            conn.close()


# Login session storage (in-memory for simplicity, in production use Redis/Database)
active_sessions = {}

class LoginRequest(BaseModel):
    client_id: str
    auth_code: str

class LoginResponse(BaseModel):
    success: bool
    message: str
    session_id: str = None
    user_type: str = None  # "admin" or "client"

@app.post("/api/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    Login endpoint for clients and admins
    """
    # Check if it's an admin login
    if request.client_id == "admin" and request.auth_code == config.ADMIN_API_KEY:
        session_id = str(uuid.uuid4())
        active_sessions[session_id] = {
            "user_type": "admin",
            "client_id": "admin",
            "auth_code": request.auth_code
        }
        return LoginResponse(
            success=True,
            message="Admin login successful",
            session_id=session_id,
            user_type="admin"
        )
    
    # Check if it's a client login
    conn = None
    try:
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT auth_code FROM clients WHERE client_id = ?",
            (request.client_id,)
        )
        result = cursor.fetchone()
        
        if result and result[0] == request.auth_code:
            session_id = str(uuid.uuid4())
            active_sessions[session_id] = {
                "user_type": "client",
                "client_id": request.client_id,
                "auth_code": request.auth_code
            }
            return LoginResponse(
                success=True,
                message="Client login successful",
                session_id=session_id,
                user_type="client"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid client ID or authentication code"
            )
    finally:
        if conn:
            conn.close()

@app.get("/api/logout")
async def logout(session_id: str = None):
    """
    Logout endpoint
    """
    if session_id and session_id in active_sessions:
        del active_sessions[session_id]
    return {"success": True, "message": "Logged out successfully"}

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """
    Vectra - BearDefend API Homepage
    High-Performance Data-Architect Interface
    """
    # Check for session cookie
    session_id = request.cookies.get("session_id")
    is_logged_in = session_id in active_sessions if session_id else False
    user_type = active_sessions.get(session_id, {}).get("user_type") if is_logged_in else None
    client_id = active_sessions.get(session_id, {}).get("client_id") if is_logged_in else None
    
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Vectra Semantic Search Engine</title>
    <link rel="icon" type="image/x-icon" href="/static/favicon.ico">
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root { --gray-900: #111827; --gray-800: #1f2937; --gray-700: #374151; --gray-600: #4b5563; --gray-500: #6b7280; --gray-400: #9ca3af; --gray-300: #d1d5db; --gray-200: #e5e7eb; --gray-100: #f3f4f6; --lime-400: #a3e635; --lime-500: #84cc16; --cyan-400: #22d3ee; --purple-400: #c084fc; }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Inter', sans-serif; background-color: var(--gray-900); color: var(--gray-300); min-height: 100vh; line-height: 1.6; }
        body::before { content: ''; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background-image: linear-gradient(rgba(163, 230, 53, 0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(163, 230, 53, 0.03) 1px, transparent 1px); background-size: 20px 20px; pointer-events: none; z-index: 0; }
        .container { max-width: 1400px; margin: 0 auto; padding: 0 24px; position: relative; z-index: 1; }
        header { padding: 20px 0; border-bottom: 1px solid var(--gray-800); background: linear-gradient(180deg, rgba(31, 41, 55, 0.8) 0%, rgba(17, 24, 39, 0.95) 100%); backdrop-filter: blur(10px); position: sticky; top: 0; z-index: 100; }
        .header-inner { display: flex; justify-content: space-between; align-items: center; padding: 0 48px; max-width: 1400px; margin: 0 auto; }
        .nav-links { display: flex; gap: 32px; align-items: center; justify-content: flex-end; flex: 1; }
        .logo-section { display: flex; align-items: center; gap: 16px; }
        .logo { font-family: 'Inter', sans-serif; font-size: 28px; font-weight: 700; letter-spacing: -0.5px; color: var(--lime-400); display: flex; align-items: center; gap: 8px; }
        .logo img { width: 32px; height: 32px; border-radius: 4px; }
        .logo-icon { width: 32px; height: 32px; background: linear-gradient(135deg, var(--lime-400), var(--cyan-400)); border-radius: 4px; display: flex; align-items: center; justify-content: center; font-weight: 800; color: var(--gray-900); font-size: 14px; }
        .company-badge { background: var(--gray-800); color: var(--gray-500); padding: 4px 12px; border-radius: 4px; font-size: 10px; font-weight: 500; letter-spacing: 0.5px; text-transform: uppercase; border: 1px solid var(--gray-700); }
        .nav-links { display: flex; gap: 32px; align-items: center; }
        .nav-link { color: var(--gray-400); text-decoration: none; font-size: 14px; font-weight: 500; transition: color 0.2s; }
        .nav-link:hover { color: var(--lime-400); }
        .nav-link.active { color: var(--lime-400); border-bottom: 2px solid var(--lime-400); padding-bottom: 2px; }
        .hero { padding: 80px 24px; text-align: center; position: relative; }
        .hero-badge { display: inline-flex; align-items: center; gap: 8px; background: linear-gradient(135deg, rgba(163, 230, 53, 0.1), rgba(34, 211, 238, 0.1)); border: 1px solid rgba(163, 230, 53, 0.3); padding: 6px 16px; border-radius: 50px; font-size: 12px; color: var(--lime-400); font-weight: 500; margin-bottom: 24px; }
        .hero-badge::before { content: 'o'; animation: pulse 2s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .hero-title { font-size: 56px; font-weight: 800; letter-spacing: -2px; background: linear-gradient(135deg, var(--gray-100), var(--gray-300)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 16px; line-height: 1.1; }
        .hero-subtitle { font-size: 20px; color: var(--gray-500); max-width: 700px; margin: 0 auto 32px; line-height: 1.6; }
        .hero-actions { display: flex; gap: 16px; justify-content: center; margin-top: 32px; }
        .btn-primary { background: var(--lime-400); color: var(--gray-900); padding: 14px 28px; border-radius: 6px; font-weight: 600; font-size: 14px; text-decoration: none; display: inline-flex; align-items: center; gap: 8px; transition: all 0.2s; border: 1px solid rgba(163, 230, 53, 0.5); }
        .btn-primary:hover { background: var(--lime-500); transform: translateY(-1px); box-shadow: 0 8px 24px rgba(163, 230, 53, 0.2); }
        .btn-secondary { background: transparent; color: var(--gray-400); padding: 14px 28px; border-radius: 6px; font-weight: 600; font-size: 14px; text-decoration: none; display: inline-flex; align-items: center; gap: 8px; border: 1px solid var(--gray-700); transition: all 0.2s; }
        .btn-secondary:hover { color: var(--gray-300); border-color: var(--gray-600); }
        .stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: var(--gray-800); border: 1px solid var(--gray-800); margin-bottom: 40px; }
        .stat-item { background: var(--gray-900); padding: 24px; text-align: center; }
        .stat-value { font-family: 'JetBrains Mono', monospace; font-size: 24px; color: var(--lime-400); font-weight: 600; margin-bottom: 4px; }
        .stat-label { font-size: 12px; color: var(--gray-600); text-transform: uppercase; letter-spacing: 1px; font-weight: 500; }
        .glass-panel { background: linear-gradient(180deg, rgba(31, 41, 55, 0.6) 0%, rgba(17, 24, 39, 0.8) 100%); border: 1px solid var(--gray-800); border-radius: 12px; padding: 32px; margin-bottom: 24px; backdrop-filter: blur(20px); position: relative; overflow: hidden; }
        .glass-panel::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 1px; background: linear-gradient(90deg, transparent, var(--lime-400), transparent); opacity: 0.3; }
        .section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--gray-800); }
        .section-title { font-size: 18px; font-weight: 600; color: var(--gray-200); display: flex; align-items: center; gap: 12px; }
        .section-icon { width: 8px; height: 8px; background: var(--lime-400); border-radius: 50%; }
        .section-subtitle { font-size: 13px; color: var(--gray-600); font-weight: 400; }
        .endpoint-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; background: var(--gray-800); border: 1px solid var(--gray-800); border-radius: 8px; overflow: hidden; }
        .endpoint-item { background: var(--gray-900); padding: 20px; border-right: 1px solid var(--gray-800); border-bottom: 1px solid var(--gray-800); transition: background 0.2s; }
        .endpoint-item:hover { background: rgba(31, 41, 55, 0.5); }
        .endpoint-item:last-child, .endpoint-item:nth-child(3n) { border-right: none; }
        .endpoint-header { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
        .method { font-family: 'JetBrains Mono', monospace; font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
        .method-get { background: rgba(34, 211, 238, 0.15); color: #22d3ee; }
        .method-post { background: rgba(163, 230, 53, 0.15); color: var(--lime-400); }
        .method-put { background: rgba(251, 191, 36, 0.15); color: #fbbf24; }
        .method-delete { background: rgba(248, 113, 113, 0.15); color: #f87171; }
        .path { font-family: 'JetBrains Mono', monospace; font-size: 13px; color: var(--gray-300); font-weight: 500; }
        .endpoint-desc { font-size: 13px; color: var(--gray-600); line-height: 1.5; }
        .auth-badge { font-family: 'JetBrains Mono', monospace; font-size: 10px; padding: 3px 8px; background: rgba(163, 230, 53, 0.1); color: var(--lime-400); border: 1px solid rgba(163, 230, 53, 0.3); border-radius: 3px; margin-left: auto; }
        .features-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
        .feature-item { background: rgba(31, 41, 55, 0.4); border: 1px solid var(--gray-800); border-radius: 8px; padding: 20px; display: flex; flex-direction: column; gap: 12px; }
        .feature-item:hover { border-color: var(--gray-700); background: rgba(31, 41, 55, 0.6); }
        .feature-header { display: flex; align-items: center; gap: 12px; }
        .feature-icon { width: 32px; height: 32px; background: linear-gradient(135deg, rgba(163, 230, 53, 0.2), rgba(34, 211, 238, 0.2)); border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 14px; }
        .feature-title { font-size: 14px; font-weight: 600; color: var(--gray-200); }
        .feature-desc { font-size: 12px; color: var(--gray-500); line-height: 1.5; padding-left: 44px; }
        .code-block { background: #0d1117; border: 1px solid var(--gray-800); border-radius: 8px; padding: 16px 20px; font-family: 'JetBrains Mono', monospace; font-size: 13px; color: var(--gray-400); overflow-x: auto; line-height: 1.7; position: relative; }
        .code-block::before { content: attr(data-lang); position: absolute; top: 8px; right: 12px; font-family: 'Inter', sans-serif; font-size: 10px; color: var(--gray-600); text-transform: uppercase; letter-spacing: 1px; }
        .code-highlight { color: var(--lime-400); }
        .code-comment { color: var(--gray-600); }
        .code-string { color: #22d3ee; }
        .code-keyword { color: #c084fc; }
        footer {
            border-top: 1px solid var(--gray-800);
            padding: 48px 0 32px;
            margin-top: 40px;
            background-color: #000818;
        }
        .footer-container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 0 24px;
        }
        .footer-grid {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 48px;
            align-items: center;
        }
        .footer-section {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .footer-section.center {
            align-items: center;
            text-align: center;
        }
        .footer-section.right {
            align-items: flex-end;
            text-align: right;
        }
        .footer-product-name {
            font-family: 'Inter', sans-serif;
            font-size: 20px;
            font-weight: 700;
            color: var(--gray-100);
            letter-spacing: -0.5px;
        }
        .footer-copyright {
            font-size: 13px;
            color: var(--gray-500);
            line-height: 1.5;
        }
        .footer-powered {
            font-size: 12px;
            color: var(--gray-600);
            margin-top: 4px;
        }
        .footer-link {
            color: var(--gray-400);
            text-decoration: none;
            font-size: 14px;
            font-weight: 500;
            transition: color 0.2s ease, opacity 0.2s ease;
        }
        .footer-link:hover {
            color: var(--lime-400);
            opacity: 1;
        }
        .footer-link-ghost {
            color: var(--gray-500);
            font-weight: 400;
        }
        .footer-link-ghost:hover {
            color: var(--lime-400);
        }
        @media (max-width: 768px) {
            .footer-grid {
                grid-template-columns: 1fr;
                gap: 24px;
                text-align: center;
            }
            .footer-section.right, .footer-section.center {
                align-items: center;
                text-align: center;
            }
            .footer-section.right {
                align-items: center;
            }
        }
        @media (max-width: 1024px) { .stats-grid { grid-template-columns: repeat(2, 1fr); } .endpoint-grid { grid-template-columns: repeat(2, 1fr); } .features-grid { grid-template-columns: repeat(2, 1fr); } }
        @media (max-width: 768px) { .hero-title { font-size: 36px; } .endpoint-grid { grid-template-columns: 1fr; } .features-grid { grid-template-columns: 1fr; } .nav-links { flex-wrap: wrap; gap: 16px; } }
    </style>
</head>
<body>
    <header>
        <div class="header-inner">
            <div class="logo-section">
                <div class="logo">
                    <img src="/static/vectra.png" alt="Vectra Logo" style="width: 32px; height: 32px; border-radius: 4px;">
                    <span>Vectra</span>
                </div>
                <span class="company-badge">by BearDefend</span>
            </div>
            <nav class="nav-links">
                <a href="#endpoints" class="nav-link">Endpoints</a>
                <a href="#features" class="nav-link">Features</a>
                <a href="#examples" class="nav-link">Examples</a>
            </nav>
        </div>
    </header>
    <div class="container">
        <section class="hero">
            <div class="hero-badge">High-Performance Semantic Search Engine</div>
            <h1 class="hero-title">Turning Product Data<br>into Customer Answers</h1>
            <p class="hero-subtitle">Enterprise-grade multi-tenant API powered Semantic Search. Built for <span style="color: var(--lime-400);">precision, speed, and reliability</span></p>
            <div class="hero-actions">
                <a href="/dashboard" class="btn-primary"><span>Login to Dashboard</span><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 8H13M13 8L9 4M13 8L9 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></a>
                <a href="/docs" class="btn-secondary"><span>Explore Swagger Docs</span></a>
            </div>
        </section>
        <div class="stats-grid">
            <div class="stat-item"><div class="stat-value">14</div><div class="stat-label">Endpoints</div></div>
            <div class="stat-item"><div class="stat-value">< 10ms</div><div class="stat-label">Avg Response</div></div>
            <div class="stat-item"><div class="stat-value">99.9%</div><div class="stat-label">Uptime</div></div>
            <div class="stat-item"><div class="stat-value">∞</div><div class="stat-label">Scalability</div></div>
        </div>
        <section class="glass-panel" id="endpoints">
            <div class="section-header"><div class="section-title"><span class="section-icon"></span>API Endpoints</div><div class="section-subtitle">Client Auth Required (except admin)</div></div>
            <div class="endpoint-grid">
                <div class="endpoint-item"><div class="endpoint-header"><span class="method method-post">POST</span><span class="path">/search</span><span class="auth-badge">CLIENT</span></div><p class="endpoint-desc">Semantic search with vector filtering and relevance scoring</p></div>
                <div class="endpoint-item"><div class="endpoint-header"><span class="method method-post">POST</span><span class="path">/product</span><span class="auth-badge">CLIENT</span></div><p class="endpoint-desc">Create single or bulk products in SQLite + ChromaDB</p></div>
                <div class="endpoint-item"><div class="endpoint-header"><span class="method method-put">PUT</span><span class="path">/editProduct/{id}</span><span class="auth-badge">CLIENT</span></div><p class="endpoint-desc">Partial update of existing product with auto-sync</p></div>
                <div class="endpoint-item"><div class="endpoint-header"><span class="method method-delete">DELETE</span><span class="path">/product/{id}</span><span class="auth-badge">CLIENT</span></div><p class="endpoint-desc">Remove single product from both databases</p></div>
                <div class="endpoint-item"><div class="endpoint-header"><span class="method method-delete">DELETE</span><span class="path">/products</span><span class="auth-badge">CLIENT</span></div><p class="endpoint-desc">Bulk delete multiple products by IDs</p></div>
                <div class="endpoint-item"><div class="endpoint-header"><span class="method method-delete">DELETE</span><span class="path">/products/all</span><span class="auth-badge">CLIENT</span></div><p class="endpoint-desc">Clear all products for client</p></div>
                <div class="endpoint-item"><div class="endpoint-header"><span class="method method-get">GET</span><span class="path">/getAllProducts</span><span class="auth-badge">CLIENT</span></div><p class="endpoint-desc">Retrieve complete product inventory</p></div>
                <div class="endpoint-item"><div class="endpoint-header"><span class="method method-post">POST</span><span class="path">/rebuild</span><span class="auth-badge">CLIENT</span></div><p class="endpoint-desc">Sync ChromaDB collection from SQLite</p></div>
                <div class="endpoint-item"><div class="endpoint-header"><span class="method method-get">GET</span><span class="path">/health</span><span class="auth-badge">CLIENT</span></div><p class="endpoint-desc">System health and product count check</p></div>
                <div class="endpoint-item"><div class="endpoint-header"><span class="method method-post">POST</span><span class="path">/admin/client</span><span class="auth-badge">ADMIN</span></div><p class="endpoint-desc">Create or delete client account</p></div>
                <div class="endpoint-item"><div class="endpoint-header"><span class="method method-get">GET</span><span class="path">/admin/status</span><span class="auth-badge">ADMIN</span></div><p class="endpoint-desc">Detailed system monitoring dashboard</p></div>
                <div class="endpoint-item"><div class="endpoint-header"><span class="method method-post">POST</span><span class="path">/fixDBissues</span><span class="auth-badge">ADMIN</span></div><p class="endpoint-desc">Automated database repair and sync</p></div>
            </div>
        </section>
        <section class="glass-panel" id="features">
            <div class="section-header"><div class="section-title"><span class="section-icon"></span>System Capabilities</div><div class="section-subtitle">Enterprise-Grade Architecture</div></div>
            <div class="features-grid">
                <div class="feature-item"><div class="feature-header"><div class="feature-icon">X</div><div class="feature-title">Multi-Tenant Isolation</div></div><p class="feature-desc">Complete data isolation with separate SQLite tables and ChromaDB collections for each client. Zero cross-contamination.</p></div>
                <div class="feature-item"><div class="feature-header"><div class="feature-icon">Y</div><div class="feature-title">Vector-Based Search</div></div><p class="feature-desc">High-performance semantic search powered by ChromaDB's vector embeddings with relevance scoring.</p></div>
                <div class="feature-item"><div class="feature-header"><div class="feature-icon">Z</div><div class="feature-title">Precision Filtering</div></div><p class="feature-desc">Multi-criteria filtering with price, category, brand, and use-case parameters for exact results.</p></div>
                <div class="feature-item"><div class="feature-header"><div class="feature-icon">A</div><div class="feature-title">Auto-Sync Engine</div></div><p class="feature-desc">Real-time synchronization between SQLite and ChromaDB ensures data consistency across all operations.</p></div>
                <div class="feature-item"><div class="feature-header"><div class="feature-icon">B</div><div class="feature-title">Monitoring Tools</div></div><p class="feature-desc">Built-in health checks and admin dashboard for system visibility and performance monitoring.</p></div>
                <div class="feature-item"><div class="feature-header"><div class="feature-icon">C</div><div class="feature-title">Docker-Ready</div></div><p class="feature-desc">Production-ready containerization with persistent volumes and health checks for reliable deployment.</p></div>
            </div>
        </section>
        <section class="glass-panel" id="examples">
            <div class="section-header"><div class="section-title"><span class="section-icon"></span>API Examples</div><div class="section-subtitle">Request & Response Samples</div></div>
            <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 24px;">
                <div>
                    <div class="code-block" data-lang="bash" style="margin-bottom: 16px;">
<span class="code-comment"># Search products by semantic query</span>
<span class="code-keyword">curl</span> -X POST <span class="code-string">"http://localhost:8000/search"</span> \
  -H <span class="code-string">"X-Client-ID: your-client-id"</span> \
  -H <span class="code-string">"X-Auth: your-auth-code"</span> \
  -H <span class="code-string">"Content-Type: application/json"</span> \
  -d <span class="code-string">'{"query": "wireless headphones", "max_price": 100}'</span>
                    </div>
                    <div class="code-block" data-lang="json" style="margin-bottom: 16px;">
<span class="code-comment">// Response</span>
{
  <span class="code-string">"success"</span>: true,
  <span class="code-string">"results"</span>: [
    {
      <span class="code-string">"product_id"</span>: <span class="code-string">"prod_001"</span>,
      <span class="code-string">"product_name"</span>: <span class="code-string">"Wireless Headphones Pro"</span>,
      <span class="code-string">"brand"</span>: <span class="code-string">"AudioTech"</span>,
      <span class="code-string">"price"</span>: 89.99,
      <span class="code-string">"score"</span>: 0.95
    }
  ]
}
                    </div>
                </div>
                <div>
                    <div class="code-block" data-lang="bash" style="margin-bottom: 16px;">
<span class="code-comment"># Add a new product</span>
<span class="code-keyword">curl</span> -X POST <span class="code-string">"http://localhost:8000/product"</span> \
  -H <span class="code-string">"X-Client-ID: your-client-id"</span> \
  -H <span class="code-string">"X-Auth: your-auth-code"</span> \
  -H <span class="code-string">"Content-Type: application/json"</span> \
  -d <span class="code-string">'{
    "product_id": "prod_002",
    "product_name": "Gaming Mouse",
    "category": "Electronics",
    "subcategory": "Computer Accessories",
    "brand": "GameMaster",
    "price": 49.99,
    "currency": "USD",
    "specs": "25000 DPI, Wireless, RGB",
    "description": "High-precision gaming mouse with customizable RGB",
    "use_case": "Gaming",
    "product_url": "https://example.com/gaming-mouse"
  }'</span>
                    </div>
                    <div class="code-block" data-lang="json" style="margin-bottom: 16px;">
<span class="code-comment">// Response</span>
{
  <span class="code-string">"success"</span>: true,
  <span class="code-string">"message"</span>: <span class="code-string">"Product added successfully"</span>,
  <span class="code-string">"product_id"</span>: <span class="code-string">"prod_002"</span>
}
                    </div>
                </div>
            </div>
        </section>
    </div>
    <footer>
        <div class="footer-container">
            <div class="footer-grid">
                <!-- Column 1: Product Name -->
                <div class="footer-section">
                    <div class="footer-product-name">Vectra</div>
                </div>
                
                <!-- Column 2: Copyright & Powered By -->
                <div class="footer-section center">
                    <div class="footer-copyright">
                        © 2026 Vectra. All rights reserved.
                    </div>
                    <div class="footer-powered">
                        Powered by <a href="https://www.beardefend.com" class="footer-link footer-link-ghost" target="_blank">BearDefend</a>
                    </div>
                </div>
                
                <!-- Column 3: Contact -->
                <div class="footer-section right">
                    <a href="mailto:defendbear@gmail.com" class="footer-link">defendbear@gmail.com</a>
                    <a href="https://www.beardefend.com" class="footer-link footer-link-ghost" target="_blank" style="margin-top: 4px;">www.beardefend.com</a>
                </div>
            </div>
        </div>
    </footer>
</body>
</html>
    """
    return HTMLResponse(content=html_content)

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

# Session validation helper
def validate_session(session_id: str):
    if not session_id or session_id not in active_sessions:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return active_sessions[session_id]

# Dashboard API endpoints that proxy to core endpoints with session validation
@app.get("/api/stats")
async def get_stats(session_id: str = None):
    validate_session(session_id)
    session = active_sessions[session_id]
    
    # For admin, we need to aggregate stats
    if session["user_type"] == "admin":
        # Use the admin status endpoint but return in stats format
        try:
            # Call admin status endpoint to get the data
            admin_status_result = await admin_status_api(session_id=session_id)
            
            # Extract the needed data
            return {
                "success": True,
                "stats": {
                    "products": admin_status_result["sqlite"]["total_products"],
                    "clients": admin_status_result["sqlite"]["total_clients"],
                    "collections": admin_status_result["chromadb"]["total_collections"],
                    "sync_rate": admin_status_result["sync_status"]["sync_rate"],
                    "sqlite_products": admin_status_result["sqlite"]["total_products"],
                    "chromadb_products": admin_status_result["chromadb"]["total_products"]
                }
            }
        except Exception as e:
            return {"success": False, "message": str(e)}
    else:
        # For client, get their health/status
        # Create a temporary ClientContext for the health endpoint
        client_id = session["client_id"]
        table_name = derive_table_name(client_id)
        collection_name = derive_collection_name(client_id)
        client = ClientContext(client_id, table_name, collection_name)
        
        # Call the existing health endpoint logic
        try:
            conn = get_sqlite_connection()
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {client.table_name}")
            sqlite_count = cursor.fetchone()[0]
            conn.close()

            collection = chroma_client.get_or_create_collection(name=client.collection_name)
            chromadb_count = collection.count()

            return {
                "success": True,
                "stats": {
                    "products": sqlite_count,
                    "clients": 1,
                    "collections": 1,
                    "sync_rate": "100%",
                    "sqlite_products": sqlite_count,
                    "chromadb_products": chromadb_count
                }
            }
        except Exception as e:
            return {"success": False, "message": str(e)}

@app.get("/api/products")
async def get_products(session_id: str = None, client_id: str = None):
    validate_session(session_id)
    session = active_sessions[session_id]
    
    # Determine which client's products to fetch
    target_client_id = client_id
    if session["user_type"] == "admin":
        if client_id:
            target_client_id = client_id
        else:
            # Admin without client_id returns empty list
            return {"success": True, "products": []}
    else:
        # Non-admins can only fetch their own products
        target_client_id = session["client_id"]
    
    # Create ClientContext for the target client
    table_name = derive_table_name(target_client_id)
    collection_name = derive_collection_name(target_client_id)
    client = ClientContext(target_client_id, table_name, collection_name)
    
    try:
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT 
                product_id, product_name, category, subcategory, brand, price, currency,
                specs, description, use_case, product_url, last_updated
            FROM {table_name}
            ORDER BY product_name
        """)
        
        products = cursor.fetchall()
        
        product_list = []
        for product in products:
            product_list.append({
                "product_id": product[0],
                "product_name": product[1],
                "category": product[2],
                "subcategory": product[3],
                "brand": product[4],
                "price": product[5],
                "currency": product[6],
                "specs": product[7],
                "description": product[8],
                "use_case": product[9],
                "product_url": product[10],
                "last_updated": product[11]
            })
        
        return {"success": True, "products": product_list}
    except Exception as e:
        return {"success": False, "message": str(e)}
    finally:
        conn.close()

@app.post("/api/search")
async def search_products_api(request: SearchRequest, session_id: str = None, client_id: str = None):
    validate_session(session_id)
    session = active_sessions[session_id]
    
    # Determine which client's database to search
    target_client_id = client_id
    if session["user_type"] == "admin":
        if client_id:
            target_client_id = client_id
        else:
            # Admin can search their own "database" (empty) or all clients
            # For now, require client_id for admin search
            raise HTTPException(status_code=400, detail="client_id is required for admin search")
    else:
        # Non-admins can only search their own database
        target_client_id = session["client_id"]
    
    # Create a temporary ClientContext
    table_name = derive_table_name(target_client_id)
    collection_name = derive_collection_name(target_client_id)
    client = ClientContext(target_client_id, table_name, collection_name)
    
    # Call the search logic from the core endpoint
    try:
        collection = chroma_client.get_or_create_collection(name=client.collection_name)

        # Build where clause dynamically
        filters = []
        
        if request.max_price is not None:
            filters.append({"price": {"$lte": request.max_price}})
        if request.min_price is not None:
            filters.append({"price": {"$gte": request.min_price}})
        if request.category:
            filters.append({"category": request.category})
        if request.brand:
            filters.append({"brand": request.brand})
        if request.use_case:
            filters.append({"use_case": request.use_case})

        # Build the where clause
        where_clause = {}
        if len(filters) == 1:
            where_clause = filters[0]
        elif len(filters) > 1:
            where_clause = {"$and": filters}

        # Perform search
        if where_clause:
            results = collection.query(
                query_texts=request.query,
                where=where_clause,
                n_results=request.max_result
            )
        else:
            results = collection.query(
                query_texts=request.query,
                n_results=request.max_result
            )

        # Process results
        processed_results = []
        if results["documents"] and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                metadata = results["metadatas"][0][i]
                freshness_warning = utils.check_freshness(
                    metadata["last_updated"],
                    config.FRESHNESS_THRESHOLD_DAYS
                )

                processed_results.append({
                    "product_id": metadata["product_id"],
                    "product_name": doc,
                    "category": metadata["category"],
                    "subcategory": metadata["subcategory"],
                    "brand": metadata["brand"],
                    "price": metadata["price"],
                    "currency": metadata["currency"],
                    "use_case": metadata["use_case"],
                    "product_url": metadata["product_url"],
                    "freshness_warning": freshness_warning,
                    "score": results["distances"][0][i] if results["distances"] else None
                })

        return {
            "success": True,
            "message": "Search completed successfully",
            "results": processed_results,
            "count": len(processed_results)
        }
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.post("/api/product")
async def add_product_api(product: Product, session_id: str = None, client_id: str = None):
    validate_session(session_id)
    session = active_sessions[session_id]
    
    # Determine which client's database to add the product to
    target_client_id = client_id
    if session["user_type"] == "admin":
        if not client_id:
            raise HTTPException(status_code=400, detail="client_id is required for admin add product")
        target_client_id = client_id
    else:
        # Non-admins can only add to their own database
        target_client_id = session["client_id"]
    
    table_name = derive_table_name(target_client_id)
    collection_name = derive_collection_name(target_client_id)
    client = ClientContext(target_client_id, table_name, collection_name)
    
    # Reuse the existing /product endpoint logic
    try:
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        
        # Check if product already exists
        cursor.execute(
            f"SELECT product_id FROM {client.table_name} WHERE product_id = ?",
            (product.product_id,)
        )
        if cursor.fetchone():
            return {"success": False, "message": "Product already exists"}
        
        # Insert into SQLite
        cursor.execute(f"""
            INSERT INTO {client.table_name}
            (product_id, product_name, category, subcategory, brand, price, currency,
             specs, description, use_case, product_url, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            product.product_id, product.product_name, product.category,
            product.subcategory, product.brand, product.price, product.currency,
            product.specs, product.description, product.use_case,
            product.product_url, product.last_updated
        ))
        
        # Insert into ChromaDB
        collection = chroma_client.get_or_create_collection(name=client.collection_name)
        document_string = utils.build_document_string(product.dict())
        
        collection.add(
            ids=[product.product_id],
            documents=[document_string],
            metadatas=[{
                "product_id": product.product_id,
                "category": product.category,
                "subcategory": product.subcategory,
                "brand": product.brand,
                "price": product.price,
                "currency": product.currency,
                "use_case": product.use_case,
                "product_url": product.product_url,
                "last_updated": product.last_updated
            }]
        )
        
        conn.commit()
        conn.close()
        
        return {"success": True, "message": "Product added successfully"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.post("/api/bulk-upload")
async def bulk_upload_csv(
    session_id: str = Form(...),
    client_id: Optional[str] = Form(None),
    mode: str = Form("append"),  # "append" or "replace"
    file: UploadFile = File(...)
):
    """
    Bulk upload products from a CSV file.
    - Validates CSV using convert_utils.py logic
    - Mode: "append" (add to existing) or "replace" (clear existing first)
    - Validates each row before processing
    - Returns progress updates
    - Calls /rebuild endpoint after successful upload
    """
    import convert_utils
    
    validate_session(session_id)
    session = active_sessions[session_id]
    
    # Determine target client
    target_client_id = client_id
    if session["user_type"] == "admin":
        if not client_id:
            raise HTTPException(status_code=400, detail="client_id is required for admin bulk upload")
        target_client_id = client_id
    else:
        target_client_id = session["client_id"]
    
    # Validate mode
    if mode not in ["append", "replace"]:
        raise HTTPException(status_code=400, detail="Mode must be 'append' or 'replace'")
    
    if not file:
        raise HTTPException(status_code=400, detail="No file uploaded")
    
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")
    
    table_name = derive_table_name(target_client_id)
    collection_name = derive_collection_name(target_client_id)
    client = ClientContext(target_client_id, table_name, collection_name)
    
    conn = None
    temp_csv_path = None
    
    try:
        # Save uploaded file to temp location
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            content = await file.read()
            tmp.write(content)
            temp_csv_path = tmp.name
        
        # Validate and read CSV using convert_utils logic
        products = []
        with open(temp_csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            # Check required fields
            csv_fields = reader.fieldnames
            expected_fields = convert_utils.EXPECTED_FIELDS
            missing_fields = set(expected_fields) - set(csv_fields)
            if missing_fields:
                raise HTTPException(
                    status_code=400, 
                    detail=f"CSV file is missing required fields: {missing_fields}"
                )
            
            for row_num, row in enumerate(reader, start=2):
                product = {field: row[field] for field in expected_fields}
                
                # Validate product data
                errors = convert_utils.validate_product_data(product)
                if errors:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Validation error on row {row_num}: {errors}"
                    )
                
                # Convert price to float
                try:
                    product['price'] = float(product['price'])
                except (ValueError, TypeError):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid price on row {row_num}: {product['price']}"
                    )
                
                products.append(product)
        
        # Connect to database
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        
        # Handle replace mode
        if mode == "replace":
            # Clear existing products
            cursor.execute(f"DELETE FROM {client.table_name}")
            
            # Clear ChromaDB collection
            collection = chroma_client.get_or_create_collection(name=client.collection_name)
            existing_ids = collection.get().get('ids', [])
            if existing_ids:
                collection.delete(ids=existing_ids)
        
        # Insert products into SQLite and ChromaDB
        inserted_count = 0
        skipped_count = 0
        skipped_products = []
        
        for product in products:
            # Check if product already exists (for append mode)
            if mode == "append":
                cursor.execute(
                    f"SELECT product_id FROM {client.table_name} WHERE product_id = ?",
                    (product['product_id'],)
                )
                if cursor.fetchone():
                    skipped_count += 1
                    skipped_products.append({
                        "product_id": product['product_id'],
                        "reason": "Product already exists"
                    })
                    continue
            
            # Insert into SQLite
            cursor.execute(f"""
                INSERT INTO {client.table_name}
                (product_id, product_name, category, subcategory, brand, price, currency,
                 specs, description, use_case, product_url, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                product['product_id'], product['product_name'], product['category'],
                product['subcategory'], product['brand'], product['price'], product['currency'],
                product['specs'], product['description'], product['use_case'],
                product['product_url'], product['last_updated']
            ))
            
            # Insert into ChromaDB
            collection = chroma_client.get_or_create_collection(name=client.collection_name)
            document_string = utils.build_document_string(product)
            
            collection.add(
                ids=[product['product_id']],
                documents=[document_string],
                metadatas=[{
                    "product_id": product['product_id'],
                    "category": product['category'],
                    "subcategory": product['subcategory'],
                    "brand": product['brand'],
                    "price": product['price'],
                    "currency": product['currency'],
                    "use_case": product['use_case'],
                    "product_url": product['product_url'],
                    "last_updated": product['last_updated']
                }]
            )
            
            inserted_count += 1
        
        conn.commit()
        
        # Call rebuild endpoint to ensure sync
        # We'll call the helper function directly to avoid another HTTP request
        rebuild_result = rebuild_collection_helper(client)
        
        return {
            "success": True,
            "message": f"Bulk Import completed. {inserted_count} products inserted, {skipped_count} skipped.",
            "inserted_count": inserted_count,
            "skipped_count": skipped_count,
            "skipped_products": skipped_products,
            "rebuild_result": rebuild_result
        }
    
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Bulk upload failed: {str(e)}"
        )
    finally:
        if conn:
            conn.close()
        if temp_csv_path and os.path.exists(temp_csv_path):
            os.unlink(temp_csv_path)

@app.delete("/api/product/{product_id}")
async def delete_product_api(product_id: str, session_id: str = None, client_id: str = None):
    validate_session(session_id)
    session = active_sessions[session_id]
    
    # Determine which client's product to delete
    target_client_id = client_id
    if session["user_type"] == "admin":
        if not client_id:
            raise HTTPException(status_code=400, detail="client_id is required for admin delete product")
        target_client_id = client_id
    else:
        # Non-admins can only delete their own products
        target_client_id = session["client_id"]
    
    table_name = derive_table_name(target_client_id)
    collection_name = derive_collection_name(target_client_id)
    client = ClientContext(target_client_id, table_name, collection_name)
    
    # Reuse the existing /product/{id} endpoint logic
    try:
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        
        # Check if product exists
        cursor.execute(
            f"SELECT product_id FROM {client.table_name} WHERE product_id = ?",
            (product_id,)
        )
        if not cursor.fetchone():
            return {"success": False, "message": "Product not found"}
        
        # Delete from SQLite
        cursor.execute(
            f"DELETE FROM {client.table_name} WHERE product_id = ?",
            (product_id,)
        )
        
        # Delete from ChromaDB
        collection = chroma_client.get_or_create_collection(name=client.collection_name)
        collection.delete(ids=[product_id])
        
        conn.commit()
        conn.close()
        
        return {"success": True, "message": "Product deleted successfully"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.put("/api/admin/product/{product_id}")
async def admin_edit_product_api(product_id: str, request: EditProductRequest, session_id: str = None, client_id: str = None):
    """
    Admin endpoint to edit a product for a specific client.
    """
    validate_session(session_id)
    session = active_sessions[session_id]
    
    if session["user_type"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    if not client_id:
        raise HTTPException(status_code=400, detail="client_id is required for admin edit product")
    
    # Create ClientContext for the target client
    table_name = derive_table_name(client_id)
    collection_name = derive_collection_name(client_id)
    client = ClientContext(client_id, table_name, collection_name)
    
    # Reuse the edit product logic from the core endpoint
    try:
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        
        # Check if product exists
        cursor.execute(
            f"SELECT product_id FROM {client.table_name} WHERE product_id = ?",
            (product_id,)
        )
        if not cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product {product_id} not found"
            )
        
        # Build update fields and values
        update_fields = []
        update_values = []
        
        if request.product_name is not None:
            update_fields.append("product_name = ?")
            update_values.append(request.product_name)
        
        if request.category is not None:
            update_fields.append("category = ?")
            update_values.append(request.category)
        
        if request.subcategory is not None:
            update_fields.append("subcategory = ?")
            update_values.append(request.subcategory)
        
        if request.brand is not None:
            update_fields.append("brand = ?")
            update_values.append(request.brand)
        
        if request.price is not None:
            update_fields.append("price = ?")
            update_values.append(request.price)
        
        if request.currency is not None:
            update_fields.append("currency = ?")
            update_values.append(request.currency)
        
        if request.specs is not None:
            update_fields.append("specs = ?")
            update_values.append(request.specs)
        
        if request.description is not None:
            update_fields.append("description = ?")
            update_values.append(request.description)
        
        if request.use_case is not None:
            update_fields.append("use_case = ?")
            update_values.append(request.use_case)
        
        if request.product_url is not None:
            update_fields.append("product_url = ?")
            update_values.append(request.product_url)
        
        if request.last_updated is not None:
            update_fields.append("last_updated = ?")
            update_values.append(request.last_updated)
        
        if not update_fields:
            return {"success": True, "message": "No fields to update"}
        
        # Add product_id to values
        update_values.append(product_id)
        
        # Execute update
        cursor.execute(f"""
            UPDATE {client.table_name}
            SET {', '.join(update_fields)}
            WHERE product_id = ?
        """, update_values)
        
        # Update ChromaDB
        collection = chroma_client.get_or_create_collection(name=client.collection_name)
        
        # Get updated product data
        cursor.execute(f"""
            SELECT product_id, product_name, category, subcategory, brand, price, currency,
                   specs, description, use_case, product_url, last_updated
            FROM {client.table_name}
            WHERE product_id = ?
        """, (product_id,))
        
        product_data = cursor.fetchone()
        
        if product_data:
            (
                product_id, product_name, category, subcategory, brand,
                price, currency, specs, description, use_case, product_url, last_updated
            ) = product_data
            
            # Build document string
            product_dict = {
                'product_name': product_name,
                'brand': brand,
                'category': category,
                'subcategory': subcategory,
                'specs': specs,
                'description': description,
                'use_case': use_case,
                'price': price,
                'currency': currency
            }
            document_string = utils.build_document_string(product_dict)
            
            # Update ChromaDB
            collection.update(
                ids=[product_id],
                documents=[document_string],
                metadatas=[{
                    'product_id': product_id,
                    'category': category,
                    'subcategory': subcategory,
                    'brand': brand,
                    'price': float(price),
                    'currency': currency,
                    'use_case': use_case,
                    'product_url': product_url,
                    'last_updated': last_updated
                }]
            )
        
        conn.commit()
        conn.close()
        
        return {"success": True, "message": "Product updated successfully"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.post("/api/admin/client")
async def admin_create_client_api(request: AdminClientRequest, session_id: str = None):
    validate_session(session_id)
    session = active_sessions[session_id]
    
    if session["user_type"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Use the helper function directly
    return await manage_client_helper(request)

class RebuildRequest(BaseModel):
    client_id: Optional[str] = None

@app.post("/api/rebuild")
async def rebuild_api(request: RebuildRequest, session_id: str = None, client_id: str = None):
    """
    Dashboard API endpoint to rebuild a client's ChromaDB collection.
    Admins can rebuild any client's collection by specifying client_id.
    Non-admins can only rebuild their own collection.
    """
    validate_session(session_id)
    session = active_sessions[session_id]
    
    # Determine which client's database to rebuild
    # Accept client_id from query parameter or request body
    target_client_id = client_id or request.client_id
    if session["user_type"] == "admin":
        if not target_client_id:
            raise HTTPException(status_code=400, detail="client_id is required for admin rebuild")
    else:
        # Non-admins can only rebuild their own database
        target_client_id = session["client_id"]
    
    # Create ClientContext for the target client
    table_name = derive_table_name(target_client_id)
    collection_name = derive_collection_name(target_client_id)
    client = ClientContext(target_client_id, table_name, collection_name)
    
    # Use the helper function to rebuild the collection
    return rebuild_collection_helper(client)

@app.post("/api/fixDBissues")
async def fix_db_issues_api(session_id: str = None):
    validate_session(session_id)
    session = active_sessions[session_id]
    
    if session["user_type"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Reuse the existing /fixDBissues endpoint logic
    return await fix_db_issues(admin=None)

@app.delete("/api/products/all")
async def clear_all_products_api(session_id: str = None, request: ClearAllProductsRequest = None, client_id: str = None):
    validate_session(session_id)
    session = active_sessions[session_id]
    
    # Determine which client's products to clear
    target_client_id = client_id
    if session["user_type"] == "admin":
        if not client_id:
            raise HTTPException(status_code=400, detail="client_id is required for admin clear all products")
        target_client_id = client_id
        # For admin, auth code is optional (session is sufficient)
    else:
        # Non-admins can only clear their own products
        target_client_id = session["client_id"]
        if not request or not request.auth_code:
            raise HTTPException(status_code=400, detail="Auth code required for confirmation")
    
    table_name = derive_table_name(target_client_id)
    collection_name = derive_collection_name(target_client_id)
    client = ClientContext(target_client_id, table_name, collection_name)
    
    # Verify auth code matches the client's auth code (only for non-admin users)
    if session["user_type"] != "admin":
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT auth_code FROM clients WHERE client_id = ?", (target_client_id,))
        result = cursor.fetchone()
        conn.close()
        
        if not result or result[0] != request.auth_code:
            raise HTTPException(status_code=403, detail="Invalid auth code")
    
    # Reuse the existing /products/all endpoint logic
    try:
        conn = get_sqlite_connection()
        cursor = conn.cursor()

        cursor.execute(f"DELETE FROM {client.table_name}")
        
        collection = chroma_client.get_or_create_collection(name=client.collection_name)
        existing_ids = collection.get().get('ids', [])
        if existing_ids:
            collection.delete(ids=existing_ids)

        conn.commit()
        conn.close()

        return {"success": True, "message": "All products cleared"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/health")
async def health_api(session_id: str = None):
    validate_session(session_id)
    session = active_sessions[session_id]
    
    # Reuse the existing /health endpoint logic
    client_id = session["client_id"]
    table_name = derive_table_name(client_id)
    collection_name = derive_collection_name(client_id)
    client = ClientContext(client_id, table_name, collection_name)
    
    try:
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {client.table_name}")
        sqlite_count = cursor.fetchone()[0]
        conn.close()

        collection = chroma_client.get_or_create_collection(name=client.collection_name)
        chromadb_count = collection.count()

        return {
            "status": "healthy",
            "total_sqlite_products": sqlite_count,
            "total_chromadb_products": chromadb_count
        }
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/admin/status")
async def admin_status_api(session_id: str = None):
    validate_session(session_id)
    session = active_sessions[session_id]
    
    if session["user_type"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Replicate admin_status logic without the admin dependency
    conn = None
    try:
        # Connect to SQLite
        conn = get_sqlite_connection()
        cursor = conn.cursor()
        
        # Get all clients from registry
        cursor.execute("SELECT client_id, table_name, collection_name FROM clients ORDER BY client_id")
        clients_data = cursor.fetchall()
        
        # ChromaDB status
        chromadb_collections = []
        chromadb_total_products = 0
        chromadb_total_collections = 0
        
        try:
            collections = chroma_client.list_collections()
            for collection in collections:
                try:
                    count = collection.count()
                    chromadb_total_collections += 1
                    chromadb_total_products += count
                    chromadb_collections.append({
                        "name": collection.name,
                        "product_count": count
                    })
                except Exception as e:
                    chromadb_collections.append({
                        "name": collection.name,
                        "product_count": 0,
                        "error": str(e)
                    })
        except Exception as e:
            chromadb_collections.append({
                "error": f"Failed to list collections: {str(e)}"
            })
        
        # SQLite status
        sqlite_clients = []
        sqlite_total_products = 0
        
        for client_id, table_name, collection_name in clients_data:
            try:
                # Get product count for this client's table
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                product_count = cursor.fetchone()[0]
                sqlite_total_products += product_count
                
                sqlite_clients.append({
                    "client_id": client_id,
                    "table_name": table_name,
                    "collection_name": collection_name,
                    "product_count": product_count
                })
            except Exception as e:
                sqlite_clients.append({
                    "client_id": client_id,
                    "table_name": table_name,
                    "collection_name": collection_name,
                    "product_count": 0,
                    "error": str(e)
                })
        
        # Check sync status between SQLite and ChromaDB
        sync_issues = []
        synced_clients = 0
        unsynced_clients = 0
        
        for client_info in sqlite_clients:
            client_id = client_info["client_id"]
            sqlite_count = client_info["product_count"]
            
            # Find corresponding ChromaDB collection
            collection_name = client_info["collection_name"]
            chromadb_collection = None
            for collection in chromadb_collections:
                if collection["name"] == collection_name:
                    chromadb_collection = collection
                    break
            
            if chromadb_collection:
                chromadb_count = chromadb_collection["product_count"]
                if sqlite_count == chromadb_count:
                    synced_clients += 1
                else:
                    unsynced_clients += 1
                    sync_issues.append({
                        "client_id": client_id,
                        "sqlite_count": sqlite_count,
                        "chromadb_count": chromadb_count,
                        "difference": sqlite_count - chromadb_count
                    })
            else:
                unsynced_clients += 1
                sync_issues.append({
                    "client_id": client_id,
                    "sqlite_count": sqlite_count,
                    "chromadb_count": 0,
                    "difference": sqlite_count,
                    "issue": "ChromaDB collection not found"
                })
        
        conn.close()
        
        total_clients = len(sqlite_clients)
        sync_rate = f"{(synced_clients / total_clients * 100) if total_clients > 0 else 100:.1f}%" if total_clients > 0 else "100%"
        
        return {
            "success": True,
            "message": "ChromaDB status retrieved successfully. Overall health: " + ("healthy" if unsynced_clients == 0 else "warning"),
            "chromadb": {
                "total_collections": chromadb_total_collections,
                "total_products": chromadb_total_products,
                "collections": chromadb_collections,
                "orphaned_collections": [],  # Would need to implement orphan detection
                "health": "healthy" if unsynced_clients == 0 else "warning"
            },
            "sqlite": {
                "total_clients": total_clients,
                "total_products": sqlite_total_products,
                "clients": sqlite_clients,
                "health": "healthy"
            },
            "clients": sqlite_clients,
            "sync_status": {
                "total_clients": total_clients,
                "synced_clients": synced_clients,
                "unsynced_clients": unsynced_clients,
                "sync_rate": sync_rate,
                "issues": sync_issues,
                "health": "healthy" if unsynced_clients == 0 else "warning"
            }
        }
    except Exception as e:
        return {"success": False, "message": str(e)}
    finally:
        if conn:
            conn.close()


@app.get("/docs", include_in_schema=False)
async def docs_redirect():
    if config.REDIRECT_DOCS_TO_DASHBOARD:
        return RedirectResponse(url="/dashboard")
    return RedirectResponse(url="/docs")

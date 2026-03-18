# auth.py
# Authentication dependencies for client and admin endpoints.

import hashlib
import re
from fastapi import Depends, HTTPException, Header, status

import config


def sanitize_identifier(identifier: str) -> str:
    """
    Sanitizes an identifier to be safe for use in table and collection names.
    Only allows alphanumeric characters and underscores.
    """
    return re.sub(r'[^a-zA-Z0-9_]', '_', identifier)


def derive_table_name(client_id: str) -> str:
    """
    Derives the SQLite table name for a client from their client_id.
    Uses a hash to ensure uniqueness and consistency.
    """
    sanitized = sanitize_identifier(client_id)
    hash_suffix = hashlib.sha256(sanitized.encode()).hexdigest()[:8]
    return f"products_{sanitized}_{hash_suffix}"


def derive_collection_name(client_id: str) -> str:
    """
    Derives the ChromaDB collection name for a client from their client_id.
    Uses a hash to ensure uniqueness and consistency.
    """
    sanitized = sanitize_identifier(client_id)
    hash_suffix = hashlib.sha256(sanitized.encode()).hexdigest()[:8]
    return f"client_{sanitized}_{hash_suffix}"


class ClientContext:
    def __init__(self, client_id: str, table_name: str, collection_name: str):
        self.client_id = client_id
        self.table_name = table_name
        self.collection_name = collection_name


async def client_dependency(
    x_client_id: str = Header(..., alias="X-Client-ID"),
    x_auth: str = Header(..., alias="X-Auth")
) -> ClientContext:
    """
    Dependency to authenticate and validate client requests.
    Reads X-Client-ID and X-Auth headers.
    Looks up the clients registry table to validate the auth_code.
    If invalid or missing, returns 401.
    """
    if not x_client_id or not x_auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing client ID or authentication header"
        )

    # Import here to avoid circular dependency at module level
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

        return ClientContext(
            client_id=x_client_id,
            table_name=table_name,
            collection_name=collection_name
        )
    finally:
        conn.close()


async def admin_dependency(
    x_admin_key: str = Header(..., alias="X-Admin-Key")
) -> None:
    """
    Dependency to authenticate admin requests.
    Reads X-Admin-Key header and validates against config.ADMIN_API_KEY.
    """
    if not x_admin_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing admin key"
        )

    if x_admin_key != config.ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin key"
        )

    return None

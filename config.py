# config.py
# Configuration settings for the multi-tenant ChromaDB middleware application.

import os

# Admin API key for administrative endpoints
# Can be set via ADMIN_API_KEY environment variable
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "Vectra@Beardefend_2026")

# Freshness threshold in days (default: 7 days)
# Can be set via FRESHNESS_THRESHOLD_DAYS environment variable
FRESHNESS_THRESHOLD_DAYS = int(os.getenv("FRESHNESS_THRESHOLD_DAYS", "7"))

# SQLite database path
# Can be set via SQLITE_DB_PATH environment variable
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", "clients.db")

# ChromaDB persistent storage path
# Can be set via CHROMADB_PATH environment variable
CHROMADB_PATH = os.getenv("CHROMADB_PATH", "./chroma_data")

# Server port
# Can be set via PORT environment variable
PORT = int(os.getenv("PORT", "8000"))

# Server host
# Can be set via HOST environment variable (default: 0.0.0.0 for Docker)
HOST = os.getenv("HOST", "0.0.0.0")

# Redirect /docs to /dashboard (default: true)
# Can be set via REDIRECT_DOCS_TO_DASHBOARD environment variable
REDIRECT_DOCS_TO_DASHBOARD = os.getenv("REDIRECT_DOCS_TO_DASHBOARD", "true").lower() == "true"
# REDIRECT_DOCS_TO_DASHBOARD = os.getenv("REDIRECT_DOCS_TO_DASHBOARD", "false").lower() == "true"

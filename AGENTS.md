# Project Guide for AI Agents

## Project Overview
Vectra is a production-ready multi-tenant semantic search API system built with FastAPI, ChromaDB, and SQLite.

## Key Technologies
- **FastAPI**: Web framework
- **ChromaDB**: Vector database for semantic search
- **SQLite**: Relational database for product storage
- **Jinja2**: Template engine for dashboard

## Development Commands

### Run the application
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Install dependencies
```bash
pip install -r requirements.txt
```

### Lint and typecheck
```bash
# Note: Type checking may show errors due to ChromaDB type hints
# The application runs correctly despite these warnings
python -m py_compile main.py
```

## Project Structure
- `main.py` - Main application file with all API endpoints
- `config.py` - Configuration settings
- `auth.py` - Authentication dependencies
- `utils.py` - Utility functions
- `templates/dashboard.html` - Dashboard UI
- `static/` - Static files (favicon, logo)
- `data/` - Persistent data storage

## API Endpoints
- `/dashboard` - Interactive dashboard UI
- `/api/login` - Session-based login
- `/api/logout` - Session logout
- `/api/stats` - System statistics
- `/api/products` - Get products (session-based)
- `/api/search` - Search products (session-based)
- `/api/product` - Add product (session-based)
- Original header-based endpoints also available

## Authentication
- **Admin**: Client ID "admin" with ADMIN_API_KEY
- **Clients**: Use client-specific credentials
- **Session-based**: Dashboard uses session tokens

## Testing
1. Start server: `uvicorn main:app --host 0.0.0.0 --port 8000`
2. Access dashboard: `http://localhost:8000/dashboard`
3. Login with admin credentials
4. Test API endpoints through dashboard

## Docker Deployment
```bash
docker-compose up -d
```

## Important Notes
- The `/docs` endpoint redirects to `/dashboard`
- Technology stack details are hidden from frontend
- All data is persisted in `./data` directory

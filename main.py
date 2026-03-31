"""
PRLens — Brutally honest AI code reviewer for GitHub.

Run with:
    uvicorn main:app --reload --port 8000

Or:
    uvicorn app.main:app --reload --port 8000
"""

from app.main import app  # noqa: F401
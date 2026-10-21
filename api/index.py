"""Vercel entry point: serves the FastAPI app (app/api/main.py) as a Python function.

vercel.json sends every /api/* request here; the React build in app/web/dist is served
as static files. Runs without torch: our model's forecast uses the numpy weights.
"""

from app.api.main import app  # noqa: F401

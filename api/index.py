"""Vercel entry point: serves the Django WSGI app as a serverless function."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from config.wsgi import application as app  # noqa: E402

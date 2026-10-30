"""Checkout paths shared by tests and recorders; consumers never depend on their own depth."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / 'backend'
TESTS = BACKEND / 'tests'
FIXTURES = TESTS / 'fixtures'
FRONTEND_APP = REPO / 'frontend' / 'app'
FRONTEND_FIXTURES = FRONTEND_APP / 'tests' / 'fixtures'
DASHBOARD = BACKEND / 'dashboard.py'
RELEASE_TOOLS = REPO / 'tools'
BACKEND_TOOLS = BACKEND / 'tools'

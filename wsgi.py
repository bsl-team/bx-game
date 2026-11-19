"""WSGI entry point for production servers (gunicorn -w 1 --threads 32 wsgi:app)."""
from app import create_app, load_config

app = create_app(load_config())

"""WSGI entry point for production servers (gunicorn -w 1 --threads 16 wsgi:app)."""
from app import create_app, load_config

app = create_app(load_config())

"""Entry point for Flask, Gunicorn and Celery.

flask --app wsgi run
gunicorn wsgi:app
celery -A wsgi:celery_app worker
"""

from reelforge_api import create_app

app = create_app()
celery_app = app.extensions["celery"]

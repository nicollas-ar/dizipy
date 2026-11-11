"""Render entrypoint. One Gunicorn worker; no preload/forked scheduler."""
import logging
import threading

from app import create_app
from runner import tick

app = create_app()


def schedule():
    while True:
        try:
            tick(app)
        except Exception:
            # Never log an API payload, credential or connection URL.
            logging.getLogger(__name__).error('Falha na agenda; confira Atividade e a conexão do banco.')
        threading.Event().wait(15)


threading.Thread(target=schedule, name='dizipy-agenda', daemon=True).start()

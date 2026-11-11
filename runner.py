"""Processamento periódico, com exclusão entre instâncias e filas duráveis."""
import time
from datetime import timedelta

import messaging
import services
from storage import db, now


def tick(app):
    with app.app_context():
        until=time.time()+900
        with db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row=connection.execute("SELECT until_time FROM worker_lock WHERE name='agenda'").fetchone()
            if row and row['until_time']>time.time():
                return
            connection.execute("INSERT INTO worker_lock VALUES('agenda',?) ON CONFLICT(name) DO UPDATE SET until_time=excluded.until_time",(until,))
        try:
            with db():
                db().execute("UPDATE message SET status='unknown',error='O processo foi interrompido durante o envio. Confira a entrega antes de reenviar.' WHERE status='sending' AND created_at<?",((now()-timedelta(minutes=10)).isoformat(),))
            services.work_events()
            services.demo_cycles()
            messaging.enqueue_reminders()
            messaging.send_queue()
        finally:
            with db():
                db().execute("DELETE FROM worker_lock WHERE name='agenda' AND until_time=?",(until,))

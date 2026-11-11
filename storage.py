"""Persistência local, migrações e configurações protegidas do Dizipy."""
import os
import secrets
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from flask import current_app, g
from database import Postgres

ZONE = ZoneInfo('America/Sao_Paulo')


def now():
    return datetime.now(ZONE)


def stamp():
    return now().isoformat()


def durable_secret(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(value)
        name = temporary.name
    try:
        os.chmod(name, 0o600)
        try:
            os.link(name, path)
        except FileExistsError:
            pass
        return path.read_bytes()
    finally:
        os.unlink(name)


def db():
    if 'db' not in g:
        if current_app.config.get('DATABASE_URL'):
            g.db = Postgres(current_app.config['DATABASE_URL'])
            return g.db
        g.db = sqlite3.connect(current_app.config['DATABASE'], timeout=20)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


def close_db(_error=None):
    connection = g.pop('db', None)
    if connection is not None:
        connection.close()


def secret_box():
    return Fernet(current_app.config['LOCAL_ENCRYPTION_KEY'])


def setting(name, default='', parish_id=0):
    row = db().execute('SELECT value, secret FROM setting WHERE parish_id=? AND name=?', (parish_id, name)).fetchone()
    if not row:
        return default
    return secret_box().decrypt(row['value'].encode()).decode() if row['secret'] else row['value']


def put_setting(name, value, parish_id=0, secret=False):
    stored = secret_box().encrypt(str(value).encode()).decode() if secret and value else str(value)
    db().execute('INSERT INTO setting(parish_id,name,value,secret) VALUES(?,?,?,?) ON CONFLICT(parish_id,name) DO UPDATE SET value=excluded.value,secret=excluded.secret', (parish_id, name, stored, int(secret and bool(value))))


def audit(parish_id, action, detail='', user_id=None):
    db().execute('INSERT INTO audit(parish_id,user_id,action,detail,created_at) VALUES(?,?,?,?,?)', (parish_id, user_id, action, detail[:500], stamp()))


def add_columns(table, columns):
    if current_app.config.get('DATABASE_URL'):
        existing = {row['column_name'] for row in db().execute('SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?',(table,))}
    else:
        existing = {row['name'] for row in db().execute(f'PRAGMA table_info({table})')}
    for name, declaration in columns.items():
        if name not in existing:
            db().execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')


def initialize():
    connection = db()
    postgres = bool(current_app.config.get('DATABASE_URL'))
    if not postgres:
        connection.execute('PRAGMA journal_mode=WAL')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        legacy = connection.execute("SELECT to_regclass('asaas_pix') AS name").fetchone()['name'] if postgres else connection.execute("SELECT 1 FROM sqlite_master WHERE name='asaas_pix'").fetchone()
        if legacy:
            connection.execute('ALTER TABLE asaas_pix RENAME TO asaas_payment')
    schema = '''
        CREATE TABLE IF NOT EXISTS parish(id INTEGER PRIMARY KEY, public_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, methods TEXT NOT NULL, reminder_day INTEGER NOT NULL DEFAULT 10, reminder_time TEXT NOT NULL DEFAULT '09:00');
        CREATE TABLE IF NOT EXISTS donation(id TEXT PRIMARY KEY, parish_id INTEGER NOT NULL REFERENCES parish(id), amount INTEGER NOT NULL CHECK(amount>0), platform_fee INTEGER NOT NULL, method TEXT NOT NULL, created_at TEXT NOT NULL, paid_at TEXT);
        CREATE TABLE IF NOT EXISTS asaas_payment(donation_id TEXT PRIMARY KEY REFERENCES donation(id), payment_id TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS asaas_webhook_event(id TEXT PRIMARY KEY, donation_id TEXT NOT NULL REFERENCES donation(id));
        CREATE TABLE IF NOT EXISTS setting(parish_id INTEGER NOT NULL DEFAULT 0, name TEXT NOT NULL, value TEXT NOT NULL, secret INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(parish_id,name));
        CREATE TABLE IF NOT EXISTS app_user(id INTEGER PRIMARY KEY, parish_id INTEGER NOT NULL REFERENCES parish(id), name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL, role TEXT NOT NULL, permissions TEXT NOT NULL DEFAULT '[]', active INTEGER NOT NULL DEFAULT 1, platform_admin INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS member(id INTEGER PRIMARY KEY, parish_id INTEGER NOT NULL REFERENCES parish(id), name TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', document TEXT NOT NULL DEFAULT '', manage_token TEXT NOT NULL UNIQUE, consent_at TEXT, opted_out_at TEXT, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS customer(member_id INTEGER NOT NULL REFERENCES member(id), account_hash TEXT NOT NULL, provider_id TEXT NOT NULL, PRIMARY KEY(member_id,account_hash));
        CREATE TABLE IF NOT EXISTS recurring(id TEXT PRIMARY KEY, parish_id INTEGER NOT NULL REFERENCES parish(id), member_id INTEGER REFERENCES member(id), method TEXT NOT NULL, amount INTEGER NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'awaiting', provider_id TEXT, provider_subscription TEXT, customer_id TEXT, next_due TEXT NOT NULL, day INTEGER NOT NULL, consent_at TEXT NOT NULL, created_at TEXT NOT NULL, cancelled_at TEXT, UNIQUE(parish_id,provider_id));
        CREATE TABLE IF NOT EXISTS provider_event(id TEXT PRIMARY KEY, parish_id INTEGER, event TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, processed_at TEXT);
        CREATE TABLE IF NOT EXISTS message(id INTEGER PRIMARY KEY, parish_id INTEGER NOT NULL REFERENCES parish(id), member_id INTEGER NOT NULL REFERENCES member(id), period TEXT NOT NULL, body TEXT NOT NULL, phone TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', provider_id TEXT, error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, sent_at TEXT, UNIQUE(parish_id,member_id,period));
        CREATE TABLE IF NOT EXISTS inbound_message(id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, parish_id INTEGER NOT NULL REFERENCES parish(id), user_id INTEGER, action TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS login_attempt(key TEXT PRIMARY KEY, failures INTEGER NOT NULL, blocked_until REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS worker_lock(name TEXT PRIMARY KEY, until_time REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS donation_parish ON donation(parish_id,created_at);
        CREATE INDEX IF NOT EXISTS member_parish ON member(parish_id,phone);
        CREATE INDEX IF NOT EXISTS event_state ON provider_event(state,created_at);
    '''
    if postgres:
        schema = schema.replace('id INTEGER PRIMARY KEY,', 'id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,').replace('INTEGER REFERENCES', 'BIGINT REFERENCES').replace('INTEGER NOT NULL REFERENCES', 'BIGINT NOT NULL REFERENCES').replace(' UNIQUE COLLATE NOCASE', ' UNIQUE').replace('blocked_until REAL', 'blocked_until DOUBLE PRECISION').replace('until_time REAL', 'until_time DOUBLE PRECISION')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.executescript(schema)
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        add_columns('parish', {'account_status':"TEXT NOT NULL DEFAULT 'demo'", 'account_id':'TEXT', 'reminder_enabled':'INTEGER NOT NULL DEFAULT 0', 'reminder_body':"TEXT NOT NULL DEFAULT 'Olá, {nome}! A {paroquia} agradece sua generosidade. Faça sua contribuição pelo link {link}. Para parar os lembretes, responda SAIR.'"})
        add_columns('member', {'phone_verified_at':'TEXT'})
        add_columns('donation', {'member_id':'INTEGER REFERENCES member(id)', 'recurring_id':'TEXT REFERENCES recurring(id)', 'mode':"TEXT NOT NULL DEFAULT 'demo'", 'status':"TEXT NOT NULL DEFAULT 'pending'", 'settled_at':'TEXT', 'refunded_at':'TEXT', 'invoice_url':'TEXT', 'qr_payload':'TEXT', 'customer_id':'TEXT', 'period':'TEXT', 'remote_state':'TEXT', 'provider_fee':'INTEGER', 'split_applied':'INTEGER NOT NULL DEFAULT 0'})
        connection.execute("UPDATE donation SET mode='sandbox' WHERE id IN (SELECT donation_id FROM asaas_payment)")
        connection.execute("UPDATE donation SET status='paid' WHERE paid_at IS NOT NULL AND status='pending'")
        connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS member_phone ON member(parish_id,phone) WHERE phone!=""')
        connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS recurring_period ON donation(recurring_id,period) WHERE recurring_id IS NOT NULL')
        connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS provider_recurring ON recurring(parish_id,provider_subscription) WHERE provider_subscription IS NOT NULL')
        for parish in connection.execute('SELECT * FROM parish').fetchall():
            connection.execute("INSERT OR IGNORE INTO app_user(parish_id,name,email,password_hash,role) VALUES(?,?,?,?, 'owner')", (parish['id'],parish['name'],parish['email'],parish['password_hash']))
            if not setting('asaas_webhook_token',parish_id=parish['id']):
                put_setting('asaas_webhook_token',secrets.token_hex(32),parish['id'],secret=True)
        if not connection.execute('SELECT 1 FROM app_user WHERE platform_admin=1').fetchone():
            if current_app.config['HOSTED']:
                connection.execute("UPDATE app_user SET platform_admin=1 WHERE email=? AND role='owner'",(current_app.config['ADMIN_EMAIL'],))
            else:
                connection.execute('UPDATE app_user SET platform_admin=1 WHERE id=(SELECT MIN(id) FROM app_user)')
        defaults = {'mode':'demo', 'public_url':os.environ.get('RENDER_EXTERNAL_URL','') if current_app.config['HOSTED'] else '', 'whatsapp_mode':'demo', 'whatsapp_version':'v23.0', 'whatsapp_language':'pt_BR', 'platform_wallet':''}
        for name, value in defaults.items():
            if not connection.execute('SELECT 1 FROM setting WHERE parish_id=0 AND name=?',(name,)).fetchone():
                put_setting(name,value)
        for env, name in [('ASAAS_API_KEY','asaas_key'),('ASAAS_WEBHOOK_TOKEN','asaas_webhook_token')]:
            value = os.environ.get(env, '')
            if value and not setting(name):
                put_setting(name,value,secret=True)
        if not setting('asaas_webhook_token'):
            put_setting('asaas_webhook_token',secrets.token_hex(32),secret=True)
        if not setting('whatsapp_verify_token'):
            put_setting('whatsapp_verify_token',secrets.token_hex(24),secret=True)

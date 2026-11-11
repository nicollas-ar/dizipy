"""Validação cadastral e segundo passo por e-mail."""
import hashlib
import hmac
import re
import secrets
import time

import requests
from flask import current_app

from storage import db

CODE_LIFETIME = 600
RESEND_INTERVAL = 60
MAX_ATTEMPTS = 5


def normalize_cnpj(value):
    value = re.sub(r'[./\-\s]', '', value).upper()
    if not re.fullmatch(r'[0-9A-Z]{12}[0-9]{2}', value) or len(set(value)) == 1:
        raise ValueError('Informe um CNPJ válido.')
    base = value[:12]
    for size in (12, 13):
        total = sum((ord(char) - 48) * (index % 8 + 2)
                    for index, char in enumerate(reversed(base[:size])))
        remainder = total % 11
        base += str(0 if remainder < 2 else 11 - remainder)
    if base != value:
        raise ValueError('Os dígitos verificadores do CNPJ não conferem.')
    return value


def digest(user, challenge, code):
    key = current_app.secret_key
    if isinstance(key, str):
        key = key.encode()
    data = f"{user['id']}:{user['password_hash']}:{challenge}:{code}".encode()
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def issue_code(user):
    now = int(time.time())
    challenge = secrets.token_urlsafe(24)
    code = f'{secrets.randbelow(1_000_000):06d}'
    with db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT sent_at FROM email_challenge WHERE user_id=?',
                                 (user['id'],)).fetchone()
        if row and now - row['sent_at'] < RESEND_INTERVAL:
            return None
        connection.execute(
            'INSERT INTO email_challenge(user_id,token,code_hash,sent_at,attempts) VALUES(?,?,?,?,0) '
            'ON CONFLICT(user_id) DO UPDATE SET token=excluded.token,code_hash=excluded.code_hash,'
            'sent_at=excluded.sent_at,attempts=0',
            (user['id'], challenge, digest(user, challenge, code), now))
    try:
        response = requests.post(
            'https://api.resend.com/emails',
            headers={'Authorization': 'Bearer ' + current_app.config['RESEND_API_KEY']},
            json={'from': current_app.config['DIZIPY_EMAIL_FROM'], 'to': [user['email']],
                  'subject': 'Código de acesso ao Dizipy',
                  'text': f'Seu código de acesso é {code}. Ele expira em 10 minutos. '
                          'Se você não solicitou este acesso, ignore esta mensagem.'},
            timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        current_app.logger.warning('O serviço de e-mail não confirmou o envio do código.')
        # Keep the cooldown even if the provider fails or the request times out.
        return None
    return challenge


def verify_code(user, challenge, code):
    with db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT * FROM email_challenge WHERE user_id=? AND token=?',
                                 (user['id'], challenge)).fetchone()
        if not row or time.time() >= row['sent_at'] + CODE_LIFETIME or row['attempts'] >= MAX_ATTEMPTS:
            return False
        if not re.fullmatch(r'[0-9]{6}', code) or not hmac.compare_digest(
                row['code_hash'], digest(user, challenge, code)):
            connection.execute('UPDATE email_challenge SET attempts=attempts+1 WHERE user_id=?',
                               (user['id'],))
            return False
        connection.execute('UPDATE email_challenge SET attempts=? WHERE user_id=?',
                           (MAX_ATTEMPTS, user['id']))
        return True

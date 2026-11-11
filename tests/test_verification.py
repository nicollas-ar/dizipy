import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from verification import normalize_cnpj


class Verification(unittest.TestCase):
    def test_cnpj_check_digits(self):
        self.assertEqual(normalize_cnpj('00.000.000/E08G-12'), '00000000E08G12')
        for invalid in ('00.000.000/E08G-13', '00.000.000/0000-00'):
            with self.assertRaises(ValueError):
                normalize_cnpj(invalid)

    @patch('verification.requests.post')
    def test_email_required_and_code_single_use(self, mail):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({'TESTING': True, 'DATABASE': Path(directory)/'test.sqlite',
                              'RESEND_API_KEY': 'test', 'DIZIPY_EMAIL_FROM': 'test@example.com'})
            client = app.test_client()

            def post(path, **data):
                with client.session_transaction() as state:
                    csrf = state['csrf']
                return client.post(path, data={'csrf': csrf, **data})

            client.get('/cadastro')
            response = post('/cadastro', name='Paróquia teste', email='admin@example.com',
                            password='senha-de-teste', cnpj='00.000.000/E08G-12')
            self.assertTrue(response.location.endswith('/entrar/verificar'))
            self.assertEqual(client.get('/painel').status_code, 302)
            client.get('/entrar/verificar')
            code = re.search(r'\b[0-9]{6}\b', mail.call_args.kwargs['json']['text'])[0]
            with client.session_transaction() as state:
                pending = list(state['email_challenge'])
            self.assertEqual(post('/entrar/verificar', code='errado').status_code, 200)
            self.assertEqual(post('/entrar/verificar', code=code).status_code, 302)
            self.assertEqual(client.get('/painel').status_code, 200)
            with client.session_transaction() as state:
                state.pop('user_id')
                state.pop('email_verified')
                state['email_challenge'] = pending
            client.get('/entrar/verificar')
            self.assertEqual(post('/entrar/verificar', code=code).status_code, 200)
            self.assertEqual(client.get('/painel').status_code, 302)
            self.assertIn('Política de cookies', client.get('/cookies').get_data(as_text=True))

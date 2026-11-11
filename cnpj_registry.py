"""Consulta básica ao cadastro de pessoas jurídicas do Serpro."""
import threading
import time

import requests

from verification import normalize_cnpj

GATEWAY = 'https://gateway.apiserpro.serpro.gov.br'


class RegistryUnavailable(Exception):
    pass


class Registry:
    def __init__(self, key, secret):
        self.credentials = (key, secret)
        self.token = ''
        self.expires = 0
        self.lock = threading.Lock()

    def access_token(self, expired=None):
        with self.lock:
            if self.token != expired and time.monotonic() < self.expires:
                return self.token
            response = requests.post(
                GATEWAY + '/token', auth=self.credentials,
                data={'grant_type': 'client_credentials'}, timeout=(3, 5),
                allow_redirects=False)
            response.raise_for_status()
            data = response.json()
            self.token = data['access_token']
            self.expires = time.monotonic() + max(0, int(data['expires_in']) - 60)
            return self.token

    def lookup(self, cnpj):
        cnpj = normalize_cnpj(cnpj)
        try:
            token = self.access_token()
            url = GATEWAY + '/consulta-cnpj-df/v2/basica/' + cnpj
            response = requests.get(url, headers={'Authorization': f'Bearer {token}'},
                                    timeout=(3, 6), allow_redirects=False)
            if response.status_code == 401:
                token = self.access_token(expired=token)
                response = requests.get(url, headers={'Authorization': f'Bearer {token}'},
                                        timeout=(3, 6), allow_redirects=False)
            if response.status_code == 404:
                return 'not_found', None
            response.raise_for_status()
            data = response.json()
            if normalize_cnpj(data['ni']) != cnpj:
                raise ValueError('Resposta com CNPJ divergente.')
            name = data['nome_empresarial'].strip()
            status = int(data['situacao_cadastral']['codigo'])
            if not name or status not in (1, 2, 3, 4, 5, 8):
                raise ValueError('Resposta cadastral incompleta.')
            return ('active' if status == 2 else 'inactive'), name[:200]
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
            raise RegistryUnavailable from None

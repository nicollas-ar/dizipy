"""Cliente HTTP para as APIs do Asaas."""

import requests

BASE_URL = "https://api-sandbox.asaas.com/v3"
PRODUCTION_URL = "https://api.asaas.com/v3"


class AsaasError(Exception):
    pass


def call(api_key, method, path, *, data=None, params=None, production=False):
    base_url = PRODUCTION_URL if production else BASE_URL
    provider = "Asaas" if production else "Asaas Sandbox"
    try:
        response = requests.request(
            method, base_url + path, json=data, params=params, timeout=(3, 5),
            headers={"access_token": api_key, "User-Agent": "Dizipy/1.0"},
            allow_redirects=False,
        )
        if not 200 <= response.status_code < 300:
            message = f"{provider} respondeu com HTTP {response.status_code}."
            try:
                body = response.json()
            except ValueError:
                body = {}
            errors = body.get("errors") if isinstance(body, dict) else None
            if isinstance(errors, list):
                descriptions = [
                    error["description"] for error in errors[:3]
                    if isinstance(error, dict) and isinstance(error.get("description"), str)
                ]
                detail = " ".join(" ".join(descriptions).split())
                if api_key:
                    detail = detail.replace(api_key, "[chave omitida]")
                if detail:
                    message += " " + detail[:500]
            raise AsaasError(message)
        result = response.json()
    except requests.Timeout:
        raise AsaasError(f"O {provider} demorou a responder. Tente consultar novamente.") from None
    except (requests.RequestException, ValueError):
        raise AsaasError(f"Não foi possível obter uma resposta válida do {provider}.") from None
    if not isinstance(result, dict):
        raise AsaasError(f"Resposta inesperada do {provider}.")
    return result

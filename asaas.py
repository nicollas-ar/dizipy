"""Cliente HTTP usado exclusivamente para o Asaas Sandbox."""

import requests

BASE_URL = "https://api-sandbox.asaas.com/v3"


class AsaasError(Exception):
    pass


def call(api_key, method, path, *, data=None, params=None):
    try:
        response = requests.request(
            method, BASE_URL + path, json=data, params=params, timeout=(3, 5),
            headers={"access_token": api_key, "User-Agent": "Dizipy-Sandbox/1.0"},
            allow_redirects=False,
        )
        if not 200 <= response.status_code < 300:
            message = f"Asaas Sandbox respondeu com HTTP {response.status_code}."
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
        raise AsaasError("O Asaas Sandbox demorou a responder. Tente consultar novamente.") from None
    except (requests.RequestException, ValueError):
        raise AsaasError("Não foi possível obter uma resposta válida do Asaas Sandbox.") from None
    if not isinstance(result, dict):
        raise AsaasError("Resposta inesperada do Asaas Sandbox.")
    return result


"""
Send the JSON payload via HTTPS POST to the configured target URL.
Credentials and URL are read from environment variables.
"""
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

TARGET_URL: str = os.getenv("TARGET_URL", "https://edi.eglo.com/dw/Request/ETIM10_Export/v1")
BASIC_AUTH_USER: str = os.getenv("BASIC_AUTH_USER", "")
BASIC_AUTH_PASS: str = os.getenv("BASIC_AUTH_PASS", "")
TIMEOUT_SECONDS: float = float(os.getenv("REQUEST_TIMEOUT", "30"))


async def send_payload(payload: dict) -> dict:
    """
    POST *payload* as JSON to TARGET_URL.

    Returns:
        {
            "status_code": int,
            "success": bool,
            "body": str (first 2000 chars of response text),
        }

    Raises:
        Exception with a human-readable German error message on network failures.
    """
    auth = (BASIC_AUTH_USER, BASIC_AUTH_PASS) if BASIC_AUTH_USER else None

    try:
        async with httpx.AsyncClient(verify=True) as client:
            response = await client.post(
                TARGET_URL,
                json=payload,
                auth=auth,
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=TIMEOUT_SECONDS,
            )
    except httpx.TimeoutException:
        raise Exception(
            f"Zeitüberschreitung nach {int(TIMEOUT_SECONDS)} s – der Ziel-Server antwortet nicht."
        )
    except httpx.ConnectError:
        raise Exception(f"Verbindung fehlgeschlagen zu: {TARGET_URL}")
    except httpx.RequestError as exc:
        raise Exception(f"HTTP-Anfragefehler: {exc}")

    return {
        "status_code": response.status_code,
        "success": response.is_success,
        "body": response.text[:2000],
    }
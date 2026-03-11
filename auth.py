"""Azure AD SSO – MSAL helper"""
import os

import msal

TENANT_ID     = os.getenv("AZURE_TENANT_ID",    "8d493468-c59a-42b7-80ed-42d78e69c065")
CLIENT_ID     = os.getenv("AZURE_CLIENT_ID",    "b908ea08-395f-413d-9aab-25acf3d66c02")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")
REDIRECT_URI  = os.getenv("AZURE_REDIRECT_URI",  "https://etim.eglo.com/auth")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES    = ["User.Read"]


def _msal_app() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET,
    )


def get_auth_url(state: str) -> str:
    return _msal_app().get_authorization_request_url(
        SCOPES,
        state=state,
        redirect_uri=REDIRECT_URI,
    )


def exchange_code(code: str) -> dict:
    return _msal_app().acquire_token_by_authorization_code(
        code,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

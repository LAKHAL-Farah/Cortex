import os
from fastapi import Header, HTTPException, status

API_KEY = os.environ["CORTEX_API_KEY"]


def require_api_key(x_api_key: str = Header(...)) -> None:
    if x_api_key != API_KEY:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")


# Cortex has no user-account system yet, so Copilot conversation history
# (routers/conversations.py) can't be scoped by a real login. Instead the
# frontend generates a random UUID once per browser (see
# lib/copilotHistory.ts's getClientId) and sends it as X-Client-Id on every
# request; this dependency just extracts and sanity-checks it -- it is a
# scoping key, not a secret, so unlike require_api_key there's nothing to
# compare it against. Every conversations.py route still also requires
# X-API-Key, same as every other mutating/read-sensitive endpoint in the API.
def get_client_id(x_client_id: str = Header(..., min_length=8, max_length=128)) -> str:
    return x_client_id

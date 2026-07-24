import os
from fastapi import Header, HTTPException, status

API_KEY = os.environ["CORTEX_API_KEY"]


def require_api_key(x_api_key: str = Header(...)) -> None:
    if x_api_key != API_KEY:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")

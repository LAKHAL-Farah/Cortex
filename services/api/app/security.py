from fastapi import Header

# The shared X-API-Key gate (require_api_key) that used to guard mutating
# endpoints has been replaced by real accounts -- see app/auth.py's
# get_current_user/require_admin, wired in globally in main.py. This file
# now only holds get_client_id, which is unrelated to authentication.


# Cortex has no anonymous-only usage anymore (every router requires a
# logged-in user, see main.py), but Copilot conversation history
# (routers/conversations.py) is still scoped per-browser rather than by
# account, since one person may use several browsers/devices. The frontend
# generates a random UUID once per browser (see lib/copilotHistory.ts's
# getClientId) and sends it as X-Client-Id on every request; this dependency
# just extracts and sanity-checks it -- it is a scoping key, not a secret.
def get_client_id(x_client_id: str = Header(..., min_length=8, max_length=128)) -> str:
    return x_client_id

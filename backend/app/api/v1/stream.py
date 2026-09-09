from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.core.errors import UnauthenticatedError
from app.core.security import decode_token
from app.services.events import event_stream

router = APIRouter(tags=["real-time"])


@router.get("/stream")
async def stream(access_token: str = Query(..., description="Short-lived stream token")):
    """Server-Sent Events feed for the operations dashboard.

    The browser's EventSource cannot set an Authorization header, so the token
    arrives as a query parameter. That is a weaker place for a credential
    (proxy logs, browser history), which is why this accepts only a token of
    type ``stream``: five-minute lifetime, no API authority, obtained from
    POST /auth/stream-token with a normal bearer token.
    """
    claims = decode_token(access_token, "stream")
    if not claims.get("sub"):
        raise UnauthenticatedError("Invalid stream token.")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Nginx buffers proxied responses by default, which would hold
            # events until the buffer filled and defeat the point of SSE.
            "X-Accel-Buffering": "no",
        },
    )

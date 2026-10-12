from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Header, Form, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.models.tenancy import UnsubscribeToken
from app.core.security import generate_csrf_token, verify_csrf_token
from app.services.suppression_service import SuppressionService

router = APIRouter(prefix="/unsubscribe", tags=["Unsubscribe"])

class BrowserConfirmRequest(BaseModel):
    token: str
    csrf_token: str

@router.get("", response_class=HTMLResponse)
def get_unsubscribe_page(
    token: str = Query(..., description="Opaque high-entropy unsubscribe token"),
    db: Session = Depends(get_db)
):
    """
    GET /api/v1/unsubscribe?token=<opaque_token>
    STRICTLY NON-MUTATING.
    Immune to automated email security scanners and link prefetchers.
    Renders a generic confirmation page with an anti-CSRF protected confirm button.
    Zero PII leakage.
    """
    # 1. Validate token syntax & existence
    if not token or len(token) < 20:
        return HTMLResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content="<html><body><h2>Invalid or expired unsubscribe link.</h2></body></html>"
        )

    db_token = db.execute(
        select(UnsubscribeToken).where(UnsubscribeToken.token == token)
    ).scalars().first()

    if not db_token:
        return HTMLResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content="<html><body><h2>Invalid or expired unsubscribe link.</h2></body></html>"
        )

    now = datetime.now(timezone.utc)
    token_expires_at = db_token.expires_at
    if token_expires_at and token_expires_at.tzinfo is None:
        token_expires_at = token_expires_at.replace(tzinfo=timezone.utc)

    if token_expires_at < now:
        # Safe generic response without leaking existence
        return HTMLResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content="<html><body><h2>Invalid or expired unsubscribe link.</h2></body></html>"
        )

    # 2. Generate anti-CSRF token bound to this opaque unsubscribe token
    csrf_token = generate_csrf_token(token)

    # 3. Render generic confirmation page (Zero PII)
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Unsubscribe Confirmation</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; background-color: #0f172a; color: #f8fafc; }}
        .card {{ background: #1e293b; padding: 2.5rem; border-radius: 12px; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5); max-width: 440px; text-align: center; border: 1px solid #334155; }}
        h1 {{ font-size: 1.5rem; margin-bottom: 1rem; color: #ffffff; }}
        p {{ color: #94a3b8; font-size: 0.95rem; line-height: 1.5; margin-bottom: 2rem; }}
        button {{ background: #ef4444; color: #ffffff; border: none; padding: 0.85rem 1.75rem; border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 1rem; transition: background 0.2s; width: 100%; }}
        button:hover {{ background: #dc2626; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>Confirm Opt-Out</h1>
        <p>Please confirm that you wish to opt out and stop receiving business development communications.</p>
        <form action="/api/v1/unsubscribe/confirm" method="POST">
            <input type="hidden" name="token" value="{token}">
            <input type="hidden" name="csrf_token" value="{csrf_token}">
            <button type="submit">Confirm Unsubscribe</button>
        </form>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=status.HTTP_200_OK)


@router.post("/confirm")
def browser_confirm_unsubscribe(
    request: Request,
    token: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """
    POST /api/v1/unsubscribe/confirm
    Browser confirmation endpoint.
    Requires valid unsubscribe token + anti-CSRF token.
    Atomically persists suppression across workspace, organization, and compliance ledgers.
    Idempotent.
    """
    if not token or not csrf_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request: Missing token or CSRF token."
        )

    # 1. Validate anti-CSRF token
    if not verify_csrf_token(token, csrf_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired CSRF token."
        )

    # 2. Validate unsubscribe token in DB
    db_token = db.execute(
        select(UnsubscribeToken).where(UnsubscribeToken.token == token)
    ).scalars().first()

    now = datetime.now(timezone.utc)
    token_expires_at = db_token.expires_at if db_token else None
    if token_expires_at and token_expires_at.tzinfo is None:
        token_expires_at = token_expires_at.replace(tzinfo=timezone.utc)

    if not db_token or token_expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired unsubscribe link."
        )

    # 3. Atomically perform suppression
    SuppressionService.execute_atomic_suppression(
        db=db,
        organization_id=db_token.organization_id,
        workspace_id=db_token.workspace_id,
        email=db_token.raw_email_for_delivery,
        source_channel="UNSUBSCRIBE_BROWSER_CONFIRM",
        reason="Recipient confirmed opt-out via web confirmation page",
        actor_id="RECIPIENT_BROWSER",
        actor_type="EXTERNAL_USER",
        commit=True
    )

    # 4. Mark token used (idempotent)
    db_token.is_used = True
    db_token.used_at = now
    db.commit()

    return {
        "status": "success",
        "message": "You have been successfully unsubscribed."
    }


@router.post("/one-click")
async def rfc8058_one_click_unsubscribe(
    request: Request,
    token: Optional[str] = Query(None),
    list_unsubscribe_post: Optional[str] = Header(None, alias="List-Unsubscribe-Post"),
    db: Session = Depends(get_db)
):
    """
    POST /api/v1/unsubscribe/one-click
    RFC 8058 / List-Unsubscribe-Post direct endpoint for Mail User Agents.
    Does NOT require browser CSRF token.
    Validates RFC 8058 request semantics (List-Unsubscribe=One-Click).
    Atomically persists suppression across workspace, organization, and compliance ledgers.
    Idempotent.
    """
    # 1. Validate RFC 8058 request semantics
    # Standard specifies POST request with body "List-Unsubscribe=One-Click"
    # or header List-Unsubscribe-Post: List-Unsubscribe=One-Click
    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8", errors="ignore").strip()

    is_valid_rfc8058 = (
        "List-Unsubscribe=One-Click" in body_text or
        (list_unsubscribe_post and "List-Unsubscribe=One-Click" in list_unsubscribe_post)
    )

    if not is_valid_rfc8058:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed RFC 8058 request: Missing List-Unsubscribe=One-Click declaration."
        )

    # 2. Extract token from query or body
    effective_token = token
    if not effective_token and "=" in body_text:
        for pair in body_text.split("&"):
            if pair.startswith("token="):
                effective_token = pair.split("=", 1)[1]

    if not effective_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing unsubscribe token."
        )

    # 3. Validate token in DB
    db_token = db.execute(
        select(UnsubscribeToken).where(UnsubscribeToken.token == effective_token)
    ).scalars().first()

    now = datetime.now(timezone.utc)
    token_expires_at = db_token.expires_at if db_token else None
    if token_expires_at and token_expires_at.tzinfo is None:
        token_expires_at = token_expires_at.replace(tzinfo=timezone.utc)

    if not db_token or token_expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired unsubscribe link."
        )

    # 4. Atomically perform suppression via shared service
    SuppressionService.execute_atomic_suppression(
        db=db,
        organization_id=db_token.organization_id,
        workspace_id=db_token.workspace_id,
        email=db_token.raw_email_for_delivery,
        source_channel="RFC8058_ONE_CLICK",
        reason="Mail User Agent direct one-click unsubscribe",
        actor_id="MUA_RFC8058",
        actor_type="EXTERNAL_MUA",
        commit=True
    )

    # 5. Mark token used (idempotent)
    db_token.is_used = True
    db_token.used_at = now
    db.commit()

    return {
        "status": "success",
        "message": "You have been successfully unsubscribed."
    }

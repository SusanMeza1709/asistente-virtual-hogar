from html import escape

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.services.lgthinq_service import LGThinQService

router = APIRouter(prefix="/integrations", tags=["Integrations"])


@router.get("/lgthinq/status")
def lgthinq_status(db: Session = Depends(get_db)):
    return {
        "ready": LGThinQService.can_query(db),
        "message": "LG ThinQ está listo" if LGThinQService.can_query(db) else LGThinQService.setup_instructions(),
        "alert_config": LGThinQService.alert_config(db),
        "last_event": LGThinQService.last_event(db),
    }


@router.post("/lgthinq/webhook")
async def lgthinq_webhook(
    request: Request,
    x_lgthinq_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    expected_secret = LGThinQService._webhook_secret()
    if expected_secret and (x_lgthinq_secret or "").strip() != expected_secret:
        return {"ok": False, "message": "Webhook secret inválido."}

    payload = await request.json()
    ok, event, detail = LGThinQService.process_webhook_payload(db, payload)
    return {
        "ok": ok,
        "message": detail,
        "event": event,
    }


@router.post("/lgthinq/poll")
def lgthinq_poll(device: str | None = Query(default=None), db: Session = Depends(get_db)):
    ok, event, detail = LGThinQService.poll_for_alerts(db, preferred_name=device)
    return {
        "ok": ok,
        "message": detail,
        "event": event,
    }

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.schemas import ChatMessage, ChatResponse
from app.services.chat_service import ChatService

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post("", response_model=ChatResponse)
def chat(payload: ChatMessage, db: Session = Depends(get_db)):
    try:
        return ChatResponse(reply=ChatService.reply(db, payload.message))
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return ChatResponse(
            reply=(
                "Tuve un problema interno al procesar ese mensaje. "
                "Inténtalo de nuevo con: 'agrega nombre_del_producto'."
            )
        )

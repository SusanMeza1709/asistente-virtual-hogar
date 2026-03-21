from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.schemas import ChatMessage, ChatResponse
from app.services.chat_service import ChatService

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post("", response_model=ChatResponse)
def chat(payload: ChatMessage, db: Session = Depends(get_db)):
    return ChatResponse(reply=ChatService.reply(db, payload.message))

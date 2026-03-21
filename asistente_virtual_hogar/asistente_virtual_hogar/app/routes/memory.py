from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.schemas import MemoryCreate, MemoryResponse
from app.services.memory_service import MemoryService

router = APIRouter(prefix="/memoria", tags=["Memoria"])


@router.get("", response_model=list[MemoryResponse])
def list_memory(db: Session = Depends(get_db)):
    return MemoryService.list_items(db)


@router.post("", response_model=MemoryResponse, status_code=201)
def save_memory(payload: MemoryCreate, db: Session = Depends(get_db)):
    return MemoryService.save_item(db, payload)

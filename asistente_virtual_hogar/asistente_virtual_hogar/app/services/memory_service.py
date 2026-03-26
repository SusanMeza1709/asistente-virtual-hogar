from sqlalchemy.orm import Session

from app.models.entities import MemoryItem
from app.models.schemas import MemoryCreate


class MemoryService:
    @staticmethod
    def list_items(db: Session) -> list[MemoryItem]:
        return db.query(MemoryItem).order_by(MemoryItem.updated_at.desc()).all()

    @staticmethod
    def get_by_key(db: Session, key: str) -> MemoryItem | None:
        return db.query(MemoryItem).filter(MemoryItem.key.ilike(key.strip())).first()

    @staticmethod
    def delete_item(db: Session, item: MemoryItem) -> None:
        db.delete(item)
        db.commit()

    @staticmethod
    def save_item(db: Session, payload: MemoryCreate) -> MemoryItem:
        item = db.query(MemoryItem).filter(MemoryItem.key.ilike(payload.key.strip())).first()
        if item:
            item.value = payload.value
            db.commit()
            db.refresh(item)
            return item

        item = MemoryItem(key=payload.key.strip(), value=payload.value.strip())
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

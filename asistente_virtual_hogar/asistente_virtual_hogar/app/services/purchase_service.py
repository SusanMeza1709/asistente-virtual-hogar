from datetime import datetime

from sqlalchemy.orm import Session

from app.models.entities import Product, Purchase
from app.models.schemas import PurchaseCreate
from app.services.inventory_service import InventoryService


class PurchaseService:
    @staticmethod
    def register_purchase(db: Session, product: Product, payload: PurchaseCreate) -> Purchase:
        purchase = Purchase(
            product_id=product.id,
            quantity=payload.quantity,
            unit_price=payload.unit_price,
            store=payload.store,
            purchased_at=payload.purchased_at or datetime.utcnow(),
        )
        db.add(purchase)
        db.flush()
        InventoryService.increase_stock(db, product, payload.quantity)
        db.refresh(purchase)
        return purchase

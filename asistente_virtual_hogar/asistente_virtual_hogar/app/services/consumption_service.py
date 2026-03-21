from datetime import datetime

from sqlalchemy.orm import Session

from app.models.entities import Consumption, Product
from app.models.schemas import ConsumptionCreate
from app.services.inventory_service import InventoryService


class ConsumptionService:
    @staticmethod
    def register_consumption(db: Session, product: Product, payload: ConsumptionCreate) -> Consumption:
        InventoryService.decrease_stock(db, product, payload.quantity)
        consumption = Consumption(
            product_id=product.id,
            quantity=payload.quantity,
            consumed_at=payload.consumed_at or datetime.utcnow(),
            note=payload.note,
        )
        db.add(consumption)
        db.commit()
        db.refresh(consumption)
        return consumption

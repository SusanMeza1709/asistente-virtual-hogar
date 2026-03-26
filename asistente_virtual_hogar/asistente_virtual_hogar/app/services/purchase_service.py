from datetime import datetime

from sqlalchemy.orm import Session

from app.models.entities import Product, Purchase
from app.models.schemas import ExpenseSummary
from app.models.schemas import PurchaseCreate
from app.services.inventory_service import InventoryService


class PurchaseService:
    @staticmethod
    def list_purchases(db: Session, limit: int | None = None) -> list[Purchase]:
        query = db.query(Purchase).order_by(Purchase.purchased_at.desc())
        if limit is not None:
            query = query.limit(limit)
        return query.all()

    @staticmethod
    def summarize_expenses(db: Session, days: int = 30) -> ExpenseSummary:
        purchases = PurchaseService.list_purchases(db)
        cutoff = datetime.utcnow().timestamp() - (days * 24 * 60 * 60)

        total_amount = 0.0
        purchases_count = 0
        items_with_price = 0

        for purchase in purchases:
            if purchase.purchased_at.timestamp() < cutoff:
                continue
            purchases_count += 1
            if purchase.unit_price is not None:
                total_amount += purchase.unit_price * purchase.quantity
                items_with_price += 1

        return ExpenseSummary(
            total_amount=round(total_amount, 2),
            purchases_count=purchases_count,
            items_with_price=items_with_price,
            period_days=days,
        )

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

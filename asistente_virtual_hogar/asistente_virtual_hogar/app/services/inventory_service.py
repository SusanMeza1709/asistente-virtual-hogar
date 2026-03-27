from sqlalchemy.orm import Session

from app.models.entities import Product


class InventoryService:
    @staticmethod
    def increase_stock(db: Session, product: Product, quantity: float) -> Product:
        product.stock_current += quantity
        db.commit()
        db.refresh(product)
        return product

    @staticmethod
    def decrease_stock(db: Session, product: Product, quantity: float) -> Product:
        if product.stock_current <= 0:
            raise ValueError(
                f"{product.name} ya está sin stock. No hay nada que descontar."
            )
        product.stock_current = max(0.0, product.stock_current - quantity)
        db.commit()
        db.refresh(product)
        return product

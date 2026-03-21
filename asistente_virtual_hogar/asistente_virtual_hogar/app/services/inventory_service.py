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
        if product.stock_current < quantity:
            raise ValueError(
                f"Stock insuficiente para {product.name}. Disponible: {product.stock_current} {product.unit}."
            )
        product.stock_current -= quantity
        db.commit()
        db.refresh(product)
        return product

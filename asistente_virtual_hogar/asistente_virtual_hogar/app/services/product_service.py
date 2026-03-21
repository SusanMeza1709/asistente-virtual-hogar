from sqlalchemy.orm import Session

from app.models.entities import Product
from app.models.schemas import ProductCreate, ProductUpdate


class ProductService:
    @staticmethod
    def list_products(db: Session) -> list[Product]:
        return db.query(Product).order_by(Product.name.asc()).all()

    @staticmethod
    def get_by_id(db: Session, product_id: int) -> Product | None:
        return db.query(Product).filter(Product.id == product_id).first()

    @staticmethod
    def get_by_name(db: Session, name: str) -> Product | None:
        return db.query(Product).filter(Product.name.ilike(name.strip())).first()

    @staticmethod
    def create_product(db: Session, payload: ProductCreate) -> Product:
        product = Product(**payload.model_dump())
        db.add(product)
        db.commit()
        db.refresh(product)
        return product

    @staticmethod
    def update_product(db: Session, product: Product, payload: ProductUpdate) -> Product:
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(product, key, value)
        db.commit()
        db.refresh(product)
        return product

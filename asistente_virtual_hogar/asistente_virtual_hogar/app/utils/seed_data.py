from sqlalchemy.orm import Session

from app.models.entities import Product


SEED_PRODUCTS = [
    {
        "name": "Leche",
        "category": "Lácteos",
        "unit": "litro",
        "stock_current": 2,
        "stock_minimum": 1,
        "location": "Refrigerador",
    },
    {
        "name": "Huevos",
        "category": "Proteínas",
        "unit": "unidad",
        "stock_current": 6,
        "stock_minimum": 6,
        "location": "Cocina",
    },
]


def seed_if_empty(db: Session) -> None:
    if db.query(Product).count() > 0:
        return

    for row in SEED_PRODUCTS:
        db.add(Product(**row))
    db.commit()

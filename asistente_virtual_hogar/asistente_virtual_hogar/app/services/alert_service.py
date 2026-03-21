from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.entities import Product
from app.models.schemas import AlertItem, AlertsResponse


class AlertService:
    @staticmethod
    def build_alerts(db: Session, expiration_window_days: int = 3) -> AlertsResponse:
        products = db.query(Product).order_by(Product.name.asc()).all()
        today = date.today()
        max_date = today + timedelta(days=expiration_window_days)

        low_stock: list[AlertItem] = []
        expiring_soon: list[AlertItem] = []

        for product in products:
            if product.stock_current <= product.stock_minimum:
                low_stock.append(
                    AlertItem(
                        product_name=product.name,
                        current_stock=product.stock_current,
                        minimum_stock=product.stock_minimum,
                        unit=product.unit,
                        reason="stock_bajo",
                        expiration_date=product.expiration_date,
                    )
                )

            if product.expiration_date and today <= product.expiration_date <= max_date:
                expiring_soon.append(
                    AlertItem(
                        product_name=product.name,
                        current_stock=product.stock_current,
                        minimum_stock=product.stock_minimum,
                        unit=product.unit,
                        reason="proximo_a_vencer",
                        expiration_date=product.expiration_date,
                    )
                )

        return AlertsResponse(low_stock=low_stock, expiring_soon=expiring_soon)

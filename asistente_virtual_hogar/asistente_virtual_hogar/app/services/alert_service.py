from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.entities import Product
from app.models.schemas import AlertItem, AlertsResponse, ShoppingListItem


class AlertService:
    @staticmethod
    def refresh_product_statuses(db: Session) -> None:
        today = date.today()
        changed = False

        for product in db.query(Product).all():
            expected_status = "vencido" if product.expiration_date and product.expiration_date < today else "activo"
            if product.status != expected_status:
                product.status = expected_status
                changed = True

        if changed:
            db.commit()

    @staticmethod
    def build_alerts(db: Session, expiration_window_days: int = 3) -> AlertsResponse:
        AlertService.refresh_product_statuses(db)
        products = db.query(Product).order_by(Product.name.asc()).all()
        today = date.today()
        max_date = today + timedelta(days=expiration_window_days)

        low_stock: list[AlertItem] = []
        expiring_soon: list[AlertItem] = []
        expired: list[AlertItem] = []
        consume_first: list[AlertItem] = []
        shopping_list: list[ShoppingListItem] = []

        for product in products:
            days_until_expiration = None
            if product.expiration_date:
                days_until_expiration = (product.expiration_date - today).days

            if product.stock_current <= product.stock_minimum:
                low_stock.append(
                    AlertItem(
                        product_name=product.name,
                        current_stock=product.stock_current,
                        minimum_stock=product.stock_minimum,
                        unit=product.unit,
                        reason="stock_bajo",
                        expiration_date=product.expiration_date,
                        days_until_expiration=days_until_expiration,
                        suggested_action="agregar_a_lista_de_compras",
                    )
                )
                shopping_list.append(
                    ShoppingListItem(
                        product_name=product.name,
                        needed_quantity=max(product.stock_minimum - product.stock_current, 1),
                        unit=product.unit,
                        reason="stock_bajo",
                    )
                )

            if product.expiration_date and product.expiration_date < today:
                expired.append(
                    AlertItem(
                        product_name=product.name,
                        current_stock=product.stock_current,
                        minimum_stock=product.stock_minimum,
                        unit=product.unit,
                        reason="vencido",
                        expiration_date=product.expiration_date,
                        days_until_expiration=days_until_expiration,
                        suggested_action="retirar_o_desechar",
                    )
                )
                continue

            if product.expiration_date and today <= product.expiration_date <= max_date:
                expiring_soon.append(
                    AlertItem(
                        product_name=product.name,
                        current_stock=product.stock_current,
                        minimum_stock=product.stock_minimum,
                        unit=product.unit,
                        reason="proximo_a_vencer",
                        expiration_date=product.expiration_date,
                        days_until_expiration=days_until_expiration,
                        suggested_action="consumir_primero",
                    )
                )

            if product.expiration_date and product.stock_current > 0:
                consume_first.append(
                    AlertItem(
                        product_name=product.name,
                        current_stock=product.stock_current,
                        minimum_stock=product.stock_minimum,
                        unit=product.unit,
                        reason="prioridad_de_consumo",
                        expiration_date=product.expiration_date,
                        days_until_expiration=days_until_expiration,
                        suggested_action="consumir_primero",
                    )
                )

        consume_first.sort(
            key=lambda item: (
                item.days_until_expiration is None,
                item.days_until_expiration if item.days_until_expiration is not None else 999999,
                item.product_name,
            )
        )

        return AlertsResponse(
            low_stock=low_stock,
            expiring_soon=expiring_soon,
            expired=expired,
            consume_first=consume_first[:5],
            shopping_list=shopping_list,
        )

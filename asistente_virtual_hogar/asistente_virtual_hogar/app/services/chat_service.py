import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.schemas import ConsumptionCreate, MemoryCreate, ProductCreate, PurchaseCreate
from app.services.alert_service import AlertService
from app.services.consumption_service import ConsumptionService
from app.services.memory_service import MemoryService
from app.services.product_service import ProductService
from app.services.purchase_service import PurchaseService


class ChatService:
    PRODUCT_CMD = re.compile(
        r"agregar producto\s+(?P<name>[\w\sáéíóúñÁÉÍÓÚÑ-]+)"
        r"(?:,\s*cantidad\s+(?P<stock>[\d.]+))?"
        r"(?:,\s*unidad\s+(?P<unit>[\wáéíóúñÁÉÍÓÚÑ]+))?"
        r"(?:,\s*categoria\s+(?P<category>[\w\sáéíóúñÁÉÍÓÚÑ-]+))?"
        r"(?:,\s*ubicacion\s+(?P<location>[\w\sáéíóúñÁÉÍÓÚÑ-]+))?"
        r"(?:,\s*stock_minimo\s+(?P<minimum>[\d.]+))?"
        r"(?:,\s*vencimiento\s+(?P<expiration>\d{4}-\d{2}-\d{2}))?",
        re.IGNORECASE,
    )
    BUY_CMD = re.compile(r"comprar\s+(?P<qty>[\d.]+)\s+(?P<name>[\w\sáéíóúñÁÉÍÓÚÑ-]+)", re.IGNORECASE)
    CONSUME_CMD = re.compile(r"consumir\s+(?P<qty>[\d.]+)\s+(?P<name>[\w\sáéíóúñÁÉÍÓÚÑ-]+)", re.IGNORECASE)
    MEMORY_CMD = re.compile(r"recuerda\s+(?P<key>[\w\sáéíóúñÁÉÍÓÚÑ-]+)\s*:\s*(?P<value>.+)", re.IGNORECASE)

    @staticmethod
    def reply(db: Session, message: str) -> str:
        text = message.strip()
        text_l = text.lower()

        product_match = ChatService.PRODUCT_CMD.fullmatch(text)
        if product_match:
            data = product_match.groupdict()
            product = ProductService.get_by_name(db, data["name"].strip())
            if product:
                return f"El producto {product.name} ya existe. Usa el endpoint /productos/{{id}} para editarlo."

            expiration = None
            if data.get("expiration"):
                expiration = datetime.strptime(data["expiration"], "%Y-%m-%d").date()

            created = ProductService.create_product(
                db,
                ProductCreate(
                    name=data["name"].strip(),
                    stock_current=float(data["stock"] or 0),
                    unit=(data["unit"] or "unidad").strip(),
                    category=(data.get("category") or None),
                    location=(data.get("location") or None),
                    stock_minimum=float(data["minimum"] or 1),
                    expiration_date=expiration,
                ),
            )
            return f"Producto creado: {created.name} con stock {created.stock_current} {created.unit}."

        buy_match = ChatService.BUY_CMD.fullmatch(text)
        if buy_match:
            qty = float(buy_match.group("qty"))
            name = buy_match.group("name").strip()
            product = ProductService.get_by_name(db, name)
            if not product:
                return f"No encontré el producto {name}. Primero créalo con 'agregar producto ...'."
            PurchaseService.register_purchase(db, product, PurchaseCreate(product_name=name, quantity=qty))
            return f"Compra registrada: +{qty} {product.unit} de {product.name}. Stock actual: {product.stock_current} {product.unit}."

        consume_match = ChatService.CONSUME_CMD.fullmatch(text)
        if consume_match:
            qty = float(consume_match.group("qty"))
            name = consume_match.group("name").strip()
            product = ProductService.get_by_name(db, name)
            if not product:
                return f"No encontré el producto {name}."
            try:
                ConsumptionService.register_consumption(db, product, ConsumptionCreate(product_name=name, quantity=qty))
            except ValueError as exc:
                return str(exc)
            return f"Consumo registrado: -{qty} {product.unit} de {product.name}. Stock actual: {product.stock_current} {product.unit}."

        memory_match = ChatService.MEMORY_CMD.fullmatch(text)
        if memory_match:
            key = memory_match.group("key").strip()
            value = memory_match.group("value").strip()
            item = MemoryService.save_item(db, MemoryCreate(key=key, value=value))
            return f"Lo recordaré: {item.key} = {item.value}"

        if "inventario" in text_l or "qué tengo" in text_l or "que tengo" in text_l:
            products = ProductService.list_products(db)
            if not products:
                return "Tu inventario está vacío todavía."
            lines = [f"- {p.name}: {p.stock_current} {p.unit} ({p.location or 'sin ubicación'})" for p in products]
            return "Esto es lo que tienes ahora:\n" + "\n".join(lines)

        if "alerta" in text_l or "por vencer" in text_l or "falta comprar" in text_l:
            alerts = AlertService.build_alerts(db)
            chunks: list[str] = []
            if alerts.low_stock:
                chunks.append(
                    "Stock bajo:\n" + "\n".join(
                        f"- {item.product_name}: {item.current_stock}/{item.minimum_stock} {item.unit}"
                        for item in alerts.low_stock
                    )
                )
            if alerts.expiring_soon:
                chunks.append(
                    "Próximos a vencer:\n" + "\n".join(
                        f"- {item.product_name}: vence el {item.expiration_date}"
                        for item in alerts.expiring_soon
                    )
                )
            return "\n\n".join(chunks) if chunks else "Todo está bien por ahora: sin stock bajo ni productos por vencer pronto."

        if "memoria" in text_l or "qué recuerdas" in text_l or "que recuerdas" in text_l:
            items = MemoryService.list_items(db)
            if not items:
                return "Aún no tengo recuerdos guardados sobre tus preferencias."
            return "Esto recuerdo de ti:\n" + "\n".join(f"- {item.key}: {item.value}" for item in items)

        return (
            "Puedo ayudarte con comandos como:\n"
            "- agregar producto Leche, cantidad 2, unidad litro, categoria Lacteos, ubicacion Refrigerador, stock_minimo 1, vencimiento 2026-03-25\n"
            "- comprar 2 Leche\n"
            "- consumir 1 Leche\n"
            "- ver inventario\n"
            "- ver alertas\n"
            "- recuerda marca favorita: Gloria"
        )

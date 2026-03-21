import re
import unicodedata
from datetime import datetime
from difflib import get_close_matches

from sqlalchemy.orm import Session

from app.models.schemas import ConsumptionCreate, MemoryCreate, ProductCreate, PurchaseCreate
from app.services.alert_service import AlertService
from app.services.consumption_service import ConsumptionService
from app.services.memory_service import MemoryService
from app.services.product_service import ProductService
from app.services.purchase_service import PurchaseService


class ChatService:
    ACTION_ADD = ("agregar", "anadir", "añadir", "crear", "registrar")
    ACTION_BUY = ("comprar", "compra", "adquirir", "adquiere", "reponer", "traer", "trae")
    ACTION_CONSUME = ("consumir", "consume", "gastar", "usar", "usa", "tomar", "toma", "comer", "come", "beber", "bebe")

    INVENTORY_HINTS = ("inventario", "stock", "que tengo", "qué tengo", "lista de productos", "productos tengo")
    ALERT_HINTS = ("alerta", "alertas", "por vencer", "vencimiento", "falta comprar", "bajo stock")
    MEMORY_HINTS = ("memoria", "que recuerdas", "qué recuerdas", "recuerdas de mi", "recuerdas de mí")
    GREETING_HINTS = ("hola", "buenos dias", "buenas tardes", "buenas noches", "que tal", "qué tal", "hey", "hi")
    THANKS_HINTS = ("gracias", "muchas gracias", "te agradezco")
    GOODBYE_HINTS = ("adios", "adiós", "hasta luego", "chau", "nos vemos", "bye")
    HELP_HINTS = ("ayuda", "que puedes hacer", "qué puedes hacer", "como funcionas", "cómo funcionas")
    WHO_HINTS = ("quien eres", "quién eres", "como te llamas", "cómo te llamas")
    MOOD_HINTS = ("como estas", "cómo estás", "todo bien", "que tal estas", "qué tal estás")

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
    def _normalize(text: str) -> str:
        text = text.strip().lower()
        text = unicodedata.normalize("NFD", text)
        text = "".join(char for char in text if unicodedata.category(char) != "Mn")
        text = re.sub(r"[^\w\s.,:-]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _contains_any(text: str, options: tuple[str, ...]) -> bool:
        return any(option in text for option in options)

    @staticmethod
    def _extract_qty(text: str, default: float = 1.0) -> float:
        match = re.search(r"(\d+(?:[.,]\d+)?)", text)
        if not match:
            return default
        return float(match.group(1).replace(",", "."))

    @staticmethod
    def _clean_candidate_name(candidate: str) -> str:
        filler_words = {
            "de",
            "del",
            "la",
            "el",
            "los",
            "las",
            "un",
            "una",
            "unos",
            "unas",
            "por",
            "favor",
            "producto",
        }
        tokens = [token for token in ChatService._normalize(candidate).split() if token not in filler_words]
        return " ".join(tokens).strip()

    @staticmethod
    def _find_product_flexible(db: Session, raw_name: str):
        direct = ProductService.get_by_name(db, raw_name)
        if direct:
            return direct

        normalized_target = ChatService._normalize(raw_name)
        products = ProductService.list_products(db)
        if not products:
            return None

        by_normalized: dict[str, object] = {}
        for product in products:
            by_normalized[ChatService._normalize(product.name)] = product

        if normalized_target in by_normalized:
            return by_normalized[normalized_target]

        partial = [item for key, item in by_normalized.items() if normalized_target and normalized_target in key]
        if partial:
            return partial[0]

        close = get_close_matches(normalized_target, list(by_normalized.keys()), n=1, cutoff=0.6)
        if close:
            return by_normalized[close[0]]
        return None

    @staticmethod
    def _try_social_reply(text_n: str) -> str | None:
        if ChatService._contains_any(text_n, ChatService.GREETING_HINTS):
            return "¡Hola! Qué gusto ayudarte. Dime qué necesitas en casa y yo me encargo."

        if ChatService._contains_any(text_n, ChatService.MOOD_HINTS):
            return "¡Todo bien por aquí! Listo para ayudarte con compras, consumos, inventario o alertas."

        if ChatService._contains_any(text_n, ChatService.WHO_HINTS):
            return "Soy tu asistente virtual del hogar. Te ayudo a registrar productos, compras, consumos, alertas y recuerdos."

        if ChatService._contains_any(text_n, ChatService.THANKS_HINTS):
            return "¡De nada! Estoy para ayudarte cuando quieras."

        if ChatService._contains_any(text_n, ChatService.GOODBYE_HINTS):
            return "Perfecto, quedo atento. ¡Hasta luego!"

        if ChatService._contains_any(text_n, ChatService.HELP_HINTS):
            return (
                "Puedo ayudarte de forma natural con tareas del hogar. Por ejemplo: "
                "'compra 2 leches', 'me tomé 1 yogurt', 'qué tengo en casa', "
                "'muéstrame alertas' o 'recuerda que mi bebida favorita es café'."
            )

        return None

    @staticmethod
    def _try_memory_natural(db: Session, text: str, text_n: str) -> str | None:
        exact_memory = ChatService.MEMORY_CMD.fullmatch(text)
        if exact_memory:
            key = exact_memory.group("key").strip()
            value = exact_memory.group("value").strip()
            item = MemoryService.save_item(db, MemoryCreate(key=key, value=value))
            return f"Listo, lo guardaré: {item.key} = {item.value}."

        memory_patterns = [
            r"(?:recuerda|anota|guarda)\s+que\s+(?P<key>.+?)\s+(?:es|:|=)\s+(?P<value>.+)",
            r"(?:acuerdate|acuérdate)\s+de\s+(?P<key>.+?)\s+(?:es|:|=)\s+(?P<value>.+)",
        ]
        for pattern in memory_patterns:
            match = re.search(pattern, text_n)
            if match:
                key = match.group("key").strip()
                value = match.group("value").strip()
                item = MemoryService.save_item(db, MemoryCreate(key=key, value=value))
                return f"Perfecto, lo recordaré: {item.key} = {item.value}."
        return None

    @staticmethod
    def _try_buy_or_consume(db: Session, text: str, text_n: str, consume: bool) -> str | None:
        regex = ChatService.CONSUME_CMD if consume else ChatService.BUY_CMD
        match = regex.fullmatch(text)
        qty: float
        name: str

        if match:
            qty = float(match.group("qty"))
            name = match.group("name").strip()
        else:
            actions = ChatService.ACTION_CONSUME if consume else ChatService.ACTION_BUY
            if not ChatService._contains_any(text_n, actions):
                return None

            qty = ChatService._extract_qty(text_n, default=1.0)
            action_pattern = "|".join(actions)
            reduced = re.sub(rf"\b(?:{action_pattern})\b", " ", text_n)
            reduced = re.sub(r"\b\d+(?:[.,]\d+)?\b", " ", reduced)
            name = ChatService._clean_candidate_name(reduced)
            if not name:
                return "Te entendí la acción, pero no el nombre del producto. Dímelo otra vez, por ejemplo: comprar 2 leche."

        product = ChatService._find_product_flexible(db, name)
        if not product:
            return f"No encontré el producto '{name}'. Si quieres, primero lo registramos."

        if consume:
            try:
                ConsumptionService.register_consumption(
                    db,
                    product,
                    ConsumptionCreate(product_name=product.name, quantity=qty),
                )
            except ValueError as exc:
                return str(exc)
            return f"Hecho. Registré consumo de {qty} {product.unit} de {product.name}. Ahora quedan {product.stock_current} {product.unit}."

        PurchaseService.register_purchase(
            db,
            product,
            PurchaseCreate(product_name=product.name, quantity=qty),
        )
        return f"Perfecto. Registré compra de {qty} {product.unit} de {product.name}. Ahora tienes {product.stock_current} {product.unit}."

    @staticmethod
    def _try_create_product(db: Session, text: str, text_n: str) -> str | None:
        strict_match = ChatService.PRODUCT_CMD.fullmatch(text)
        if strict_match:
            data = strict_match.groupdict()
            product = ProductService.get_by_name(db, data["name"].strip())
            if product:
                return f"El producto {product.name} ya existe. Si quieres, te ayudo a actualizarlo."

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
            return f"Listo. Creé {created.name} con stock {created.stock_current} {created.unit}."

        if not ChatService._contains_any(text_n, ChatService.ACTION_ADD):
            return None

        create_pattern = (
            r"(?:agregar|anadir|añadir|crear|registrar)\s+"
            r"(?:un\s+|una\s+|el\s+|la\s+)?"
            r"(?:producto\s+)?"
            r"(?P<name>[^,]+)"
        )
        match = re.search(create_pattern, text_n)
        if not match:
            return None

        name = match.group("name").strip().title()
        if not name:
            return "Te entendí que quieres agregar un producto, pero no capté el nombre."

        existing = ChatService._find_product_flexible(db, name)
        if existing:
            return f"{existing.name} ya existe. Si quieres, te ayudo a actualizar su stock."

        qty = ChatService._extract_qty(text_n, default=0.0)
        created = ProductService.create_product(
            db,
            ProductCreate(
                name=name,
                stock_current=qty,
                unit="unidad",
                stock_minimum=1,
            ),
        )
        return f"Genial. Registré el producto {created.name} con stock inicial {created.stock_current} {created.unit}."

    @staticmethod
    def reply(db: Session, message: str) -> str:
        text = message.strip()
        text_n = ChatService._normalize(text)

        social_reply = ChatService._try_social_reply(text_n)
        if social_reply:
            return social_reply

        product_created = ChatService._try_create_product(db, text, text_n)
        if product_created:
            return product_created

        buy_reply = ChatService._try_buy_or_consume(db, text, text_n, consume=False)
        if buy_reply:
            return buy_reply

        consume_reply = ChatService._try_buy_or_consume(db, text, text_n, consume=True)
        if consume_reply:
            return consume_reply

        memory_reply = ChatService._try_memory_natural(db, text, text_n)
        if memory_reply:
            return memory_reply

        if ChatService._contains_any(text_n, ChatService.INVENTORY_HINTS):
            products = ProductService.list_products(db)
            if not products:
                return "Tu inventario está vacío todavía."
            lines = [f"- {p.name}: {p.stock_current} {p.unit} ({p.location or 'sin ubicación'})" for p in products]
            return "Esto es lo que tienes en casa:\n" + "\n".join(lines)

        if ChatService._contains_any(text_n, ChatService.ALERT_HINTS):
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

        if ChatService._contains_any(text_n, ChatService.MEMORY_HINTS):
            items = MemoryService.list_items(db)
            if not items:
                return "Aún no tengo recuerdos guardados sobre tus preferencias."
            return "Esto recuerdo de ti:\n" + "\n".join(f"- {item.key}: {item.value}" for item in items)

        return (
            "Todavía no capté esa parte, pero sí puedo ayudarte si me lo dices más directo. "
            "Por ejemplo: 'compra 2 leches', 'consumí 1 yogurt', 'qué tengo en casa', "
            "'ver alertas' o 'recuerda que mi bebida favorita es café'."
        )

import re
import unicodedata
from datetime import datetime
from difflib import get_close_matches

from sqlalchemy.orm import Session

from app.models.schemas import ConsumptionCreate, MemoryCreate, ProductCreate, PurchaseCreate
from app.services.alert_service import AlertService
from app.services.consumption_service import ConsumptionService
from app.services.inventory_service import InventoryService
from app.services.memory_service import MemoryService
from app.services.product_service import ProductService
from app.services.purchase_service import PurchaseService

# ---------------------------------------------------------------------------
# Pending-action state (single-user home assistant — in-memory is fine)
# ---------------------------------------------------------------------------
_PENDING: dict = {}


class ChatService:
    ACTION_ADD = (
        "agregar",
        "agrega",
        "agregame",
        "anadir",
        "añadir",
        "anade",
        "añade",
        "crear",
        "crea",
        "registrar",
        "registra",
        "mete",
        "pon",
        "sumar",
    )
    ACTION_BUY = ("comprar", "compra", "adquirir", "adquiere", "reponer", "traer", "trae")
    ACTION_CONSUME = (
        "consumir", "consume", "consumí", "consumi",
        "gastar", "gaste", "gasté",
        "usar", "use", "usé", "usa",
        "tomar", "tome", "tomé", "toma",
        "comer", "comi", "comí", "come",
        "beber", "bebi", "bebí", "bebe",
    )

    INVENTORY_HINTS = ("inventario", "stock", "que tengo", "qué tengo", "lista de productos", "productos tengo")
    ALERT_HINTS = ("alerta", "alertas", "por vencer", "vencimiento", "falta comprar", "bajo stock")
    MEMORY_HINTS = ("memoria", "que recuerdas", "qué recuerdas", "recuerdas de mi", "recuerdas de mí")
    # Confirmation / denial
    CONFIRM_HINTS = (
        "si", "sí", "claro", "dale", "ok", "afirmativo", "por supuesto",
        "adelante", "hazlo", "crealo", "créalo", "si quiero", "sí quiero",
        "eso", "exacto", "correcto",
    )
    DENY_HINTS = (
        "no", "nope", "no gracias", "cancelar", "olvida", "olvidalo",
        "olvídalo", "no quiero", "dejalo", "déjalo",
    )

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
    def _contains_any(text: str, options: tuple) -> bool:
        return any(option in text for option in options)

    @staticmethod
    def _extract_qty(text: str, default: float = 1.0) -> float:
        match = re.search(r"(\d+(?:[.,]\d+)?)", text)
        if not match:
            return default
        return float(match.group(1).replace(",", "."))

    @staticmethod
    def _fmt_num(value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")

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
        cleaned = " ".join(tokens).strip()
        cleaned = re.sub(
            r"\b(?:al|a|en|para\s+el|para\s+la)\s+(?:inventario|despensa|refri|refrigerador|cocina)\b",
            "",
            cleaned,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
        return cleaned

    @staticmethod
    def _find_product_flexible(db: Session, raw_name: str):
        direct = ProductService.get_by_name(db, raw_name)
        if direct:
            return direct

        normalized_target = ChatService._normalize(raw_name)
        products = ProductService.list_products(db)
        if not products:
            return None

        by_normalized: dict = {}
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

    # ------------------------------------------------------------------
    # Pending-confirmation handler
    # ------------------------------------------------------------------
    @staticmethod
    def _try_pending_confirmation(db: Session, text_n: str) -> str | None:
        """Execute or cancel a previously stored pending action."""
        global _PENDING
        import json
        from app.models.entities import MemoryItem

        def _clear_pending_state() -> None:
            try:
                db.query(MemoryItem).filter_by(key="__pending_create__").delete()
                db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
            _PENDING.clear()
        
        # Busca estado pendiente en DB first
        pending = None
        try:
            item = db.query(MemoryItem).filter_by(key="__pending_create__").first()
            if item:
                pending = json.loads(item.value)
        except:
            pass
        
        # Fallback a memoria global
        if not pending:
            if not _PENDING:
                return None
            pending = dict(_PENDING)

        if ChatService._contains_any(text_n, ChatService.CONFIRM_HINTS):
            if pending.get("action") == "create_product":
                name = pending["name"]
                qty = pending.get("qty", 0.0)
                unit = pending.get("unit", "unidad")

                try:
                    existing = ChatService._find_product_flexible(db, name)
                    if existing:
                        if qty > 0:
                            before = existing.stock_current
                            InventoryService.increase_stock(db, existing, qty)
                            _clear_pending_state()
                            return (
                                f"{existing.name} ya existía. Sumé {ChatService._fmt_num(qty)} {existing.unit}. "
                                f"Antes tenías {ChatService._fmt_num(before)} y ahora tienes "
                                f"{ChatService._fmt_num(existing.stock_current)} {existing.unit}."
                            )

                        _clear_pending_state()
                        return (
                            f"{existing.name} ya estaba en el inventario. "
                            f"Cuando lo compres, avísame y sumo el stock."
                        )

                    created = ProductService.create_product(
                        db,
                        ProductCreate(
                            name=name,
                            stock_current=qty,
                            unit=unit,
                            stock_minimum=1,
                        ),
                    )
                    _clear_pending_state()

                    stock_str = ChatService._fmt_num(created.stock_current)
                    if qty > 0:
                        return (
                            f"¡Listo! Agregué {created.name} al inventario "
                            f"con {stock_str} {created.unit} de entrada."
                        )
                    return (
                        f"¡Listo! Creé {created.name} en el inventario. "
                        f"Cuando lo compres, avísame y sumo el stock."
                    )
                except Exception:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    return (
                        "Se me complicó confirmar ese producto en este momento. "
                        "Inténtalo otra vez con: 'agrega nombre_del_producto'."
                    )

            return "Acción confirmada, pero no encontré qué hacer. Cuéntame de nuevo."

        if ChatService._contains_any(text_n, ChatService.DENY_HINTS):
            pending_name = pending.get("name", "el producto")
            _clear_pending_state()
            
            return f"Entendido, no creé {pending_name}. Avísame si cambias de idea."

        # Pending exists but user said something unrelated — remind them.
        pending_name = pending.get("name", "el producto")
        return (
            f"Antes de seguir: ¿quieres que cree {pending_name} en el inventario? "
            f"Dime sí o no."
        )

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
                "Puedo ayudarte de forma natural. Por ejemplo: "
                "'compré 2 leches', 'gasté 1 yogurt', 'agrega 3 huevos', "
                "'qué tengo en casa', 'ver alertas' o "
                "'recuerda que mi bebida favorita es café'."
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
                if consume:
                    return "Entendí que usaste algo, pero no capté qué. Dímelo así: 'gasté 1 leche'."
                return "Entendí que compraste algo, pero no capté qué. Dímelo así: 'compré 2 leches'."

        product = ChatService._find_product_flexible(db, name)
        if not product:
            return (
                f"No encontré '{name}' en el inventario. "
                f"¿Quieres que lo registre? Dímelo con: 'agrega {name}'."
            )

        qty_str = ChatService._fmt_num(qty)
        unit = product.unit

        if consume:
            try:
                ConsumptionService.register_consumption(
                    db,
                    product,
                    ConsumptionCreate(product_name=product.name, quantity=qty),
                )
            except ValueError as exc:
                return str(exc)
            remaining = ChatService._fmt_num(product.stock_current)
            return (
                f"Anotado. Gasté {qty_str} {unit} de {product.name}. "
                f"Te quedan {remaining} {unit}."
            )

        PurchaseService.register_purchase(
            db,
            product,
            PurchaseCreate(product_name=product.name, quantity=qty),
        )
        total = ChatService._fmt_num(product.stock_current)
        return (
            f"Compré {qty_str} {unit} de {product.name}. "
            f"Ahora tienes {total} {unit} en casa."
        )

    @staticmethod
    def _try_add_or_create(db: Session, text: str, text_n: str) -> str | None:
        """
        - Strict PRODUCT_CMD format  → always create.
        - Natural 'agrega X':
            • Product exists   → increase stock (counter).
            • Product missing  → ask confirmation before creating.
        """
        # ── Strict structured command ────────────────────────────────
        strict_match = ChatService.PRODUCT_CMD.fullmatch(text)
        if strict_match:
            data = strict_match.groupdict()
            product = ProductService.get_by_name(db, data["name"].strip())
            if product:
                return (
                    f"{product.name} ya estaba en el inventario. "
                    f"Si quieres sumar stock, dime: 'agrega X {product.name}'."
                )

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
            return f"Listo. Creé {created.name} con stock {ChatService._fmt_num(created.stock_current)} {created.unit}."

        # ── Natural language ─────────────────────────────────────────
        if not ChatService._contains_any(text_n, ChatService.ACTION_ADD):
            return None

        create_pattern = (
            r"(?:agregar|agrega|agregame|anadir|añadir|anade|añade|"
            r"crear|crea|registrar|registra|mete|pon|sumar)\s+"
            r"(?:un\s+|una\s+|el\s+|la\s+)?"
            r"(?:producto\s+)?"
            r"(?P<name>[^,]+)"
        )
        match = re.search(create_pattern, text_n)
        if not match:
            return None

        qty = ChatService._extract_qty(text_n, default=0.0)
        raw_candidate = match.group("name")
        name = ChatService._clean_candidate_name(raw_candidate).title()
        if not name:
            return "Entendí que quieres agregar algo, pero no capté el nombre. ¿Puedes repetirlo?"

        existing = ChatService._find_product_flexible(db, name)

        # ── Product EXISTS → increase stock ──────────────────────────
        if existing:
            add_qty = qty if qty > 0 else 1.0
            before = existing.stock_current
            InventoryService.increase_stock(db, existing, add_qty)
            after = ChatService._fmt_num(existing.stock_current)
            qty_str = ChatService._fmt_num(add_qty)
            unit = existing.unit
            return (
                f"Sumé {qty_str} {unit} de {existing.name} al inventario. "
                f"Antes tenías {ChatService._fmt_num(before)} y ahora tienes {after} {unit}."
            )

        # ── Product DOES NOT EXIST → ask confirmation ─────────────────
        import json
        from app.models.schemas import MemoryCreate
        
        _PENDING["action"] = "create_product"
        _PENDING["name"] = name
        _PENDING["qty"] = qty
        _PENDING["unit"] = "unidad"

        # Save pending state to DB for persistence across requests
        try:
            from app.services.memory_service import MemoryService
            MemoryService.save_item(
                db,
                MemoryCreate(
                    key="__pending_create__",
                    value=json.dumps(_PENDING)
                )
            )
        except Exception as e:
            print(f"Warning: Could not save pending state to DB: {e}")

        qty_hint = f" con {ChatService._fmt_num(qty)} unidades de entrada" if qty > 0 else ""
        return (
            f"No tengo {name} en el inventario. "
            f"¿Quieres que lo cree{qty_hint}? Dime sí o no."
        )

    @staticmethod
    def reply(db: Session, message: str) -> str:
        text = message.strip()
        text_n = ChatService._normalize(text)

        # 1. Pending confirmation takes priority
        pending_reply = ChatService._try_pending_confirmation(db, text_n)
        if pending_reply:
            return pending_reply

        # 2. Social / conversational
        social_reply = ChatService._try_social_reply(text_n)
        if social_reply:
            return social_reply

        # 3. Add to / create in inventory
        add_reply = ChatService._try_add_or_create(db, text, text_n)
        if add_reply:
            return add_reply

        # 4. Buy
        buy_reply = ChatService._try_buy_or_consume(db, text, text_n, consume=False)
        if buy_reply:
            return buy_reply

        # 5. Consume
        consume_reply = ChatService._try_buy_or_consume(db, text, text_n, consume=True)
        if consume_reply:
            return consume_reply

        # 6. Memory save
        memory_reply = ChatService._try_memory_natural(db, text, text_n)
        if memory_reply:
            return memory_reply

        # 7. Inventory list
        if ChatService._contains_any(text_n, ChatService.INVENTORY_HINTS):
            products = ProductService.list_products(db)
            if not products:
                return "Tu inventario está vacío todavía."
            lines = [
                f"- {p.name}: {ChatService._fmt_num(p.stock_current)} {p.unit} ({p.location or 'sin ubicación'})"
                for p in products
            ]
            return "Esto es lo que tienes en casa:\n" + "\n".join(lines)

        # 8. Alerts
        if ChatService._contains_any(text_n, ChatService.ALERT_HINTS):
            alerts = AlertService.build_alerts(db)
            chunks: list = []
            if alerts.low_stock:
                chunks.append(
                    "Stock bajo:\n" + "\n".join(
                        f"- {item.product_name}: te quedan {ChatService._fmt_num(item.current_stock)} "
                        f"y el mínimo es {ChatService._fmt_num(item.minimum_stock)} {item.unit}"
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
            return "\n\n".join(chunks) if chunks else "Todo está bien: sin stock bajo ni productos por vencer pronto."

        # 9. Memory recall
        if ChatService._contains_any(text_n, ChatService.MEMORY_HINTS):
            items = MemoryService.list_items(db)
            if not items:
                return "Aún no tengo recuerdos guardados sobre tus preferencias."
            return "Esto recuerdo de ti:\n" + "\n".join(f"- {item.key}: {item.value}" for item in items)

        return (
            "No entendí eso del todo, pero puedo ayudarte. Por ejemplo: "
            "'compré 2 leches', 'gasté 1 yogurt', 'agrega 3 huevos', "
            "'qué tengo en casa', 'ver alertas' o "
            "'recuerda que mi bebida favorita es café'."
        )

import re
import unicodedata
from datetime import datetime
from difflib import get_close_matches

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.schemas import ConsumptionCreate, MemoryCreate, ProductCreate, ProductUpdate, PurchaseCreate
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
    DEFAULT_CATEGORY = "General"
    DEFAULT_LOCATION = "Sin ubicación"

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
    EXPIRING_HINTS = ("productos por vencer", "que esta por vencer", "qué está por vencer", "por vencer")
    SHOPPING_LIST_HINTS = ("lista de compras", "que falta comprar", "qué falta comprar", "compras pendientes")
    DAILY_ALERT_HINTS = ("alertas del dia", "alertas del día", "resumen del dia", "resumen del día")
    EXPENSE_HINTS = ("gastos del hogar", "gastos de la casa", "cuanto he gastado", "cuánto he gastado", "mis gastos")
    IMPORTANT_MEMORY_HINTS = ("recuerdos importantes", "recuerdos clave", "cosas importantes que recuerdas")
    CONSUME_FIRST_HINTS = ("que consumir primero", "qué consumir primero", "que debo consumir primero", "qué debo consumir primero")
    RECIPE_HINTS = ("recetas segun inventario", "recetas según inventario", "que puedo cocinar", "qué puedo cocinar", "con lo que hay en la refri")
    DAILY_SUMMARY_HINTS = ("resumen diario", "resumen del dia", "resumen del día", "resumen diario automatico", "resumen diario automático")
    HOUSEHOLD_REMINDER_HINTS = ("recordatorios del hogar", "recordatorio del hogar", "recordatorios hogar")

    HOUSEHOLD_DEFAULT_REMINDERS = (
        "Revisar gas",
        "Revisar agua",
        "Revisar luz",
        "Programar limpieza",
        "Sacar basura",
        "Revisar pagos pendientes",
    )

    RECIPE_BOOK = (
        {"name": "Tortilla de huevo", "ingredients": ("huevo",)},
        {"name": "Avena con leche", "ingredients": ("avena", "leche")},
        {"name": "Yogurt con fruta", "ingredients": ("yogurt", "papaya", "platano", "banana", "fresa")},
        {"name": "Arroz con huevo", "ingredients": ("arroz", "huevo")},
        {"name": "Batido de papaya", "ingredients": ("papaya", "leche")},
    )
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
    def _format_days(days: int | None) -> str:
        if days is None:
            return "sin fecha registrada"
        if days < 0:
            overdue = abs(days)
            return f"vencido hace {overdue} día{'s' if overdue != 1 else ''}"
        if days == 0:
            return "vence hoy"
        return f"vence en {days} día{'s' if days != 1 else ''}"

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
                name = str(pending.get("name", "")).strip().title()
                if not name:
                    _clear_pending_state()
                    return "No pude identificar qué producto querías crear. Dímelo otra vez con: 'agrega nombre_del_producto'."
                name = re.sub(r"\s+", " ", name).strip()[:120]
                if not name:
                    _clear_pending_state()
                    return "No pude identificar qué producto querías crear. Dímelo otra vez con: 'agrega nombre_del_producto'."

                try:
                    qty = float(pending.get("qty", 0.0) or 0.0)
                except Exception:
                    qty = 0.0
                if qty < 0:
                    qty = 0.0
                if qty > 1_000_000:
                    qty = 1_000_000.0

                unit = str(pending.get("unit", "unidad") or "unidad").strip() or "unidad"
                unit = re.sub(r"\s+", " ", unit).strip()[:20] or "unidad"

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

                    try:
                        created = ProductService.create_product(
                            db,
                            ProductCreate(
                                name=name,
                                category=ChatService.DEFAULT_CATEGORY,
                                stock_current=qty,
                                unit=unit,
                                stock_minimum=1,
                                location=ChatService.DEFAULT_LOCATION,
                            ),
                        )
                    except IntegrityError:
                        db.rollback()
                        existing_after_conflict = ChatService._find_product_flexible(db, name)
                        if existing_after_conflict:
                            if qty > 0:
                                before = existing_after_conflict.stock_current
                                InventoryService.increase_stock(db, existing_after_conflict, qty)
                                _clear_pending_state()
                                return (
                                    f"{existing_after_conflict.name} ya existía. "
                                    f"Sumé {ChatService._fmt_num(qty)} {existing_after_conflict.unit}. "
                                    f"Antes tenías {ChatService._fmt_num(before)} y ahora tienes "
                                    f"{ChatService._fmt_num(existing_after_conflict.stock_current)} {existing_after_conflict.unit}."
                                )
                            _clear_pending_state()
                            return (
                                f"{existing_after_conflict.name} ya estaba en el inventario. "
                                f"Cuando lo compres, avísame y sumo el stock."
                            )
                        raise

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
                    _clear_pending_state()
                    return (
                        "Se me complicó confirmar ese producto en este momento. "
                        "Inténtalo otra vez con: 'agrega nombre_del_producto'."
                    )

            return "Acción confirmada, pero no encontré qué hacer. Cuéntame de nuevo."

        if ChatService._contains_any(text_n, ChatService.DENY_HINTS):
            pending_name = pending.get("name", "el producto")
            _clear_pending_state()
            
            return f"Entendido, no creé {pending_name}. Avísame si cambias de idea."

        # If user starts a new add/create command, replace stale pending instead of blocking.
        if ChatService._contains_any(text_n, ChatService.ACTION_ADD):
            _clear_pending_state()
            return None

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
            r"(?:agrega|agregar|anota|guarda)\s+(?:un\s+)?recuerdo\s+(?:que\s+)?(?P<key>.+?)\s+(?:es|:|=)\s+(?P<value>.+)",
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
    def _try_update_location(db: Session, text_n: str) -> str | None:
        patterns = [
            r"(?:cambia|cambiar|actualiza|actualizar|modifica|modificar)\s+(?:la\s+)?(?:ubicacion|ubicación|lugar)\s+de\s+(?P<name>.+?)\s+(?:a|en)\s+(?P<location>.+)",
            r"(?:mueve|mover|pon)\s+(?P<name>.+?)\s+(?:a|al|en)\s+(?P<location>.+)",
        ]

        match = None
        for pattern in patterns:
            match = re.search(pattern, text_n)
            if match:
                break

        if not match:
            return None

        raw_name = match.group("name")
        raw_location = match.group("location")

        name = ChatService._clean_candidate_name(raw_name).title()
        name = re.sub(r"\s+", " ", name).strip()[:120]

        location = ChatService._clean_candidate_name(raw_location).title()
        location = re.sub(r"\s+", " ", location).strip(" ,.-")[:50]

        if not name or not location:
            return "Entendí que quieres cambiar una ubicación, pero me faltó producto o lugar. Ejemplo: 'cambia la ubicación de leche a cocina'."

        product = ChatService._find_product_flexible(db, name)
        if not product:
            return f"No encontré '{name}' en el inventario para cambiar su ubicación."

        previous = product.location or "Sin ubicación"
        ProductService.update_product(db, product, ProductUpdate(location=location))
        return f"Listo. Cambié la ubicación de {product.name} de {previous} a {product.location}."

    @staticmethod
    def _try_delete_memory(db: Session, text_n: str) -> str | None:
        patterns = [
            r"(?:olvida|elimina|eliminar|borra|borrar|quita)\s+que\s+(?P<key>.+?)\s+(?:es|:|=)\s+(?P<value>.+)",
            r"(?:elimina|eliminar|borra|borrar|quita)\s+(?:el\s+|un\s+)?recuerdo\s+(?:de\s+)?(?P<key>.+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text_n)
            if not match:
                continue

            key = match.group("key").strip()
            item = MemoryService.get_by_key(db, key)
            if not item:
                return f"No encontré un recuerdo guardado con '{key}'."

            value = match.groupdict().get("value")
            if value and ChatService._normalize(item.value) != ChatService._normalize(value):
                return f"Encontré '{item.key}', pero su valor guardado no coincide con '{value.strip()}'."

            deleted_key = item.key
            MemoryService.delete_item(db, item)
            return f"Listo. Eliminé el recuerdo '{deleted_key}'."

        return None

    @staticmethod
    def _try_delete_product(db: Session, text_n: str) -> str | None:
        patterns = [
            r"(?:elimina|eliminar|borra|borrar|quita|quitar|saca|sacar)\s+(?:del\s+|de\s+mi\s+)?(?:inventario\s+)?(?:el\s+|la\s+|producto\s+)?(?P<name>.+)",
            r"(?:ya\s+no\s+quiero|ya\s+no\s+deseo)\s+(?:tener\s+)?(?P<name>.+?)\s+(?:en\s+el\s+inventario|en\s+casa)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text_n)
            if not match:
                continue

            raw_name = match.group("name")
            raw_name = re.sub(r"\b(?:del|de mi|en el)\s+(?:inventario|casa)\b", "", raw_name).strip()
            name = ChatService._clean_candidate_name(raw_name).title()
            name = re.sub(r"\s+", " ", name).strip()[:120]
            if not name:
                return "Entendí que quieres eliminar un producto, pero no capté cuál."

            product = ChatService._find_product_flexible(db, name)
            if not product:
                return f"No encontré '{name}' en el inventario para eliminarlo."

            deleted_name = product.name
            ProductService.delete_product(db, product)
            return f"Listo. Eliminé {deleted_name} del inventario."

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
                    category=(data.get("category") or ChatService.DEFAULT_CATEGORY),
                    location=(data.get("location") or ChatService.DEFAULT_LOCATION),
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
        name = re.sub(r"\s+", " ", name).strip()[:120]
        if not name:
            return "Entendí que quieres agregar algo, pero no capté el nombre. ¿Puedes repetirlo?"

        if qty < 0:
            qty = 0.0
        if qty > 1_000_000:
            qty = 1_000_000.0

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
    def _build_inventory_reply(db: Session) -> str:
        AlertService.refresh_product_statuses(db)
        products = ProductService.list_products(db)
        if not products:
            return "Tu inventario está vacío todavía."

        lines = []
        for product in products:
            status_hint = ""
            if product.status == "vencido":
                status_hint = " [VENCIDO]"
            elif product.expiration_date:
                days_until_expiration = (product.expiration_date - datetime.utcnow().date()).days
                if days_until_expiration <= 3:
                    status_hint = f" [{ChatService._format_days(days_until_expiration)}]"

            lines.append(
                f"- {product.name}: {ChatService._fmt_num(product.stock_current)} {product.unit} ({product.location or 'sin ubicación'}){status_hint}"
            )
        return "Esto es lo que tienes en casa:\n" + "\n".join(lines)

    @staticmethod
    def _build_expiring_reply(db: Session) -> str:
        alerts = AlertService.build_alerts(db)
        if not alerts.expired and not alerts.expiring_soon:
            return "No tienes productos vencidos ni próximos a vencer por ahora."

        chunks: list[str] = []
        if alerts.expired:
            chunks.append(
                "Vencidos:\n" + "\n".join(
                    f"- {item.product_name}: {ChatService._format_days(item.days_until_expiration)}"
                    for item in alerts.expired
                )
            )
        if alerts.expiring_soon:
            chunks.append(
                "Por vencer:\n" + "\n".join(
                    f"- {item.product_name}: {ChatService._format_days(item.days_until_expiration)}"
                    for item in alerts.expiring_soon
                )
            )
        return "\n\n".join(chunks)

    @staticmethod
    def _build_shopping_list_reply(db: Session) -> str:
        alerts = AlertService.build_alerts(db)
        if not alerts.shopping_list:
            return "Tu lista de compras está vacía por ahora."

        lines = [
            f"- {item.product_name}: compra al menos {ChatService._fmt_num(item.needed_quantity)} {item.unit}"
            for item in alerts.shopping_list
        ]
        return "Lista de compras:\n" + "\n".join(lines)

    @staticmethod
    def _build_daily_alerts_reply(db: Session) -> str:
        alerts = AlertService.build_alerts(db)
        chunks: list[str] = []

        if alerts.expired:
            chunks.append(f"- {len(alerts.expired)} producto(s) vencido(s)")
        if alerts.expiring_soon:
            chunks.append(f"- {len(alerts.expiring_soon)} producto(s) por vencer")
        if alerts.low_stock:
            chunks.append(f"- {len(alerts.low_stock)} producto(s) con stock bajo")
        if alerts.consume_first:
            next_item = alerts.consume_first[0]
            chunks.append(
                f"- Consume primero {next_item.product_name}: {ChatService._format_days(next_item.days_until_expiration)}"
            )

        if not chunks:
            return "Alertas del día: todo está en orden hoy."
        return "Alertas del día:\n" + "\n".join(chunks)

    @staticmethod
    def _build_expense_reply(db: Session) -> str:
        summary = PurchaseService.summarize_expenses(db, days=30)
        if summary.purchases_count == 0:
            return "Aún no tengo compras registradas para calcular gastos del hogar."
        if summary.items_with_price == 0:
            return (
                f"Tienes {summary.purchases_count} compra(s) registradas en los últimos {summary.period_days} días, "
                "pero ninguna con precio. Si registras unit_price podré calcular el gasto total."
            )
        return (
            f"Gastos del hogar en los últimos {summary.period_days} días: {ChatService._fmt_num(summary.total_amount)}. "
            f"Tomé {summary.items_with_price} compra(s) con precio de un total de {summary.purchases_count}."
        )

    @staticmethod
    def _build_important_memories_reply(db: Session) -> str:
        items = MemoryService.list_items(db)
        if not items:
            return "Aún no tengo recuerdos guardados sobre tus preferencias."
        top_items = items[:5]
        return "Recuerdos importantes:\n" + "\n".join(f"- {item.key}: {item.value}" for item in top_items)

    @staticmethod
    def _build_consume_first_reply(db: Session) -> str:
        alerts = AlertService.build_alerts(db)
        if not alerts.consume_first:
            return "No veo productos con vencimiento cercano para priorizar consumo."
        lines = [
            f"- {item.product_name}: {ChatService._format_days(item.days_until_expiration)}"
            for item in alerts.consume_first
        ]
        return "Te sugiero consumir primero:\n" + "\n".join(lines)

    @staticmethod
    def _build_recipes_reply(db: Session) -> str:
        products = ProductService.list_products(db)
        available_names = [ChatService._normalize(product.name) for product in products if product.stock_current > 0]
        if not available_names:
            return "Tu inventario está vacío. Cuando agregues productos, te sugiero recetas con lo que tengas."

        suggestions: list[str] = []
        for recipe in ChatService.RECIPE_BOOK:
            matched = []
            for ingredient in recipe["ingredients"]:
                if any(ingredient in product_name for product_name in available_names):
                    matched.append(ingredient)

            ingredients_count = len(recipe["ingredients"])
            min_required = 1 if ingredients_count == 1 else 2
            if len(set(matched)) >= min_required:
                suggestions.append(recipe["name"])

        if not suggestions:
            return (
                "Con lo que hay en la refri aún no detecto una receta clara de mi lista. "
                "Si agregas más ingredientes, te sugiero opciones concretas."
            )

        lines = [f"- {name}" for name in suggestions[:5]]
        return "Con lo que hay en la refri puedes cocinar:\n" + "\n".join(lines)

    @staticmethod
    def _list_household_reminders(db: Session) -> list[str]:
        items = MemoryService.list_items(db)
        custom = [item.value for item in items if item.key.startswith("hogar:")]
        reminders = list(ChatService.HOUSEHOLD_DEFAULT_REMINDERS)
        reminders.extend(custom)
        # Keep order but remove duplicates.
        seen = set()
        unique = []
        for reminder in reminders:
            marker = ChatService._normalize(reminder)
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(reminder)
        return unique

    @staticmethod
    def _build_household_reminders_reply(db: Session) -> str:
        reminders = ChatService._list_household_reminders(db)
        return "Recordatorios del hogar:\n" + "\n".join(f"- {item}" for item in reminders)

    @staticmethod
    def _try_save_household_reminder(db: Session, text: str, text_n: str) -> str | None:
        patterns = [
            r"(?:agrega|agregar|anota|guardar|guarda|crea|crear)\s+(?:un\s+)?recordatorio\s+(?:del\s+)?hogar\s+(?P<note>.+)",
            r"(?:recu[eé]rdame|recordatorio)\s+(?:que\s+)?(?P<note>.+)",
        ]

        match = None
        for pattern in patterns:
            match = re.search(pattern, text_n)
            if match:
                break
        if not match:
            return None

        note = match.group("note").strip(" .")
        if len(note) < 3:
            return None

        normalized_note = re.sub(r"\s+", " ", note).strip()
        key_suffix = re.sub(r"[^a-z0-9\s-]", "", ChatService._normalize(normalized_note)).strip()
        key_suffix = re.sub(r"\s+", "-", key_suffix)[:80] or "general"
        key = f"hogar:{key_suffix}"

        MemoryService.save_item(db, MemoryCreate(key=key, value=normalized_note))
        return f"Listo. Guardé el recordatorio del hogar: {normalized_note}."

    @staticmethod
    def _build_daily_summary_reply(db: Session) -> str:
        alerts = AlertService.build_alerts(db)
        shopping = alerts.shopping_list
        reminders = ChatService._list_household_reminders(db)

        expired_count = len(alerts.expired)
        expiring_count = len(alerts.expiring_soon)

        missing = "nada"
        if shopping:
            missing = ", ".join(item.product_name for item in shopping[:2])

        tomorrow_buy = shopping[0].product_name if shopping else "sin compras urgentes"
        reminder_hint = reminders[0] if reminders else "sin recordatorios pendientes"

        return (
            f"Resumen diario: Hoy vencen {expired_count} producto(s), hay {expiring_count} por vencer, "
            f"faltan {missing} y mañana toca comprar {tomorrow_buy}. "
            f"Recordatorio clave: {reminder_hint}."
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

        # 3. Memory save
        memory_reply = ChatService._try_memory_natural(db, text, text_n)
        if memory_reply:
            return memory_reply

        # 4. Household reminder save
        household_save_reply = ChatService._try_save_household_reminder(db, text, text_n)
        if household_save_reply:
            return household_save_reply

        # 5. Memory delete
        delete_memory_reply = ChatService._try_delete_memory(db, text_n)
        if delete_memory_reply:
            return delete_memory_reply

        # 6. Update location
        location_reply = ChatService._try_update_location(db, text_n)
        if location_reply:
            return location_reply

        # 7. Delete from inventory
        delete_product_reply = ChatService._try_delete_product(db, text_n)
        if delete_product_reply:
            return delete_product_reply

        # 8. Add to / create in inventory
        add_reply = ChatService._try_add_or_create(db, text, text_n)
        if add_reply:
            return add_reply

        # 9. Daily summary
        if ChatService._contains_any(text_n, ChatService.DAILY_SUMMARY_HINTS):
            return ChatService._build_daily_summary_reply(db)

        # 10. Recipes
        if ChatService._contains_any(text_n, ChatService.RECIPE_HINTS):
            return ChatService._build_recipes_reply(db)

        # 11. Household reminders list
        if ChatService._contains_any(text_n, ChatService.HOUSEHOLD_REMINDER_HINTS):
            return ChatService._build_household_reminders_reply(db)

        # 12. Shopping list
        if ChatService._contains_any(text_n, ChatService.SHOPPING_LIST_HINTS):
            return ChatService._build_shopping_list_reply(db)

        # 13. Daily alerts
        if ChatService._contains_any(text_n, ChatService.DAILY_ALERT_HINTS):
            return ChatService._build_daily_alerts_reply(db)

        # 14. Expenses
        if ChatService._contains_any(text_n, ChatService.EXPENSE_HINTS):
            return ChatService._build_expense_reply(db)

        # 15. Important memories
        if ChatService._contains_any(text_n, ChatService.IMPORTANT_MEMORY_HINTS):
            return ChatService._build_important_memories_reply(db)

        # 16. Consume first
        if ChatService._contains_any(text_n, ChatService.CONSUME_FIRST_HINTS):
            return ChatService._build_consume_first_reply(db)

        # 17. Buy
        buy_reply = ChatService._try_buy_or_consume(db, text, text_n, consume=False)
        if buy_reply:
            return buy_reply

        # 18. Consume
        consume_reply = ChatService._try_buy_or_consume(db, text, text_n, consume=True)
        if consume_reply:
            return consume_reply

        # 19. Inventory list
        if ChatService._contains_any(text_n, ChatService.INVENTORY_HINTS):
            return ChatService._build_inventory_reply(db)

        # 20. Products expiring / expired
        if ChatService._contains_any(text_n, ChatService.EXPIRING_HINTS):
            return ChatService._build_expiring_reply(db)

        # 17. Alerts
        if ChatService._contains_any(text_n, ChatService.ALERT_HINTS):
            alerts = AlertService.build_alerts(db)
            chunks: list = []
            if alerts.expired:
                chunks.append(
                    "Vencidos:\n" + "\n".join(
                        f"- {item.product_name}: {ChatService._format_days(item.days_until_expiration)}"
                        for item in alerts.expired
                    )
                )
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
                        f"- {item.product_name}: {ChatService._format_days(item.days_until_expiration)}"
                        for item in alerts.expiring_soon
                    )
                )
            if alerts.consume_first:
                chunks.append(
                    "Consume primero:\n" + "\n".join(
                        f"- {item.product_name}: {ChatService._format_days(item.days_until_expiration)}"
                        for item in alerts.consume_first
                    )
                )
            return "\n\n".join(chunks) if chunks else "Todo está bien: sin stock bajo ni productos por vencer pronto."

        # 18. Memory recall
        if ChatService._contains_any(text_n, ChatService.MEMORY_HINTS):
            items = MemoryService.list_items(db)
            if not items:
                return "Aún no tengo recuerdos guardados sobre tus preferencias."
            return "Esto recuerdo de ti:\n" + "\n".join(f"- {item.key}: {item.value}" for item in items)

        return (
            "No entendí eso del todo, pero puedo ayudarte. Por ejemplo: "
            "'compré 2 leches', 'gasté 1 yogurt', 'agrega 3 huevos', "
            "'qué tengo en casa', 'productos por vencer', 'lista de compras', 'gastos del hogar' o "
            "'recuerda que mi bebida favorita es café'."
        )

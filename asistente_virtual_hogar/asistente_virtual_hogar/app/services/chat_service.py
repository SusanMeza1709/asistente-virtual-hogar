import re
import random
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
        "anotar",
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
        "ingresar",
        "ingresa",
        "sumar",
    )
    ACTION_BUY = (
        "comprar", "compra", "compre", "compré", "adquirir", "adquiere", "reponer", "traer", "trae",
        "traje", "comprame", "comprare", "compraré",
    )
    ACTION_CONSUME = (
        "consumir", "consume", "consumí", "consumi",
        "gastar", "gaste", "gasté",
        "usar", "use", "usé", "usa",
        "tomar", "tome", "tomé", "toma",
        "comer", "comi", "comí", "come",
        "beber", "bebi", "bebí", "bebe",
        "acabar", "acabe", "acabé",
    )

    INVENTORY_HINTS = (
        "inventario", "stock", "que tengo en casa", "qué tengo en casa", "lista de productos", "productos tengo",
        "que hay en casa", "qué hay en casa", "que hay en la refri", "qué hay en la refri", "mi despensa",
    )
    ALERT_HINTS = (
        "alerta", "alertas", "por vencer", "vencimiento", "falta comprar", "bajo stock", "avisos", "notificaciones",
    )
    MEMORY_HINTS = (
        "memoria", "que recuerdas", "qué recuerdas", "recuerdas de mi", "recuerdas de mí", "que sabes de mi", "qué sabes de mí",
    )
    EXPIRING_HINTS = (
        "productos por vencer",
        "que esta por vencer",
        "qué está por vencer",
        "por vencer",
        "que esta vencido",
        "qué está vencido",
        "vencidos",
    )
    SHOPPING_LIST_HINTS = (
        "lista de compras",
        "que falta comprar",
        "qué falta comprar",
        "compras pendientes",
        "que me falta comprar",
        "qué me falta comprar",
    )
    DAILY_ALERT_HINTS = (
        "alertas del dia",
        "alertas del día",
        "resumen del dia",
        "resumen del día",
        "como va el dia",
        "cómo va el día",
    )
    EXPENSE_HINTS = (
        "gastos del hogar",
        "gastos de la casa",
        "cuanto he gastado",
        "cuánto he gastado",
        "mis gastos",
        "cuanto gaste",
        "cuánto gasté",
        "cuanto gastar",
        "cuánto gastar",
        "cuanto gasto",
        "cuánto gasto",
    )
    IMPORTANT_MEMORY_HINTS = ("recuerdos importantes", "recuerdos clave", "cosas importantes que recuerdas")
    CONSUME_FIRST_HINTS = ("que consumir primero", "qué consumir primero", "que debo consumir primero", "qué debo consumir primero")
    RECIPE_HINTS = (
        "recetas segun inventario",
        "recetas según inventario",
        "que puedo cocinar",
        "qué puedo cocinar",
        "con lo que hay en la refri",
        "que cocino con lo que tengo en la refri",
        "qué cocino con lo que tengo en la refri",
        "que cocinar con lo que tengo en la refri",
        "qué cocinar con lo que tengo en la refri",
        "que cocino con lo que tengo",
        "qué cocino con lo que tengo",
        "que cocinar con lo que tengo",
        "qué cocinar con lo que tengo",
        "que recetas me recomiendas",
        "qué recetas me recomiendas",
        "recomiendame una receta",
        "recomiéndame una receta",
        "recomendar una receta",
        "recomendar algo de comer",
        "dame recetas",
    )
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

    DEFAULT_UNIT_BY_KEYWORD = {
        "huevo": "unidad",
        "huevos": "unidad",
        "leche": "tarro",
        "arroz": "kilo",
        "fresa": "kilo",
        "fresas": "kilo",
        "papaya": "kilo",
        "platano": "kilo",
        "banana": "kilo",
        "manzana": "kilo",
        "naranja": "kilo",
        "pera": "kilo",
        "uva": "kilo",
        "limon": "kilo",
        "limón": "kilo",
    }

    NATURAL_INTROS = (
        "Perfecto,",
        "Buenísimo,",
        "Claro,",
        "Listo,",
    )

    INTENT_TOKEN_ALIASES = {
        # typos
        "camviar": "cambiar",
        "canviar": "cambiar",
        "hubicacion": "ubicacion",
        "uvicacion": "ubicacion",
        "ubicaion": "ubicacion",
        "recetra": "receta",
        "resetas": "recetas",
        "rceetas": "recetas",
        "ubicasion": "ubicacion",
        "consumoo": "consumo",
        "comrpar": "comprar",
        "refri": "refri",
        "q": "que",
        "k": "que",
        # inflections / variants
        "cambia": "cambiar",
        "cambialo": "cambiar",
        "cambiala": "cambiar",
        "actualiza": "actualizar",
        "modifica": "modificar",
        "mueve": "mover",
        "borra": "borrar",
        "elimina": "eliminar",
        "eliminalo": "eliminar",
        "elimnalo": "eliminar",
        "botar": "eliminar",
        "bota": "eliminar",
        "quitar": "eliminar",
        "quita": "eliminar",
        "agrega": "agregar",
        "agregalo": "agregar",
        "agregala": "agregar",
        "anota": "agregar",
        "apunta": "agregar",
        "comprame": "comprar",
        "compre": "comprar",
        "compree": "comprar",
        "traje": "comprar",
        "trajee": "comprar",
        "repone": "reponer",
        "reponerlo": "reponer",
        "gaste": "gastar",
        "gastee": "gastar",
        "use": "usar",
        "usee": "usar",
        "bebi": "beber",
        "consumi": "consumir",
        "consumi": "consumir",
        "comi": "comer",
        "acabe": "acabar",
        "acabo": "acabar",
        "seacabo": "acabar",
        "recomiendame": "recomendar",
        "recomiendame": "recomendar",
        "sugiere": "recomendar",
        "sugiereme": "recomendar",
        "cocino": "cocinar",
        "cocinas": "cocinar",
        "jato": "casa",
        "refri": "refri",
        "recordatorio": "recordatorio",
    }

    INTENT_PHRASE_ALIASES = (
        (r"\bque\s+hay\s+en\s+mi\s+refri\b", "que hay en la refri"),
        (r"\bque\s+tengo\s+en\s+mi\s+refri\b", "que hay en la refri"),
        (r"\bque\s+falta\s+en\s+la\s+casa\b", "que falta comprar"),
        (r"\bque\s+me\s+falta\s+comprar\b", "que falta comprar"),
        (r"\bque\s+puedo\s+hacer\s+de\s+comer\b", "que puedo cocinar"),
        (r"\bque\s+puedo\s+preparar\b", "que puedo cocinar"),
        (r"\bque\s+recetas\s+me\s+recomiendas\b", "que recetas me recomiendas"),
        (r"\brecomiendame\s+algo\s+de\s+comer\b", "recomiendame una receta"),
        (r"\brecomendar\s+algo\s+de\s+comer\b", "recomendar una receta"),
        (r"\bcambiar\s+la\s+ubica(?:cion|ción)\b", "cambiar la ubicacion"),
        (r"\bcamviar\s+la\s+ubica(?:cion|ción)\b", "cambiar la ubicacion"),
        (r"\bdel\s+frigo\b", "de la refri"),
        (r"\bde\s+la\s+jato\b", "de la casa"),
        (r"\bque\s+tan\s+voy\s+hoy\b", "resumen diario"),
        (r"\bcuanto\s+gastar\b", "cuanto he gastado"),
        (r"\bcuanto\s+gasto\b", "cuanto he gastado"),
    )

    TONE_KEY = "__chat_tone__"
    LOCALE_KEY = "__chat_locale__"
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
        text = re.sub(r"[^\w\s.,:/-]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _contains_any(text: str, options: tuple) -> bool:
        normalized_text = ChatService._normalize(text)
        for option in options:
            normalized_option = ChatService._normalize(str(option))
            if not normalized_option:
                continue
            escaped_option = re.escape(normalized_option).replace("\\ ", r"\s+")
            pattern = rf"(^|\b){escaped_option}(\b|$)"
            if re.search(pattern, normalized_text):
                return True
        return False

    @staticmethod
    def _canonicalize_intent_text(text_n: str) -> str:
        tokens = text_n.split()
        canonical_tokens = [ChatService.INTENT_TOKEN_ALIASES.get(token, token) for token in tokens]
        canonical_text = " ".join(canonical_tokens)
        for pattern, replacement in ChatService.INTENT_PHRASE_ALIASES:
            canonical_text = re.sub(pattern, replacement, canonical_text)
        return re.sub(r"\s+", " ", canonical_text).strip()

    @staticmethod
    def _choose(options: tuple[str, ...] | list[str]) -> str:
        return random.choice(list(options))

    @staticmethod
    def _get_tone(db: Session) -> str:
        tone_item = MemoryService.get_by_key(db, ChatService.TONE_KEY)
        if not tone_item:
            return "casual"
        value = ChatService._normalize(tone_item.value)
        return "formal" if value == "formal" else "casual"

    @staticmethod
    def _set_tone(db: Session, tone: str) -> None:
        if tone not in ("casual", "formal"):
            return
        MemoryService.save_item(db, MemoryCreate(key=ChatService.TONE_KEY, value=tone))

    @staticmethod
    def _get_locale(db: Session) -> str:
        locale_item = MemoryService.get_by_key(db, ChatService.LOCALE_KEY)
        if not locale_item:
            return "pe"
        value = ChatService._normalize(locale_item.value)
        return "pe" if value in ("pe", "peru", "peruano") else value

    @staticmethod
    def _set_locale(db: Session, locale: str) -> None:
        if not locale:
            return
        MemoryService.save_item(db, MemoryCreate(key=ChatService.LOCALE_KEY, value=locale))

    @staticmethod
    def _detect_locale_preference(text_n: str) -> str | None:
        peru_patterns = (
            "habla peruano",
            "hablame peruano",
            "háblame peruano",
            "modo peru",
            "modo peruano",
            "espanol peruano",
            "español peruano",
        )
        if ChatService._contains_any(text_n, peru_patterns):
            return "pe"
        return None

    @staticmethod
    def _maybe_update_locale(db: Session, text_n: str) -> str | None:
        detected = ChatService._detect_locale_preference(text_n)
        if not detected:
            return None
        ChatService._set_locale(db, detected)
        return "Ya está, desde ahora te hablo en español peruano, más natural y a tu estilo."

    @staticmethod
    def _detect_tone_preference(text_n: str) -> str | None:
        formal_patterns = (
            "habla formal",
            "hablame formal",
            "háblame formal",
            "responde formal",
            "modo formal",
            "tratame de usted",
            "trátame de usted",
        )
        casual_patterns = (
            "habla casual",
            "hablame casual",
            "háblame casual",
            "responde casual",
            "modo casual",
            "hablame normal",
            "háblame normal",
        )
        if ChatService._contains_any(text_n, formal_patterns):
            return "formal"
        if ChatService._contains_any(text_n, casual_patterns):
            return "casual"
        return None

    @staticmethod
    def _maybe_update_tone(db: Session, text_n: str) -> str | None:
        detected = ChatService._detect_tone_preference(text_n)
        if not detected:
            return None
        ChatService._set_tone(db, detected)
        if detected == "formal":
            return "Perfecto. Desde ahora te responderé en un tono más formal."
        return "Perfecto. Desde ahora te responderé en un tono más cercano y casual."

    @staticmethod
    def _tone_pick(
        db: Session,
        casual: tuple[str, ...],
        formal: tuple[str, ...],
        peru_casual: tuple[str, ...] | None = None,
    ) -> str:
        tone = ChatService._get_tone(db)
        if tone == "formal":
            return ChatService._choose(formal)

        locale = ChatService._get_locale(db)
        if locale == "pe" and peru_casual:
            return ChatService._choose(peru_casual)
        return ChatService._choose(casual)

    @staticmethod
    def _extract_qty(text: str, default: float = 1.0) -> float:
        qty_with_unit = ChatService._extract_qty_and_unit(text)
        if qty_with_unit:
            return qty_with_unit[0]

        for word, value in (("medio", 0.5), ("media", 0.5), ("cuarto", 0.25)):
            if re.search(rf"\b{word}\b", text):
                return value

        match = re.search(r"\b(\d+\s*/\s*\d+)\b", text)
        if match:
            frac = ChatService._parse_fraction(match.group(1))
            if frac is not None:
                return frac

        match = re.search(r"(\d+(?:[.,]\d+)?)", text)
        if not match:
            return default
        return float(match.group(1).replace(",", "."))

    @staticmethod
    def _parse_fraction(raw: str) -> float | None:
        token = raw.replace(" ", "")
        if "/" not in token:
            return None
        try:
            numerator, denominator = token.split("/", 1)
            num = float(numerator)
            den = float(denominator)
            if den == 0:
                return None
            return num / den
        except Exception:
            return None

    @staticmethod
    def _normalize_unit_label(raw_unit: str | None) -> str | None:
        if not raw_unit:
            return None
        value = ChatService._normalize(raw_unit)
        if value in ("kg", "kgs", "kilo", "kilos"):
            return "kilo"
        if value in ("g", "gramo", "gramos"):
            return "gramo"
        if value in ("lt", "l", "litro", "litros"):
            return "litro"
        if value in ("unidad", "unidades", "u"):
            return "unidad"
        if value in ("docena", "docenas", "doc"):
            return "docena"
        if value in ("tarro", "tarros"):
            return "tarro"
        return value

    @staticmethod
    def _extract_qty_and_unit(text: str) -> tuple[float, str | None] | None:
        normalized = ChatService._normalize(text)
        mixed = re.search(
            r"\b(?P<int>\d+)\s+(?P<frac>\d+\s*/\s*\d+)\s*(?P<unit>kilos?|kg|gramos?|g|tarros?|unidades?|litros?|l|docenas?|doc)?\b",
            normalized,
        )
        if mixed:
            frac = ChatService._parse_fraction(mixed.group("frac"))
            if frac is not None:
                whole = float(mixed.group("int"))
                unit = ChatService._normalize_unit_label(mixed.group("unit"))
                return whole + frac, unit

        fraction = re.search(
            r"\b(?P<frac>\d+\s*/\s*\d+)\s*(?P<unit>kilos?|kg|gramos?|g|tarros?|unidades?|litros?|l|docenas?|doc)?\b",
            normalized,
        )
        if fraction:
            frac = ChatService._parse_fraction(fraction.group("frac"))
            if frac is not None:
                unit = ChatService._normalize_unit_label(fraction.group("unit"))
                return frac, unit

        word_fraction = re.search(
            r"\b(?P<word>medio|media|cuarto)\s*(?P<unit>kilos?|kg|gramos?|g|tarros?|unidades?|litros?|l|docenas?|doc)?\b",
            normalized,
        )
        if word_fraction:
            value = {"medio": 0.5, "media": 0.5, "cuarto": 0.25}[word_fraction.group("word")]
            unit = ChatService._normalize_unit_label(word_fraction.group("unit"))
            return value, unit

        numeric = re.search(
            r"\b(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>kilos?|kg|gramos?|g|tarros?|unidades?|litros?|l|docenas?|doc)\b",
            normalized,
        )
        if numeric:
            qty = float(numeric.group("qty").replace(",", "."))
            unit = ChatService._normalize_unit_label(numeric.group("unit"))
            return qty, unit

        return None

    @staticmethod
    def _infer_default_unit(product_name: str) -> str:
        normalized_name = ChatService._normalize(product_name)
        for keyword, unit in ChatService.DEFAULT_UNIT_BY_KEYWORD.items():
            if keyword in normalized_name:
                return unit
        return "unidad"

    @staticmethod
    def _convert_qty_between_units(qty: float, source_unit: str | None, target_unit: str | None) -> float | None:
        if qty < 0:
            return None

        src = ChatService._normalize_unit_label(source_unit) if source_unit else None
        tgt = ChatService._normalize_unit_label(target_unit) if target_unit else None

        if not tgt:
            return qty
        if not src or src == tgt:
            return qty

        conversions = {
            ("gramo", "kilo"): 1 / 1000,
            ("kilo", "gramo"): 1000,
            ("docena", "unidad"): 12,
            ("unidad", "docena"): 1 / 12,
        }

        factor = conversions.get((src, tgt))
        if factor is None:
            return None
        return qty * factor

    @staticmethod
    def _extract_price_and_reference_qty(text_n: str) -> tuple[float | None, float | None]:
        price = ChatService._extract_unit_price(text_n)
        if price is None:
            return None, None

        # Example: "me costo 3.50 el 1/2 kilo" means total 3.50 for 0.5 kilo.
        reference_match = re.search(
            r"\b(?:me\s+cost[óo]|cost[óo]|costo)\s*(?:s\/|s\.)?\s*\d+(?:[.,]\d+)?\s*(?:el|por)\s+(?P<qty>\d+\s*/\s*\d+|\d+(?:[.,]\d+)?|medio|media|cuarto)\s*(?P<unit>kilos?|kg|gramos?|g|tarros?|unidades?|litros?|l|docenas?|doc)?\b",
            text_n,
        )
        if not reference_match:
            return price, None

        qty_token = reference_match.group("qty")
        qty_value: float | None = None
        if qty_token in ("medio", "media"):
            qty_value = 0.5
        elif qty_token == "cuarto":
            qty_value = 0.25
        elif "/" in qty_token:
            qty_value = ChatService._parse_fraction(qty_token)
        else:
            qty_value = float(qty_token.replace(",", "."))

        return price, qty_value

    @staticmethod
    def _extract_unit_price(text: str) -> float | None:
        patterns = [
            r"\ba\s*(?:s\/|s\.)?\s*(\d+(?:[.,]\d+)?)\b",
            r"\b(?:por|costo|cost[óo]|me\s+costo|me\s+cost[óo])\s*(?:s\/|s\.)?\s*(\d+(?:[.,]\d+)?)\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return float(match.group(1).replace(",", "."))
        return None

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
    def _try_social_reply(db: Session, text_n: str) -> str | None:
        if ChatService._contains_any(text_n, ChatService.GREETING_HINTS):
            return ChatService._tone_pick(
                db,
                (
                    "¡Hola! Qué gusto tenerte por aquí. ¿Qué necesitas hoy en casa?",
                    "¡Hola! Aquí estoy para ayudarte con tu casa. Cuéntame qué quieres hacer.",
                    "¡Hey! Estoy lista para ayudarte con inventario, compras, alertas o recuerdos.",
                ),
                (
                    "Hola. Es un gusto saludarte. ¿En qué te ayudo hoy con el hogar?",
                    "Hola, con gusto te ayudo. Indícame qué deseas revisar.",
                    "Buenos días. Estoy disponible para asistirte con inventario y recordatorios.",
                ),
                (
                    "¡Hola, causa! ¿Qué hacemos hoy en la casa?",
                    "¡Hola! Todo bien por acá. Dime nomás y lo vemos al toque.",
                    "¡Qué tal! Te ayudo con inventario, compras, alertas y todo lo de la jato.",
                ),
            )

        if ChatService._contains_any(text_n, ChatService.MOOD_HINTS):
            return ChatService._tone_pick(
                db,
                (
                    "¡Todo bien por aquí! Lista para ayudarte con lo de la casa.",
                    "Muy bien, gracias. ¿Seguimos con inventario, compras o alertas?",
                    "Con energía total. Dime y lo resolvemos juntos.",
                ),
                (
                    "Todo bien, gracias. Estoy lista para ayudarte.",
                    "Muy bien, gracias por preguntar. ¿Qué deseas gestionar ahora?",
                    "Me encuentro bien. Indícame y avanzamos.",
                ),
            )

        if ChatService._contains_any(text_n, ChatService.WHO_HINTS):
            return (
                "Soy tu asistente virtual del hogar. "
                "Te ayudo con inventario, compras, consumos, alertas, recetas y recordatorios."
            )

        if ChatService._contains_any(text_n, ChatService.THANKS_HINTS):
            return ChatService._tone_pick(
                db,
                (
                    "¡De nada! Me encanta ayudarte.",
                    "¡Siempre! Cuando quieras seguimos.",
                    "Con gusto. Avísame y hacemos lo siguiente.",
                ),
                (
                    "Con gusto. Estoy para ayudarte.",
                    "Ha sido un placer ayudarte.",
                    "De nada. Si deseas, continuamos con lo siguiente.",
                ),
                (
                    "¡De nada! Para eso estamos.",
                    "Todo bien, cuando quieras seguimos.",
                    "Dale, cualquier cosa me avisas y lo vemos.",
                ),
            )

        if ChatService._contains_any(text_n, ChatService.GOODBYE_HINTS):
            return ChatService._tone_pick(
                db,
                (
                    "Perfecto, quedo pendiente. ¡Hasta luego!",
                    "Listo, aquí estaré cuando me necesites. ¡Chao!",
                    "Hecho, hablamos luego. ¡Que te vaya súper!",
                ),
                (
                    "Perfecto, quedo atenta. Hasta luego.",
                    "De acuerdo. Estaré disponible cuando lo necesites.",
                    "Conforme. Nos vemos más tarde.",
                ),
                (
                    "Ya está, cualquier cosa me escribes. ¡Nos vemos!",
                    "Listo, te dejo tranqui. ¡Hablamos!",
                    "Dale, quedo atenta por si sale algo más.",
                ),
            )

        if ChatService._contains_any(text_n, ChatService.HELP_HINTS):
            return ChatService._tone_pick(
                db,
                (
                    "Puedo ayudarte de forma natural. Por ejemplo: "
                    "'compré 2 leches a 5.50', 'gasté 1 yogurt', 'agrega 3 huevos', "
                    "'qué tengo en casa', 'resumen diario', 'recetas según inventario' o "
                    "'recuerda que mi bebida favorita es café'.",
                ),
                (
                    "Puedo ayudarte con órdenes en lenguaje natural. Por ejemplo: "
                    "'compré 2 leches a 5.50', 'consumí 1 yogurt', 'agrega 3 huevos', "
                    "'inventario actual', 'resumen diario' o "
                    "'recuerda que mi bebida favorita es café'.",
                ),
                (
                    "Te ayudo al toque. Puedes decirme: "
                    "'compré 2 leches a 5.50', 'gasté 1 yogurt', 'agrega 3 huevos', "
                    "'resumen diario', 'lista de compras' o "
                    "'recuerda que mi bebida favorita es café'.",
                ),
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
        unit_price: float | None = None
        input_unit: str | None = None

        if match:
            qty = ChatService._extract_qty(match.group("qty"), default=1.0)
            name = match.group("name").strip()
            if not consume:
                unit_price = ChatService._extract_unit_price(text_n)
        else:
            actions = ChatService.ACTION_CONSUME if consume else ChatService.ACTION_BUY
            implicit_purchase = False
            if not ChatService._contains_any(text_n, actions):
                # Accept phrases like "la fresa me costó 3.50 el 1/2 kilo" as purchase.
                if not consume and re.search(r"\bme\s+cost[óo]\b", text_n):
                    implicit_purchase = True
                else:
                    return None

            if not consume:
                price_value, reference_qty = ChatService._extract_price_and_reference_qty(text_n)
                if price_value is not None and reference_qty and reference_qty > 0:
                    unit_price = price_value / reference_qty
                else:
                    unit_price = price_value

            qty_with_unit = ChatService._extract_qty_and_unit(text_n)
            if qty_with_unit:
                qty = qty_with_unit[0]
                input_unit = qty_with_unit[1]
            else:
                qty_source = text_n
                if not consume and unit_price is not None:
                    qty_source = re.sub(
                        r"\b(?:me\s+cost[óo]|cost[óo]|costo|a|por)\s*(?:s\/|s\.)?\s*\d+(?:[.,]\d+)?\b",
                        " ",
                        qty_source,
                    )
                qty = ChatService._extract_qty(qty_source, default=1.0)

            action_pattern = "|".join(actions)
            reduced = text_n
            if not implicit_purchase:
                reduced = re.sub(rf"\b(?:{action_pattern})\b", " ", reduced)

            reduced = re.sub(r"\b(?:me\s+cost[óo]|cost[óo]|costo|a|por)\s*(?:s\/|s\.)?\s*\d+(?:[.,]\d+)?\b", " ", reduced)
            reduced = re.sub(r"\b\d+\s*/\s*\d+\b", " ", reduced)
            reduced = re.sub(r"\b\d+(?:[.,]\d+)?\b", " ", reduced)
            reduced = re.sub(r"\b(?:medio|media|cuarto|kilo|kilos|kg|gramo|gramos|g|tarro|tarros|unidad|unidades|docena|docenas|litro|litros|el|la|los|las)\b", " ", reduced)
            name = ChatService._clean_candidate_name(reduced)
            if not name:
                if consume:
                    return "Entendí que usaste algo, pero no capté qué. Dímelo así: 'gasté 1 leche'."
                return "Entendí que compraste algo, pero no capté qué. Dímelo así: 'compré 2 leches'."

        if qty <= 0:
            qty = 0.25 if not consume else 1.0

        product = ChatService._find_product_flexible(db, name)
        if not product:
            inferred_unit = ChatService._infer_default_unit(name)
            qty_for_default_unit = ChatService._convert_qty_between_units(qty, input_unit, inferred_unit)
            if qty_for_default_unit is None:
                return (
                    f"No pude convertir {ChatService._fmt_num(qty)} {input_unit or ''} a la unidad de {name}. "
                    "Prueba con otra unidad o dime la compra en su unidad habitual."
                )

            qty = qty_for_default_unit
            if consume:
                return (
                    f"No encontré '{name}' en el inventario. "
                    f"¿Quieres que lo registre? Dímelo con: 'agrega {name} {ChatService._fmt_num(qty)} {inferred_unit}'."
                )

            product = ProductService.create_product(
                db,
                ProductCreate(
                    name=name.title(),
                    category=ChatService.DEFAULT_CATEGORY,
                    stock_current=0,
                    unit=inferred_unit,
                    stock_minimum=1,
                    location=ChatService.DEFAULT_LOCATION,
                ),
            )
            # Quantity is already converted to the newly created product unit.
            input_unit = product.unit

        qty_for_product_unit = ChatService._convert_qty_between_units(qty, input_unit, product.unit)
        if qty_for_product_unit is None:
            return (
                f"No pude convertir {ChatService._fmt_num(qty)} {input_unit or ''} a {product.unit} para {product.name}. "
                "Dímelo en la misma unidad del producto o en una equivalente."
            )
        qty = qty_for_product_unit

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
            PurchaseCreate(product_name=product.name, quantity=qty, unit_price=unit_price),
        )
        total = ChatService._fmt_num(product.stock_current)
        if unit_price is not None:
            amount = ChatService._fmt_num(unit_price * qty)
            return (
                f"Compré {qty_str} {unit} de {product.name} a {ChatService._fmt_num(unit_price)} c/u. "
                f"Gasto registrado: {amount}. Ahora tienes {total} {unit} en casa."
            )
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
                    unit=ChatService._normalize_unit_label(data["unit"]) or ChatService._infer_default_unit(data["name"]),
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

        qty_with_unit = ChatService._extract_qty_and_unit(text_n)
        qty = qty_with_unit[0] if qty_with_unit else ChatService._extract_qty(text_n, default=0.0)
        input_unit = qty_with_unit[1] if qty_with_unit else None
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
            converted_add_qty = ChatService._convert_qty_between_units(add_qty, input_unit, existing.unit)
            if converted_add_qty is None:
                return (
                    f"No pude convertir {ChatService._fmt_num(add_qty)} {input_unit or ''} a {existing.unit} para {existing.name}."
                )
            add_qty = converted_add_qty
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
        
        inferred_unit = ChatService._infer_default_unit(name)
        qty_for_new_unit = ChatService._convert_qty_between_units(qty, input_unit, inferred_unit)
        if qty_for_new_unit is None:
            qty_for_new_unit = qty

        _PENDING["action"] = "create_product"
        _PENDING["name"] = name
        _PENDING["qty"] = qty_for_new_unit
        _PENDING["unit"] = inferred_unit

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

        qty_hint = f" con {ChatService._fmt_num(qty_for_new_unit)} {inferred_unit} de entrada" if qty_for_new_unit > 0 else ""
        return (
            f"No tengo {name} en el inventario. "
            f"¿Quieres que lo cree{qty_hint}? Dime sí o no."
        )

    @staticmethod
    def _try_price_queries(db: Session, text_n: str) -> str | None:
        latest_price_patterns = [
            r"(?:ultimo|último)\s+precio\s+(?:registrado\s+)?(?:de|del|para)\s+(?P<name>.+)",
            r"(?:a\s+como|a\s+cuanto|a\s+cuánto)\s+(?:esta|está)\s+(?P<name>.+)",
            r"precio\s+de\s+(?P<name>.+)",
        ]

        for pattern in latest_price_patterns:
            match = re.search(pattern, text_n)
            if not match:
                continue

            name = ChatService._clean_candidate_name(match.group("name")).title()
            if not name:
                return None

            product = ChatService._find_product_flexible(db, name)
            if not product:
                return f"No encontré '{name}' en el inventario para revisar su precio."

            latest = PurchaseService.get_latest_purchase_for_product(db, product)
            if not latest or latest.unit_price is None:
                return f"Aún no tengo precio registrado para {product.name}."

            if product.unit == "kilo":
                half_kilo = ChatService._fmt_num(latest.unit_price * 0.5)
                return (
                    f"El último precio registrado de {product.name} es {ChatService._fmt_num(latest.unit_price)} por kilo "
                    f"(equivale a {half_kilo} por 1/2 kilo)."
                )

            return (
                f"El último precio registrado de {product.name} es {ChatService._fmt_num(latest.unit_price)} por {product.unit}."
            )

        if re.search(r"(?:producto\s+)?(?:que\s+)?cuesta\s+mas|más\s+caro|mas\s+caro", text_n):
            result = PurchaseService.get_most_expensive_product_by_latest_price(db)
            if not result:
                return "Aún no tengo precios registrados para decirte qué producto cuesta más."
            product, unit_price = result
            return (
                f"Por último precio registrado, el producto que cuesta más es {product.name}: "
                f"{ChatService._fmt_num(unit_price)} por {product.unit}."
            )

        return None

    @staticmethod
    def _build_inventory_reply(db: Session) -> str:
        AlertService.refresh_product_statuses(db)
        products = ProductService.list_products(db)
        if not products:
            return "Tu inventario está vacío por ahora. Si quieres, te ayudo a cargarlo en un minuto."

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
        intro = ChatService._tone_pick(
            db,
            (
                "Esto es lo que tienes en casa:",
                "Te paso tu inventario actual:",
                "Así va tu inventario ahora mismo:",
            ),
            (
                "Este es su inventario actual:",
                "Le comparto su inventario actual:",
                "Así se encuentra su inventario en este momento:",
            ),
            (
                "Esto tienes en la casa ahorita:",
                "Te paso cómo va tu inventario, causa:",
                "Así está tu stock por ahora:",
            ),
        )
        return intro + "\n" + "\n".join(lines)

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
            return "Tu lista de compras está vacía por ahora. Vas muy bien con el stock."

        lines = [
            f"- {item.product_name}: compra al menos {ChatService._fmt_num(item.needed_quantity)} {item.unit}"
            for item in alerts.shopping_list
        ]
        return "Te armé esta lista de compras:\n" + "\n".join(lines)

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
            return "Todavía no tengo compras registradas para calcular gastos del hogar."
        if summary.items_with_price == 0:
            return (
                f"Tienes {summary.purchases_count} compra(s) registradas en los últimos {summary.period_days} días, "
                "pero ninguna con precio. Si registras unit_price podré calcular el gasto total."
            )
        return (
            f"En los últimos {summary.period_days} días llevas {ChatService._fmt_num(summary.total_amount)} en gastos del hogar. "
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
            f"Aquí va tu resumen de hoy: vencen {expired_count} producto(s), hay {expiring_count} por vencer, "
            f"faltan {missing} y para mañana te sugiero comprar {tomorrow_buy}. "
            f"Recordatorio clave: {reminder_hint}."
        )

    @staticmethod
    def reply(db: Session, message: str) -> str:
        text = message.strip()
        text_n = ChatService._normalize(text)
        text_i = ChatService._canonicalize_intent_text(text_n)

        # 0. Locale preference
        locale_reply = ChatService._maybe_update_locale(db, text_i)
        if locale_reply:
            return locale_reply

        # 1. Conversational tone preference
        tone_reply = ChatService._maybe_update_tone(db, text_i)
        if tone_reply:
            return tone_reply

        # 2. Pending confirmation takes priority
        pending_reply = ChatService._try_pending_confirmation(db, text_i)
        if pending_reply:
            return pending_reply

        # 3. Social / conversational
        social_reply = ChatService._try_social_reply(db, text_i)
        if social_reply:
            return social_reply

        # 3. Memory save
        memory_reply = ChatService._try_memory_natural(db, text, text_i)
        if memory_reply:
            return memory_reply

        # 4. Household reminder save
        household_save_reply = ChatService._try_save_household_reminder(db, text, text_i)
        if household_save_reply:
            return household_save_reply

        # 5. Memory delete
        delete_memory_reply = ChatService._try_delete_memory(db, text_i)
        if delete_memory_reply:
            return delete_memory_reply

        # 6. Update location
        location_reply = ChatService._try_update_location(db, text_i)
        if location_reply:
            return location_reply

        # 7. Delete from inventory
        delete_product_reply = ChatService._try_delete_product(db, text_i)
        if delete_product_reply:
            return delete_product_reply

        # 8. Add to / create in inventory
        add_reply = ChatService._try_add_or_create(db, text, text_i)
        if add_reply:
            return add_reply

        # 9. Daily summary
        if ChatService._contains_any(text_i, ChatService.DAILY_SUMMARY_HINTS):
            return ChatService._build_daily_summary_reply(db)

        # 10. Recipes
        if ChatService._contains_any(text_i, ChatService.RECIPE_HINTS):
            return ChatService._build_recipes_reply(db)

        # 11. Household reminders list
        if ChatService._contains_any(text_i, ChatService.HOUSEHOLD_REMINDER_HINTS):
            return ChatService._build_household_reminders_reply(db)

        # 12. Shopping list
        if ChatService._contains_any(text_i, ChatService.SHOPPING_LIST_HINTS):
            return ChatService._build_shopping_list_reply(db)

        # 13. Daily alerts
        if ChatService._contains_any(text_i, ChatService.DAILY_ALERT_HINTS):
            return ChatService._build_daily_alerts_reply(db)

        # 14. Expenses
        if ChatService._contains_any(text_i, ChatService.EXPENSE_HINTS):
            return ChatService._build_expense_reply(db)

        # 15. Important memories
        if ChatService._contains_any(text_i, ChatService.IMPORTANT_MEMORY_HINTS):
            return ChatService._build_important_memories_reply(db)

        # 16. Consume first
        if ChatService._contains_any(text_i, ChatService.CONSUME_FIRST_HINTS):
            return ChatService._build_consume_first_reply(db)

        # 16.1 Price queries
        price_query_reply = ChatService._try_price_queries(db, text_i)
        if price_query_reply:
            return price_query_reply

        # 17. Buy
        buy_reply = ChatService._try_buy_or_consume(db, text, text_i, consume=False)
        if buy_reply:
            return buy_reply

        # 18. Consume
        consume_reply = ChatService._try_buy_or_consume(db, text, text_i, consume=True)
        if consume_reply:
            return consume_reply

        # 19. Inventory list
        if ChatService._contains_any(text_i, ChatService.INVENTORY_HINTS):
            return ChatService._build_inventory_reply(db)

        # 20. Products expiring / expired
        if ChatService._contains_any(text_i, ChatService.EXPIRING_HINTS):
            return ChatService._build_expiring_reply(db)

        # 17. Alerts
        if ChatService._contains_any(text_i, ChatService.ALERT_HINTS):
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
        if ChatService._contains_any(text_i, ChatService.MEMORY_HINTS):
            items = MemoryService.list_items(db)
            if not items:
                return "Aún no tengo recuerdos guardados sobre tus preferencias."
            return "Esto recuerdo de ti:\n" + "\n".join(f"- {item.key}: {item.value}" for item in items)

        return (
            ChatService._tone_pick(
                db,
                (
                    "No te seguí del todo, pero lo resolvemos rápido. Prueba algo como: "
                    "'compré 2 leches a 5.50', 'gasté 1 yogurt', 'agrega 3 huevos', "
                    "'resumen diario', 'recetas según inventario', 'lista de compras' o "
                    "'recuerda que mi bebida favorita es café'.",
                ),
                (
                    "No logré interpretar esa solicitud con claridad. Puedes probar con algo como: "
                    "'compré 2 leches a 5.50', 'consumí 1 yogurt', 'agrega 3 huevos', "
                    "'resumen diario', 'recetas según inventario' o "
                    "'recuerda que mi bebida favorita es café'.",
                ),
                (
                    "No te entendí bien esta vez, pero lo sacamos en una. Prueba con algo como: "
                    "'compré 2 leches a 5.50', 'gasté 1 yogurt', 'agrega 3 huevos', "
                    "'resumen diario', 'recetas según inventario' o "
                    "'recuerda que mi bebida favorita es café'.",
                ),
            )
        )

import os
import re
import random
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher, get_close_matches
from urllib.parse import quote

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.schemas import ConsumptionCreate, MemoryCreate, ProductCreate, ProductUpdate, PurchaseCreate
from app.services.alert_service import AlertService
from app.services.consumption_service import ConsumptionService
from app.services.dashboard_service import DashboardService
from app.services.inventory_service import InventoryService
from app.services.memory_service import MemoryService
from app.services.product_service import ProductService
from app.services.purchase_service import PurchaseService
from app.services.whatsapp_service import WhatsAppService

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
    STOCK_FULL_HINTS = (
        "stock completo",
        "stock total",
        "todo el stock",
        "inventario completo",
        "inventario actual",
        "lista completa",
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
    SHARE_LIST_HINTS = (
        "envia lista",
        "enviar lista",
        "manda lista",
        "mandame lista",
        "mandame la lista",
        "enviame la lista",
        "envíame la lista",
        "comparte la lista",
        "pasame la lista",
        "pásame la lista",
    )
    SHARE_REPORT_HINTS = (
        "envia dashboard",
        "enviar dashboard",
        "envia reporte",
        "enviar reporte",
        "envia gastos",
        "enviar gastos",
        "manda reporte",
        "mandame reporte",
        "envia dashboard",
        "manda dashboard",
        "mandame dashboard",
        "comparte dashboard",
        "compartir dashboard",
        "enviame reporte",
        "enviame dashboard",
    )
    MONTHLY_PDF_HINTS = (
        "gastos del mes en pdf",
        "pasame gastos del mes en pdf",
        "pásame gastos del mes en pdf",
        "reporte mensual en pdf",
        "resumen mensual en pdf",
        "dashboard mensual en pdf",
        "pdf de gastos del mes",
    )

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
    WHATSAPP_TO_KEY = "__whatsapp_to__"
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
    def _extract_phone_number(text_n: str) -> str | None:
        match = re.search(r"(?:\+?51\s*)?(9\d{8})", text_n)
        if not match:
            return None
        return "51" + match.group(1)

    @staticmethod
    def _get_default_whatsapp_to(db: Session) -> str | None:
        saved = MemoryService.get_by_key(db, ChatService.WHATSAPP_TO_KEY)
        if saved and saved.value.strip():
            return saved.value.strip()

        env_phone = (os.getenv("DEFAULT_WHATSAPP_TO") or "").strip()
        if env_phone:
            return env_phone
        return None

    @staticmethod
    def _try_set_whatsapp_number(db: Session, text_n: str) -> str | None:
        if not re.search(r"(?:mi\s+)?(?:numero|n[uú]mero)\s+.*whatsapp|whatsapp\s+es", text_n):
            return None

        phone = ChatService._extract_phone_number(text_n)
        if not phone:
            return "No encontré un número válido. Dímelo así: 'mi número de WhatsApp es 926342398'."

        MemoryService.save_item(db, MemoryCreate(key=ChatService.WHATSAPP_TO_KEY, value=phone))
        return f"Listo. Guardé tu WhatsApp de destino: +{phone}."

    @staticmethod
    def _dashboard_pdf_url(period_days: int = 30) -> str:
        path = f"/dashboard/pdf?days={period_days}"
        base_url = (os.getenv("APP_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip()
        if not base_url:
            return path
        base_url = base_url.rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            base_url = "https://" + base_url
        return f"{base_url}{path}"

    @staticmethod
    def _is_monthly_pdf_request(text_n: str) -> bool:
        if ChatService._contains_any(text_n, ChatService.MONTHLY_PDF_HINTS):
            return True
        if "pdf" not in text_n:
            return False
        month_hint = ChatService._contains_any(text_n, ("mes", "mensual", "30 dias", "30 días"))
        expense_hint = ChatService._contains_any(
            text_n,
            ("gasto", "gastos", "reporte", "dashboard", "resumen del mes", "resumen mensual"),
        )
        return month_hint and expense_hint

    @staticmethod
    def _build_monthly_pdf_reply(db: Session) -> str:
        summary = PurchaseService.summarize_expenses(db, days=30)
        pdf_url = ChatService._dashboard_pdf_url(period_days=30)
        return (
            "Listo, te preparé el PDF mensual de gastos con dashboard visual (cards + gráficos).\n"
            f"- Total del mes: S/ {ChatService._fmt_num(summary.total_amount)}\n"
            f"- Compras con precio: {summary.items_with_price}/{summary.purchases_count}\n"
            f"Abre aquí tu PDF: {pdf_url}"
        )

    @staticmethod
    def _build_monthly_pdf_share_text(db: Session) -> str:
        summary = PurchaseService.summarize_expenses(db, days=30)
        pdf_url = ChatService._dashboard_pdf_url(period_days=30)
        lines: list[str] = ["Reporte mensual de gastos del hogar - PDF", ""]
        lines.append("Gastos (ultimos 30 dias):")
        lines.append(f"- Total: S/ {ChatService._fmt_num(summary.total_amount)}")
        lines.append(f"- Compras con precio: {summary.items_with_price}/{summary.purchases_count}")
        lines.append("")
        lines.append(f"Abre el PDF aquí: {pdf_url}")
        return "\n".join(lines)

    @staticmethod
    def _try_send_monthly_pdf_whatsapp(db: Session, text_n: str) -> str | None:
        if not ("pdf" in text_n or "reporte" in text_n or "gastos del mes" in text_n):
            return None
        if not ChatService._contains_any(text_n, ("whatsapp", "wsp", "wtspp", "automatico", "automático")):
            return None

        target_phone = ChatService._extract_phone_number(text_n) or ChatService._get_default_whatsapp_to(db)
        if not target_phone:
            return (
                "Para envío automático por WhatsApp necesito tu número destino. "
                "Dímelo así: 'mi número de WhatsApp es 926342398'."
            )

        share_text = ChatService._build_monthly_pdf_share_text(db)
        pdf_url = ChatService._dashboard_pdf_url(period_days=30)
        if not pdf_url.startswith(("http://", "https://")):
            return (
                "Para enviarte el PDF como documento por WhatsApp necesito una URL pública. "
                "Configura APP_BASE_URL en Render (por ejemplo, https://tu-app.onrender.com)."
            )

        sent, detail = WhatsAppService.send_message(
            "Te envío tu reporte mensual en PDF adjunto.",
            target_phone,
            media_url=pdf_url,
        )
        if sent:
            return f"Listo, te envié el documento PDF mensual por WhatsApp a +{target_phone}."

        encoded = quote(share_text)
        fallback = (
            "No pude enviarlo automáticamente todavía. "
            f"Detalle: {detail}\n"
            "Mientras tanto, aquí tienes el link manual:\n"
            f"https://wa.me/{target_phone}?text={encoded}"
        )
        return fallback

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

        number_words = {
            "cero": 0,
            "un": 1,
            "uno": 1,
            "una": 1,
            "dos": 2,
            "tres": 3,
            "cuatro": 4,
            "cinco": 5,
            "seis": 6,
            "siete": 7,
            "ocho": 8,
            "nueve": 9,
            "diez": 10,
        }
        normalized = ChatService._normalize(text)
        for token, value in number_words.items():
            if re.search(rf"\b{token}\b", normalized):
                return float(value)

        for word, value in (("tres cuartos", 0.75), ("tres cuarto", 0.75), ("medio", 0.5), ("media", 0.5), ("cuarto", 0.25)):
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
        word_number_map = {
            "un": 1,
            "uno": 1,
            "una": 1,
            "dos": 2,
            "tres": 3,
            "cuatro": 4,
            "cinco": 5,
            "seis": 6,
            "siete": 7,
            "ocho": 8,
            "nueve": 9,
            "diez": 10,
        }

        word_numeric = re.search(
            r"\b(?P<qty>un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s*(?P<unit>kilos?|kg|gramos?|g|tarros?|unidades?|litros?|l|docenas?|doc)\b",
            normalized,
        )
        if word_numeric:
            qty = float(word_number_map[word_numeric.group("qty")])
            unit = ChatService._normalize_unit_label(word_numeric.group("unit"))
            return qty, unit
        mixed_worded = re.search(
            r"\b(?P<int>\d+)\s*(?P<unit>kilos?|kg|gramos?|g|tarros?|unidades?|litros?|l|docenas?|doc)\s+y\s+(?P<word>medio|media|cuarto)\b",
            normalized,
        )
        if mixed_worded:
            fraction = {"medio": 0.5, "media": 0.5, "cuarto": 0.25}[mixed_worded.group("word")]
            whole = float(mixed_worded.group("int"))
            unit = ChatService._normalize_unit_label(mixed_worded.group("unit"))
            return whole + fraction, unit

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
            r"\b(?P<word>tres\s+cuartos?|medio|media|cuarto)\s*(?P<unit>kilos?|kg|gramos?|g|tarros?|unidades?|litros?|l|docenas?|doc)?\b",
            normalized,
        )
        if word_fraction:
            raw_word = word_fraction.group("word")
            if re.match(r"tres\s+cuartos?", raw_word):
                value = 0.75
            else:
                value = {"medio": 0.5, "media": 0.5, "cuarto": 0.25}[raw_word]
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
    def _is_total_price_phrase(text_n: str, qty: float, raw_price: float | None = None) -> bool:
        if qty <= 0:
            return False
        unit_price_hints = (
            "c/u",
            "cada",
            "precio unitario",
            "por kilo",
            "por kg",
            "por unidad",
            "por docena",
            "por litro",
        )
        if ChatService._contains_any(text_n, unit_price_hints):
            return False

        # Explicit total-price keywords
        total_hints = (
            "en total",
            "por todos",
            "por todo",
            "en conjunto",
            "el total",
            "me costaron",
            "me costó todo",
        )
        if ChatService._contains_any(text_n, total_hints):
            return True

        # For whole-quantity purchases, default to lot total when user says
        # "compré N ... a X soles" without explicit per-unit wording.
        if qty > 1 and re.search(
            r"\b(?:a|por|costo|cost[óo]|me\s+costo|me\s+cost[óo])\s+(?:s\/|s\.)?\s*(?:\d+(?:[.,]\d+)?|un\s+sol|\d+\s+sol(?:es)?(?:\s+con\s+\d{1,2}(?:\s+centimos?)?)?|\d+\s+sol(?:es)?\s+\d{1,2}(?:\s+centimos?)?|(?:dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|veinte)\s+sol(?:es)?)\b",
            text_n,
        ):
            return True

        # Fractional quantities are almost always totals
        fractional_hint = (
            qty < 1
            or ChatService._contains_any(text_n, ("medio", "media", "cuarto", "tres cuartos", "1/2", "1/4", "3/4"))
        )
        if fractional_hint and re.search(
            r"\b(?:a|por|costo|cost[óo]|me\s+costo|me\s+cost[óo])\s+(?:s\/|s\.)?\s*(?:\d+(?:[.,]\d+)?|un\s+sol|\d+\s+sol(?:es)?(?:\s+con\s+\d{1,2}(?:\s+centimos?)?)?|\d+\s+sol(?:es)?\s+\d{1,2}(?:\s+centimos?)?|(?:dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|veinte)\s+sol(?:es)?)\b",
            text_n,
        ):
            return True

        # When buying multiple whole units: treat price as total if the implied
        # per-unit cost would be implausibly low.
        if qty > 1 and raw_price is not None and raw_price > 0:
            implied_unit = raw_price / qty
            if implied_unit < 0.10:
                return True

        return False

    @staticmethod
    def _extract_add_qty_and_unit(text_n: str, raw_candidate: str) -> tuple[float, str | None]:
        qty_with_unit = ChatService._extract_qty_and_unit(text_n)
        if qty_with_unit:
            return qty_with_unit

        candidate_n = ChatService._normalize(raw_candidate)
        # Product names like "7 semillas" should not be treated as quantity 7.
        if re.match(r"^\d+\b", candidate_n):
            return 0.0, None
        return 0.0, None

    @staticmethod
    def _infer_default_unit(product_name: str) -> str:
        normalized_name = ChatService._normalize(product_name)
        for keyword, unit in ChatService.DEFAULT_UNIT_BY_KEYWORD.items():
            if keyword in normalized_name:
                return unit
        return "unidad"

    @staticmethod
    def _convert_product_stock_units(product, target_unit: str) -> tuple[float, float] | None:
        converted_current = ChatService._convert_qty_between_units(product.stock_current, product.unit, target_unit)
        converted_minimum = ChatService._convert_qty_between_units(product.stock_minimum, product.unit, target_unit)
        if converted_current is None or converted_minimum is None:
            return None
        return converted_current, converted_minimum

    @staticmethod
    def _try_update_product_unit(db: Session, text_n: str) -> str | None:
        patterns = [
            r"(?:cambia|cambiar|actualiza|actualizar|modifica|modificar)\s+(?:la\s+)?(?:unidad|medida)\s+de\s+(?P<name>.+?)\s+(?:a|por)\s+(?P<unit>kilos?|kg|gramos?|g|tarros?|unidades?|litros?|l|docenas?|doc)",
            r"(?:pon|deja|usar|usa)\s+(?P<name>.+?)\s+(?:en|con)\s+(?P<unit>kilos?|kg|gramos?|g|tarros?|unidades?|litros?|l|docenas?|doc)",
        ]

        match = None
        for pattern in patterns:
            match = re.search(pattern, text_n)
            if match:
                break
        if not match:
            return None

        name = ChatService._clean_candidate_name(match.group("name")).title()
        target_unit = ChatService._normalize_unit_label(match.group("unit")) 
        if not name or not target_unit:
            return "Entendí que quieres cambiar la unidad, pero me faltó el producto o la unidad destino."

        product = ChatService._find_product_flexible(db, name)
        if not product:
            return f"No encontré '{name}' en el inventario para cambiar su unidad."

        previous_unit = product.unit
        if previous_unit == target_unit:
            return f"{product.name} ya está en {target_unit}."

        converted = ChatService._convert_product_stock_units(product, target_unit)
        note = ""
        if converted is not None:
            current_converted, minimum_converted = converted
            update_payload = ProductUpdate(
                unit=target_unit,
                stock_current=current_converted,
                stock_minimum=minimum_converted,
            )
            note = " Convertí también las cantidades de stock a la nueva unidad."
        else:
            update_payload = ProductUpdate(unit=target_unit)
            note = " No convertí cantidades porque no hay equivalencia matemática directa entre esas unidades."

        ProductService.update_product(db, product, update_payload)
        return f"Listo. Cambié la unidad de {product.name} de {previous_unit} a {target_unit}.{note}"

    @staticmethod
    def _try_normalize_all_units(db: Session, text_n: str) -> str | None:
        normalize_hints = (
            "normaliza unidades",
            "normalizar unidades",
            "corrige unidades",
            "corregir unidades",
            "estandariza unidades",
            "estandarizar unidades",
            "actualiza unidades de todos",
            "actualizar unidades de todos",
            "cambiar unidades de todos",
            "cambia unidades de todos",
        )
        if not ChatService._contains_any(text_n, normalize_hints):
            return None

        products = ProductService.list_products(db)
        if not products:
            return "No hay productos en el inventario para normalizar unidades."

        changed: list[str] = []
        unchanged = 0

        for product in products:
            target_unit = ChatService._infer_default_unit(product.name)
            if target_unit == product.unit:
                unchanged += 1
                continue

            converted = ChatService._convert_product_stock_units(product, target_unit)
            if converted is not None:
                current_converted, minimum_converted = converted
                ProductService.update_product(
                    db,
                    product,
                    ProductUpdate(unit=target_unit, stock_current=current_converted, stock_minimum=minimum_converted),
                )
                changed.append(f"- {product.name}: {product.unit} -> {target_unit} (conversión aplicada)")
            else:
                ProductService.update_product(db, product, ProductUpdate(unit=target_unit))
                changed.append(f"- {product.name}: {product.unit} -> {target_unit} (sin conversión numérica)")

        if not changed:
            return "Todo ya estaba con unidades correctas según las reglas actuales."

        changed_count = len(changed)
        header = f"Listo. Normalicé unidades en {changed_count} {ChatService._pluralize(changed_count, 'producto')}."
        if unchanged:
            header += f" {unchanged} ya estaban correctos."
        return header + "\n" + "\n".join(changed)

    # -----------------------------------------------------------------------
    # Weight-unit helpers
    # -----------------------------------------------------------------------

    _WEIGHT_UNITS = {"kilo", "gramo"}

    @staticmethod
    def _is_weight_unit(unit: str | None) -> bool:
        """Return True when *unit* is a weight measure (kilo, gramo)."""
        if unit is None:
            return False
        return ChatService._normalize_unit_label(unit) in ChatService._WEIGHT_UNITS

    @staticmethod
    def _extract_unit_count_from_purchase(text_n: str) -> int | None:
        """
        Detect phrases like  'vienen 10', 'son 10 unidades', 'traen 5',
        'hay 12 naranjas', etc. that tell us how many pieces came in a
        weight purchase.  Returns the integer count or None.
        """
        patterns = [
            r"\b(?:vienen?|traen?|son|hay|tiene[n]?|trae)\s+(?P<n>\d+)\b",
            r"\b(?P<n>\d+)\s+(?:unidades?|piezas?|frut[ao]s?)\b",
        ]
        for pat in patterns:
            m = re.search(pat, text_n)
            if m:
                val = int(m.group("n"))
                if val > 0:
                    return val
        return None

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
            r"\b(?:me\s+cost[óo]|cost[óo]|costo)\s+(?:(?:s\/|s\.)?\s*\d+(?:[.,]\d+)?|un\s+sol|\d+\s+sol(?:es)?(?:\s+con\s+\d{1,2}\s+centimos?)?|\d+\s+sol(?:es)?\s+\d{1,2}(?:\s+centimos?)?)\s*(?:el|por)\s+(?P<qty>\d+\s*/\s*\d+|\d+(?:[.,]\d+)?|medio|media|cuarto)\s*(?P<unit>kilos?|kg|gramos?|g|tarros?|unidades?|litros?|l|docenas?|doc)?\b",
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
        text_n = ChatService._normalize(text)

        # Word-based sol amount for voice input: "tres soles con cincuenta"
        _word_sol_map = {
            "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
            "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
            "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "veinte": 20,
        }
        _word_cent_map = {
            "diez": 10, "quince": 15, "veinte": 20, "treinta": 30, "cuarenta": 40,
            "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90,
        }
        word_price = re.search(
            r"\b(?:a|por|costo|cost[óo]|me\s+costo|me\s+cost[óo])\s+"
            r"(?P<soles_w>dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|veinte)\s+sol(?:es)?"
            r"(?:\s+con\s+(?P<cents_w>cincuenta|veinte|treinta|cuarenta|sesenta|setenta|ochenta|noventa|diez|quince)(?:\s+centimos?)?)?\b",
            text_n,
        )
        if word_price:
            soles = _word_sol_map.get(word_price.group("soles_w"), 0)
            cents_word = word_price.group("cents_w")
            cents = _word_cent_map.get(cents_word, 0) if cents_word else 0
            return soles + cents / 100

        patterns = [
            # "3 soles 50" (no connector)
            r"\b(?:a|por|costo|costo|me\s+costo)\s+(?P<soles>\d+|un)\s+sol(?:es)?\s+(?P<cents2>\d{1,2})(?:\s+centimos?)?\b",
            # "3 soles con 50" or "3 soles con 50 centimos" (centimos now optional)
            r"\b(?:a|por|costo|costo|me\s+costo)\s+(?P<soles>\d+|un)\s+sol(?:es)?(?:\s+con\s+(?P<cents1>\d{1,2})(?:\s+centimos?)?)?\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text_n)
            if match:
                soles_raw = match.group("soles")
                cents_raw = match.groupdict().get("cents1") or match.groupdict().get("cents2")
                soles = 1 if soles_raw == "un" else int(soles_raw)
                cents = int(cents_raw) if cents_raw else 0
                return soles + (cents / 100)

        decimal_patterns = [
            r"\ba\s*(?:s\/|s\.)?\s*(\d+(?:[.,]\d+)?)\b",
            r"\b(?:por|costo|cost[óo]|me\s+costo|me\s+cost[óo])\s*(?:s\/|s\.)?\s*(\d+(?:[.,]\d+)?)\b",
        ]
        for pattern in decimal_patterns:
            match = re.search(pattern, text_n)
            if match:
                return float(match.group(1).replace(",", "."))
        return None

    @staticmethod
    def _fmt_num(value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")

    @staticmethod
    def _pluralize(count: int, singular: str, plural: str | None = None) -> str:
        if count == 1:
            return singular
        return plural if plural is not None else f"{singular}s"

    @staticmethod
    def _fmt_money(value: float) -> str:
        amount = round(float(value), 2)
        if amount < 0:
            return f"menos {ChatService._fmt_money(abs(amount))}"

        if amount < 1:
            # For small amounts use 10-cent rounding and preserve leading zero.
            rounded_subsol = round(amount * 10) / 10
            cents_rounded = int(round(rounded_subsol * 100))
            return f"{rounded_subsol:.2f} céntimos"

        cents_total = int(round(amount * 100))

        soles = cents_total // 100
        cents = cents_total % 100

        if cents == 0:
            return "un sol" if soles == 1 else f"{soles} soles"
        if soles == 1:
            return f"un sol con {cents} céntimos"
        return f"{soles} soles con {cents} céntimos"

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
            key = ChatService._normalize(product.name)
            # Short product names (≤2 chars, e.g. "te") are only allowed when
            # the search target is also short (≤3 chars). This prevents "te"
            # matching inside "detergente" while still finding it for "té".
            if len(key) <= 2 and len(normalized_target) > 3:
                continue
            by_normalized[key] = product

        if normalized_target in by_normalized:
            return by_normalized[normalized_target]

        partial = [item for key, item in by_normalized.items() if normalized_target and normalized_target in key]
        if partial:
            # Prefer the closest match over the first arbitrary one.
            partial.sort(key=lambda p: SequenceMatcher(None, normalized_target, ChatService._normalize(p.name)).ratio(), reverse=True)
            return partial[0]

        close = get_close_matches(normalized_target, list(by_normalized.keys()), n=1, cutoff=0.6)
        if close:
            return by_normalized[close[0]]
        return None

    @staticmethod
    def _find_product_exact(db: Session, raw_name: str):
        direct = ProductService.get_by_name(db, raw_name)
        if direct:
            return direct

        normalized_target = ChatService._normalize(raw_name)
        if not normalized_target:
            return None

        products = ProductService.list_products(db)
        for product in products:
            if ChatService._normalize(product.name) == normalized_target:
                return product
        # Fallback to flexible matching so voice artifacts like "cebolla en"
        # can still be found when the user later says just "cebolla".
        return ChatService._find_product_flexible(db, raw_name)

    @staticmethod
    def _find_product_with_unit_preference(db: Session, raw_name: str, preferred_unit: str | None):
        """Find a product by name, preferring records that match the requested unit."""
        normalized_target = ChatService._normalize(raw_name)
        pref = ChatService._normalize_unit_label(preferred_unit)
        if not normalized_target or not pref:
            return ChatService._find_product_exact(db, raw_name)

        products = ProductService.list_products(db)
        candidates = []
        for product in products:
            key = ChatService._normalize(product.name)
            if not key:
                continue
            # Avoid over-generic reverse containment like "te" in "detergente".
            if normalized_target == key or normalized_target in key:
                candidates.append(product)

        if not candidates:
            return ChatService._find_product_exact(db, raw_name)

        def _score(product) -> float:
            key = ChatService._normalize(product.name)
            exact_bonus = 100.0 if key == normalized_target else 0.0
            contains_bonus = 20.0 if normalized_target in key else 0.0
            similarity = SequenceMatcher(None, normalized_target, key).ratio() * 10.0
            return exact_bonus + contains_bonus + similarity

        matching_unit = [p for p in candidates if ChatService._normalize_unit_label(p.unit) == pref]
        if matching_unit:
            return max(matching_unit, key=_score)

        return max(candidates, key=_score)

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
                    existing = ChatService._find_product_exact(db, name)
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
                        existing_after_conflict = ChatService._find_product_exact(db, name)
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

        # Also release stale pending state when the user starts a missing-items flow.
        if ChatService._contains_any(text_n, ("falta", "faltan", "me falta", "nos falta", "tambien falta", "también falta")):
            if not ChatService._contains_any(text_n, ChatService.SHOPPING_LIST_HINTS):
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
    def _clean_missing_line(raw_line: str) -> str:
        line = raw_line.strip().strip("-•*\t ")
        line = re.sub(r"^\d+[\.)-]\s*", "", line).strip()
        line_n = ChatService._normalize(line)
        line_n = re.sub(
            r"^(?:falta|faltan|me\s+falta|nos\s+falta|tambien\s+falta|también\s+falta|agrega|agregar|anota|anotar|registra|registrar|crea|crear)\s+",
            "",
            line_n,
        )
        line_n = re.sub(r"\b(?:tambien|también)\s+falta$", "", line_n).strip()
        line_n = re.sub(r"\s+(?:tambien|también)(?:\s+falta)?$", "", line_n).strip()
        line_n = re.sub(r"^(?:el|la|los|las|un|una|unos|unas)\s+", "", line_n).strip()
        cleaned = ChatService._clean_candidate_name(line_n)
        return re.sub(r"\s+", " ", cleaned).strip()[:120]

    @staticmethod
    def _extract_missing_names_from_text(text: str, text_n: str) -> list[str]:
        names: list[str] = []

        if "\n" in text:
            lines = [line for line in text.splitlines() if line.strip()]
            for line in lines:
                candidate = ChatService._clean_missing_line(line)
                if candidate:
                    names.append(candidate)
            if len(names) >= 2:
                return names
            names.clear()

        missing_pattern = re.search(
            r"(?:^|\b)(?:falta|faltan|me\s+falta|nos\s+falta|tambien\s+falta|también\s+falta)\s+(?P<rest>.+)",
            text_n,
        )
        if missing_pattern:
            rest = missing_pattern.group("rest")
            chunks = [chunk.strip() for chunk in re.split(r",|\s+y\s+", rest) if chunk.strip()]
            for chunk in chunks:
                candidate = ChatService._clean_missing_line(chunk)
                if candidate:
                    names.append(candidate)

        return names

    @staticmethod
    def _register_missing_products(db: Session, names: list[str]) -> str | None:
        if not names:
            return None

        created: list[str] = []
        already_present: list[str] = []

        for raw_name in names:
            name = re.sub(r"\s+", " ", raw_name).strip().title()[:120]
            if not name:
                continue

            existing = ChatService._find_product_exact(db, name)
            if existing:
                already_present.append(existing.name)
                continue

            inferred_unit = ChatService._infer_default_unit(name)
            try:
                created_product = ProductService.create_product(
                    db,
                    ProductCreate(
                        name=name,
                        category=ChatService.DEFAULT_CATEGORY,
                        stock_current=0,
                        unit=inferred_unit,
                        stock_minimum=1,
                        location=ChatService.DEFAULT_LOCATION,
                    ),
                )
                created.append(created_product.name)
            except IntegrityError:
                db.rollback()
                maybe_existing = ChatService._find_product_exact(db, name)
                if maybe_existing:
                    already_present.append(maybe_existing.name)

        if not created and not already_present:
            return None

        lines: list[str] = []
        if created:
            lines.append(
                "Listo. Agregué al inventario (stock en 0, para que salga en faltantes):\n"
                + "\n".join(f"- {item}" for item in created)
            )
        if already_present:
            lines.append(
                "Estos ya existían en tu inventario:\n"
                + "\n".join(f"- {item}" for item in already_present)
            )
        return "\n\n".join(lines)

    @staticmethod
    def _try_add_missing_products(db: Session, text: str, text_n: str) -> str | None:
        if ChatService._contains_any(text_n, ChatService.SHOPPING_LIST_HINTS):
            return None

        names = ChatService._extract_missing_names_from_text(text, text_n)
        if not names:
            return None
        return ChatService._register_missing_products(db, names)

    @staticmethod
    def _try_buy_or_consume(db: Session, text: str, text_n: str, consume: bool) -> str | None:
        regex = ChatService.CONSUME_CMD if consume else ChatService.BUY_CMD
        match = regex.fullmatch(text)
        qty: float
        name: str
        unit_price: float | None = None
        reference_qty: float | None = None
        input_unit: str | None = None
        inferred_total_price = False

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
                        r"\b(?:me\s+cost[óo]|cost[óo]|costo|a|por)\s+(?:s\/|s\.)?\s*(?:\d+(?:[.,]\d+)?|un\s+sol|\d+\s+sol(?:es)?(?:\s+con\s+\d{1,2}(?:\s+centimos?)?)?|\d+\s+sol(?:es)?\s+\d{1,2}(?:\s+centimos?)?|(?:dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|veinte)\s+sol(?:es)?(?:\s+con\s+(?:cincuenta|veinte|treinta|cuarenta|sesenta|setenta|ochenta|noventa|diez|quince)(?:\s+centimos?)?)?)\b",
                        " ",
                        qty_source,
                    )
                qty = ChatService._extract_qty(qty_source, default=1.0)

            if (
                not consume
                and unit_price is not None
                and reference_qty is None
                and ChatService._is_total_price_phrase(text_n, qty, unit_price)
                and qty > 0
            ):
                unit_price = unit_price / qty
                inferred_total_price = True

            action_pattern = "|".join(actions)
            reduced = text_n
            if not implicit_purchase:
                reduced = re.sub(rf"\b(?:{action_pattern})\b", " ", reduced)

            reduced = re.sub(
                r"\b(?:me\s+cost[óo]|cost[óo]|costo|a|por)\s+(?:s\/|s\.)?\s*(?:\d+(?:[.,]\d+)?|un\s+sol|\d+\s+sol(?:es)?(?:\s+con\s+\d{1,2}(?:\s+centimos?)?)?|\d+\s+sol(?:es)?\s+\d{1,2}(?:\s+centimos?)?|(?:dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|veinte)\s+sol(?:es)?(?:\s+con\s+(?:cincuenta|veinte|treinta|cuarenta|sesenta|setenta|ochenta|noventa|diez|quince)(?:\s+centimos?)?)?)\b",
                " ",
                reduced,
            )
            # Remove trailing piece-count clauses from the product candidate.
            # Example: "camote, vienen 4" -> "camote"
            reduced = re.sub(
                r"[,;:]?\s*\b(?:vienen?|traen?|son|hay|tiene[n]?|trae)\s+\d+\b.*$",
                " ",
                reduced,
            )
            reduced = re.sub(r"\b\d+\s*/\s*\d+\b", " ", reduced)
            reduced = re.sub(r"\b\d+(?:[.,]\d+)?\b", " ", reduced)
            reduced = re.sub(r"\b(?:un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|medio|media|cuartos?|kilo|kilos|kg|gramo|gramos|g|tarro|tarros|unidad|unidades|docena|docenas|litro|litros|el|la|los|las|sol|soles|centimo|centimos|con|y|en|cincuenta|veinte|treinta|cuarenta|sesenta|setenta|ochenta|noventa)\b", " ", reduced)
            name = ChatService._clean_candidate_name(reduced)
            if not name:
                if consume:
                    return "Entendí que usaste algo, pero no capté qué. Dímelo así: 'gasté 1 leche'."
                return "Entendí que compraste algo, pero no capté qué. Dímelo así: 'compré 2 leches'."

        if qty <= 0:
            qty = 0.25 if not consume else 1.0

        lookup_unit = input_unit
        # Natural consume phrases like "gasté una cebolla" should prefer unidad.
        if consume and lookup_unit is None and qty > 0 and float(qty).is_integer():
            lookup_unit = "unidad"

        product = ChatService._find_product_with_unit_preference(db, name, lookup_unit)
        if not product:
            inferred_unit = ChatService._infer_default_unit(name)
            qty_for_default_unit = ChatService._convert_qty_between_units(qty, input_unit, inferred_unit)
            if (
                qty_for_default_unit is None
                and not consume
                and ChatService._is_weight_unit(input_unit)
                and inferred_unit == "unidad"
            ):
                piece_count = ChatService._extract_unit_count_from_purchase(text_n)
                if piece_count:
                    if unit_price is not None and unit_price > 0:
                        total_paid = unit_price * qty
                        unit_price = round(total_paid / piece_count, 4)
                    qty_for_default_unit = float(piece_count)
                    input_unit = "unidad"
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

        # ── Weight-to-unit special case ──────────────────────────────────
        # Case A: product stored as "unidad", user bought by weight → use piece count
        # Case B: product stored as "kilo", user tells how many pieces came → migrate to unidad
        weight_to_unit_note = ""
        if not consume:
            piece_count = ChatService._extract_unit_count_from_purchase(text_n)

            if ChatService._is_weight_unit(input_unit) and product.unit == "unidad":
                # Case A: product is already tracked in unidades, purchase in kilo
                if piece_count:
                    if unit_price is not None and unit_price > 0:
                        total_paid = unit_price * qty
                        unit_price = round(total_paid / piece_count, 4)
                        weight_to_unit_note = (
                            f" ({ChatService._fmt_num(qty)} {input_unit} → "
                            f"{piece_count} unidades a {ChatService._fmt_num(unit_price)} c/u)"
                        )
                    else:
                        weight_to_unit_note = (
                            f" ({ChatService._fmt_num(qty)} {input_unit} → {piece_count} unidades)"
                        )
                    qty = float(piece_count)
                    input_unit = "unidad"
                else:
                    # No piece count: store the raw weight quantity
                    input_unit = product.unit

            elif product.unit == "kilo" and piece_count and qty > 0:
                # Case B: product is tracked in kilos but user says how many pieces came.
                # Migrate product unit from kilo to unidad.
                ratio = piece_count / qty  # units per kilo
                existing_units = round(product.stock_current * ratio)
                if unit_price is not None and unit_price > 0:
                    total_paid = unit_price * qty
                    unit_price = round(total_paid / piece_count, 4)
                    weight_to_unit_note = (
                        f" ({ChatService._fmt_num(qty)} {input_unit or 'kilo'} → "
                        f"{piece_count} unidades a {ChatService._fmt_num(unit_price)} c/u). "
                        f"Cambié el producto de kilo a unidades."
                    )
                else:
                    weight_to_unit_note = (
                        f" ({ChatService._fmt_num(qty)} {input_unit or 'kilo'} → "
                        f"{piece_count} unidades). Cambié el producto de kilo a unidades."
                    )
                ProductService.update_product(
                    db, product, ProductUpdate(unit="unidad", stock_current=existing_units)
                )
                db.refresh(product)
                qty = float(piece_count)
                input_unit = "unidad"

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
        if unit_price is not None:
            ChatService._set_current_price_for_product(db, product, unit_price)
        total = ChatService._fmt_num(product.stock_current)
        if unit_price is not None:
            amount_str = ChatService._fmt_money(unit_price * qty)
            unit_price_str = ChatService._fmt_money(unit_price)
            if inferred_total_price:
                return (
                    f"Compré {qty_str} {unit} de {product.name}. "
                    f"Total lote: {amount_str}. Equivale a {unit_price_str} por {unit}. "
                    f"Ahora tienes {total} {unit} en casa."
                    + weight_to_unit_note
                )
            return (
                f"Compré {qty_str} {unit} de {product.name} a {unit_price_str} por {unit}. "
                f"Gasto registrado: {amount_str}. Ahora tienes {total} {unit} en casa."
                + weight_to_unit_note
            )
        return (
            f"Compré {qty_str} {unit} de {product.name}. "
            f"Ahora tienes {total} {unit} en casa."
            + weight_to_unit_note
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

        raw_candidate = match.group("name")
        qty, input_unit = ChatService._extract_add_qty_and_unit(text_n, raw_candidate)
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

        # For add/create, require exact match to avoid wrong product substitutions.
        existing = ChatService._find_product_exact(db, name)

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
        ]
        current_price_patterns = [
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
                half_kilo = ChatService._fmt_money(latest.unit_price * 0.5)
                return (
                    f"El último precio registrado de {product.name} es {ChatService._fmt_money(latest.unit_price)} por kilo "
                    f"(equivale a {half_kilo} por 1/2 kilo)."
                )

            return (
                f"El último precio registrado de {product.name} es {ChatService._fmt_money(latest.unit_price)} por {product.unit}."
            )

        for pattern in current_price_patterns:
            match = re.search(pattern, text_n)
            if not match:
                continue

            name = ChatService._clean_candidate_name(match.group("name")).title()
            if not name:
                return None

            product = ChatService._find_product_flexible(db, name)
            if not product:
                return f"No encontré '{name}' en el inventario para revisar su precio."

            current_price = ChatService._get_current_price_for_product(db, product)
            if current_price is not None:
                if product.unit == "kilo":
                    half_kilo = ChatService._fmt_money(current_price * 0.5)
                    return (
                        f"El precio actual de {product.name} es {ChatService._fmt_money(current_price)} por kilo "
                        f"(equivale a {half_kilo} por 1/2 kilo)."
                    )
                return (
                    f"El precio actual de {product.name} es {ChatService._fmt_money(current_price)} por {product.unit}."
                )

            latest = PurchaseService.get_latest_purchase_for_product(db, product)
            if not latest or latest.unit_price is None:
                return f"Aún no tengo precio registrado para {product.name}."

            # Backward-compatible fallback: use latest historical price as current.
            ChatService._set_current_price_for_product(db, product, latest.unit_price)
            if product.unit == "kilo":
                half_kilo = ChatService._fmt_money(latest.unit_price * 0.5)
                return (
                    f"El precio actual de {product.name} es {ChatService._fmt_money(latest.unit_price)} por kilo "
                    f"(equivale a {half_kilo} por 1/2 kilo)."
                )
            return (
                f"El precio actual de {product.name} es {ChatService._fmt_money(latest.unit_price)} por {product.unit}."
            )

        if re.search(r"(?:producto\s+)?(?:que\s+)?cuesta\s+mas|más\s+caro|mas\s+caro", text_n):
            result = PurchaseService.get_most_expensive_product_by_latest_price(db)
            if not result:
                return "Aún no tengo precios registrados para decirte qué producto cuesta más."
            product, unit_price = result
            return (
                f"Por último precio registrado, el producto que cuesta más es {product.name}: "
                f"{ChatService._fmt_money(unit_price)} por {product.unit}."
            )

        return None

    @staticmethod
    def _try_set_price_without_purchase(db: Session, text_n: str) -> str | None:
        patterns = [
            r"(?:actualiza|actualizar|pon|poner|cambia|cambiar|registra|registrar)\s+(?:el\s+)?precio\s+(?:de|del|para)\s+(?P<name>.+?)\s+(?:a|por|en)\s+(?P<price>.+)",
            r"precio\s+(?:de|del)\s+(?P<name>.+?)\s+(?:es|seria|sería)\s+(?P<price>.+)",
            r"precio\s+(?:de|del)\s+(?P<name>.+?)\s+(?:a|por|en)\s+(?P<price>.+)",
            # Reference price without buy intent, e.g. "1 detergente opal a 8 soles"
            r"^(?P<qty>\d+(?:[.,]\d+)?|un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+(?P<name>.+?)\s+(?:a|por)\s+(?P<price>.+)$",
        ]

        parsed_name: str | None = None
        parsed_price: float | None = None

        for pattern in patterns:
            match = re.search(pattern, text_n)
            if not match:
                continue

            # For free-form "qty + name + precio" pattern, avoid hijacking
            # explicit buy/consume messages.
            if match.groupdict().get("qty") is not None:
                if ChatService._contains_any(text_n, ChatService.ACTION_BUY) or ChatService._contains_any(text_n, ChatService.ACTION_CONSUME):
                    return None
                if re.search(r"\bme\s+cost[óo]\b", text_n):
                    return None

            name_raw = match.group("name")
            price_raw = match.group("price")
            # In voice inputs we may get a leading spoken quantity/article in the name.
            # Example: "un detergente opal a 8 soles" -> name "detergente opal".
            name_raw = re.sub(
                r"^\s*(?:\d+(?:[.,]\d+)?|un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+",
                "",
                name_raw,
            )
            name = ChatService._clean_candidate_name(name_raw).title()
            if not name:
                return "Entendí que quieres actualizar un precio, pero no capté el producto."

            # Reuse existing price parser by prefixing with "a ".
            # Accepts formats like "5.5", "4 soles 50", "un sol".
            parsed = ChatService._extract_unit_price(f"a {price_raw}")
            if parsed is None or parsed < 0:
                return "No pude leer el precio. Prueba así: 'actualiza precio de arroz a 4.20'."

            parsed_name = name
            parsed_price = parsed
            break

        if parsed_name is None or parsed_price is None:
            return None

        product = ChatService._find_product_flexible(db, parsed_name)
        if not product:
            return f"No encontré '{parsed_name}' en el inventario para actualizar su precio."

        previous_price = ChatService._get_current_price_for_product(db, product)
        if previous_price is None:
            latest_before = PurchaseService.get_latest_purchase_for_product(db, product)
            previous_price = latest_before.unit_price if latest_before else None

        _, created_reference = PurchaseService.set_unit_price_without_stock(db, product, parsed_price)
        ChatService._set_current_price_for_product(db, product, parsed_price)

        new_price_str = ChatService._fmt_money(parsed_price)
        if previous_price is None:
            if created_reference:
                return (
                    f"Listo. Registré el precio de {product.name} en {new_price_str} por {product.unit} "
                    "sin mover el stock."
                )
            return f"Listo. Guardé el primer precio de {product.name}: {new_price_str} por {product.unit}."

        old_price_str = ChatService._fmt_money(previous_price)
        return (
            f"Listo. Actualicé el precio de {product.name}: antes {old_price_str}, ahora {new_price_str} "
            f"por {product.unit} (stock sin cambios)."
        )

    @staticmethod
    def _try_product_stock_query(db: Session, text_n: str) -> str | None:
        # Let explicit full-inventory requests continue to the inventory list handler.
        if ChatService._contains_any(text_n, ChatService.STOCK_FULL_HINTS):
            return None

        patterns = [
            r"\bstock\s+(?:de|del)\s+(?P<name>.+)",
            r"\bcantidad\s+(?:de|del)\s+(?P<name>.+)",
            r"\bcuant[oa]s?\s+(?:queda|quedan|tengo|hay|tiene|tienen)\s+(?:de|del)?\s*(?P<name>.+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text_n)
            if not match:
                continue

            name = ChatService._clean_candidate_name(match.group("name")).title()
            if not name:
                return None

            product = ChatService._find_product_flexible(db, name)
            if not product:
                return f"No encontré '{name}' en el inventario para revisar su stock."

            current = ChatService._fmt_num(product.stock_current)
            minimum = ChatService._fmt_num(product.stock_minimum)
            location = product.location or "sin ubicación"
            return (
                f"Stock de {product.name}: {current} {product.unit}. "
                f"Mínimo: {minimum} {product.unit}. Ubicación: {location}."
            )

        return None

    @staticmethod
    def _try_products_without_location_query(db: Session, text_n: str) -> str | None:
        triggers = [
            r"\bproductos?\s+sin\s+ubicaci(?:on|ón)\b",
            r"\bsin\s+ubicaci(?:on|ón)\b",
            r"\bque\s+productos?\s+(?:no\s+)?tienen\s+ubicaci(?:on|ón)\b",
            r"\bproductos?\s+sin\s+lugar\b",
        ]
        if not any(re.search(pattern, text_n) for pattern in triggers):
            return None

        products = ProductService.list_products(db)
        if not products:
            return "Tu inventario está vacío por ahora."

        missing = [
            product for product in products
            if not (product.location or "").strip()
            or ChatService._normalize(product.location or "") in ("sin ubicacion", "sin ubicacion.")
        ]

        if not missing:
            return "Buenísimo: no tienes productos sin ubicación."

        lines = [f"- {product.name}: {ChatService._fmt_num(product.stock_current)} {product.unit}" for product in missing]
        return "Estos productos están sin ubicación:\n" + "\n".join(lines)

    @staticmethod
    def _try_products_without_stock_query(db: Session, text_n: str) -> str | None:
        triggers = [
            r"\bproductos?\s+sin\s+stock\b",
            r"\bsin\s+stock\b",
            r"\bproductos?\s+agotados?\b",
            r"\bproductos?\s+(?:en\s+)?cero\b",
            r"\bstock\s+(?:en\s+)?cero\b",
            r"\bque\s+productos?\s+(?:no\s+tengo|me\s+faltan?)\b",
            r"\bque\s+me\s+faltan?\b",
        ]
        if not any(re.search(pattern, text_n) for pattern in triggers):
            return None

        products = ProductService.list_products(db)
        if not products:
            return "Tu inventario está vacío por ahora."

        empty = [p for p in products if (p.stock_current or 0) <= 0]

        if not empty:
            return "¡Genial! Todos tus productos tienen stock disponible."

        lines = [
            f"- {p.name}: 0 {p.unit} ({p.location or 'sin ubicación'})"
            for p in empty
        ]
        empty_count = len(empty)
        if empty_count == 1:
            header = "Este producto está sin stock (cantidad = 0):"
        else:
            header = f"Estos {empty_count} productos están sin stock (cantidad = 0):"
        return header + "\n" + "\n".join(lines)

    @staticmethod
    def _current_price_key(product_id: int) -> str:
        return f"__current_price__::{product_id}"

    @staticmethod
    def _get_current_price_for_product(db: Session, product) -> float | None:
        item = MemoryService.get_by_key(db, ChatService._current_price_key(product.id))
        if not item:
            return None
        try:
            value = float(item.value)
            if value < 0:
                return None
            return value
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _set_current_price_for_product(db: Session, product, unit_price: float) -> None:
        MemoryService.save_item(
            db,
            MemoryCreate(key=ChatService._current_price_key(product.id), value=str(float(unit_price))),
        )

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
    def _build_shopping_list_share_text(db: Session) -> str | None:
        alerts = AlertService.build_alerts(db)
        lines: list[str] = ["Lista de compras del hogar", "", "Faltantes:"]

        if not alerts.shopping_list:
            lines.append("- Sin faltantes por ahora")
        else:
            for item in alerts.shopping_list:
                lines.append(f"- {item.product_name}: {ChatService._fmt_num(item.needed_quantity)} {item.unit}")

        return "\n".join(lines)

    @staticmethod
    def _build_monthly_report_share_text(db: Session) -> str:
        summary = PurchaseService.summarize_expenses(db, days=30)
        lines: list[str] = ["Reporte mensual del hogar", ""]
        lines.append("Gastos (ultimos 30 dias):")
        lines.append(f"- Total: S/ {ChatService._fmt_num(summary.total_amount)}")
        lines.append(f"- Compras con precio: {summary.items_with_price}/{summary.purchases_count}")
        lines.append("")
        lines.append(f"PDF del dashboard mensual: {ChatService._dashboard_pdf_url(period_days=30)}")

        return "\n".join(lines)

    @staticmethod
    def _try_share_shopping_list(db: Session, text_n: str) -> str | None:
        wants_list = ChatService._contains_any(text_n, ChatService.SHARE_LIST_HINTS)
        wants_report = ChatService._contains_any(text_n, ChatService.SHARE_REPORT_HINTS)
        if not wants_list and not wants_report:
            return None

        share_text = (
            ChatService._build_shopping_list_share_text(db)
            if wants_list
            else ChatService._build_monthly_report_share_text(db)
        )
        encoded = quote(share_text)
        wants_whatsapp = "whatsapp" in text_n or "wsp" in text_n or "wtspp" in text_n
        wants_email = "correo" in text_n or "mail" in text_n or "email" in text_n
        wants_telegram = "telegram" in text_n
        wants_auto = "automatic" in text_n

        target_phone = ChatService._extract_phone_number(text_n) or ChatService._get_default_whatsapp_to(db)

        if wants_auto and (wants_whatsapp or not wants_email and not wants_telegram):
            if not target_phone:
                return (
                    "Para envío automático por WhatsApp necesito tu número destino. "
                    "Dímelo así: 'mi número de WhatsApp es 926342398'."
                )

            media_url = None
            message_body = share_text
            if wants_report:
                candidate_pdf = ChatService._dashboard_pdf_url(period_days=30)
                if candidate_pdf.startswith(("http://", "https://")):
                    media_url = candidate_pdf
                    message_body = "Te envío tu reporte mensual en PDF adjunto."

            sent, detail = WhatsAppService.send_message(message_body, target_phone, media_url=media_url)
            if sent:
                if media_url:
                    return f"Listo, te envié el PDF mensual por WhatsApp a +{target_phone}."
                return f"Listo, te lo envié automáticamente por WhatsApp a +{target_phone}."

            fallback = (
                "No pude enviarlo automáticamente todavía. "
                f"Detalle: {detail}\n"
                "Mientras tanto, aquí tienes el link manual:\n"
                f"https://wa.me/{target_phone}?text={encoded}"
            )
            return fallback

        if wants_email:
            auto_note = "\nEnvio automático real por correo requiere configurar un proveedor SMTP/API." if wants_auto else ""
            return (
                "Listo, aquí tienes para enviarlo por correo:\n"
                f"mailto:?subject=Dashboard%20hogar&body={encoded}"
                f"{auto_note}"
            )

        if wants_telegram:
            auto_note = "\nEnvio automático real por Telegram requiere bot token y chat_id." if wants_auto else ""
            return (
                "Listo, aquí tienes para compartir por Telegram:\n"
                f"https://t.me/share/url?url=&text={encoded}"
                f"{auto_note}"
            )

        if wants_whatsapp or True:
            auto_note = "\nEnvio automático real por WhatsApp requiere API (Meta/Twilio)." if wants_auto else ""
            target_suffix = target_phone if target_phone else ""
            wa_base = f"https://wa.me/{target_suffix}?text=" if target_suffix else "https://wa.me/?text="
            summary = "lista de compras" if wants_list else "reporte mensual"
            return (
                f"Listo, aquí tienes para compartir por WhatsApp ({summary}):\n"
                f"{wa_base}{encoded}"
                f"{auto_note}"
            )

    @staticmethod
    def _build_daily_alerts_reply(db: Session) -> str:
        alerts = AlertService.build_alerts(db)
        chunks: list[str] = []

        if alerts.shopping_list:
            count = len(alerts.shopping_list)
            chunks.append(f"- {count} {ChatService._pluralize(count, 'producto')} sin stock para comprar")

        if alerts.expired:
            count = len(alerts.expired)
            adjective = "vencido" if count == 1 else "vencidos"
            chunks.append(f"- {count} {ChatService._pluralize(count, 'producto')} {adjective}")
        if alerts.expiring_soon:
            count = len(alerts.expiring_soon)
            chunks.append(f"- {count} {ChatService._pluralize(count, 'producto')} por vencer")
        if alerts.low_stock:
            count = len(alerts.low_stock)
            chunks.append(f"- {count} {ChatService._pluralize(count, 'producto')} con stock bajo")
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

        expired_phrase = (
            f"vence {expired_count} {ChatService._pluralize(expired_count, 'producto')}"
            if expired_count == 1
            else f"vencen {expired_count} {ChatService._pluralize(expired_count, 'producto')}"
        )
        expiring_phrase = f"hay {expiring_count} {ChatService._pluralize(expiring_count, 'producto')} por vencer"

        return (
            f"Aquí va tu resumen de hoy: {expired_phrase}, {expiring_phrase}, "
            f"faltan {missing} y para mañana te sugiero comprar {tomorrow_buy}. "
            f"Recordatorio clave: {reminder_hint}."
        )

    @staticmethod
    def reply(db: Session, message: str) -> str:
        text = message.strip()
        text_n = ChatService._normalize(text)
        text_i = ChatService._canonicalize_intent_text(text_n)

        # 0. WhatsApp destination preference
        whatsapp_number_reply = ChatService._try_set_whatsapp_number(db, text_i)
        if whatsapp_number_reply:
            return whatsapp_number_reply

        # 1. Locale preference
        locale_reply = ChatService._maybe_update_locale(db, text_i)
        if locale_reply:
            return locale_reply

        # 2. Conversational tone preference
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

        # 6.1 Update product unit
        unit_reply = ChatService._try_update_product_unit(db, text_i)
        if unit_reply:
            return unit_reply

        # 6.2 Normalize all units
        normalize_units_reply = ChatService._try_normalize_all_units(db, text_i)
        if normalize_units_reply:
            return normalize_units_reply

        # 7. Delete from inventory
        delete_product_reply = ChatService._try_delete_product(db, text_i)
        if delete_product_reply:
            return delete_product_reply

        # 7.5 Missing-items flow (single phrase or multiline list)
        missing_products_reply = ChatService._try_add_missing_products(db, text, text_i)
        if missing_products_reply:
            return missing_products_reply

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

        # 11.05 Send monthly PDF via WhatsApp (if requested)
        send_pdf_whatsapp = ChatService._try_send_monthly_pdf_whatsapp(db, text_i)
        if send_pdf_whatsapp:
            return send_pdf_whatsapp

        # 11.06 Monthly expenses PDF (regular link)
        if ChatService._is_monthly_pdf_request(text_i):
            return ChatService._build_monthly_pdf_reply(db)

        # 11.1 Share shopping list
        share_list_reply = ChatService._try_share_shopping_list(db, text_i)
        if share_list_reply:
            return share_list_reply

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
        set_price_reply = ChatService._try_set_price_without_purchase(db, text_i)
        if set_price_reply:
            return set_price_reply

        # 16.2 Price queries
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

        # 18.5 Product stock query (single product)
        stock_query_reply = ChatService._try_product_stock_query(db, text_i)
        if stock_query_reply:
            return stock_query_reply

        # 18.6 Products without location
        without_location_reply = ChatService._try_products_without_location_query(db, text_i)
        if without_location_reply:
            return without_location_reply

        # 18.7 Products without stock (zero stock) — must run before INVENTORY_HINTS to avoid
        # "sin stock" matching the generic "stock" keyword in INVENTORY_HINTS
        without_stock_reply = ChatService._try_products_without_stock_query(db, text_i)
        if without_stock_reply:
            return without_stock_reply

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
            if alerts.shopping_list:
                chunks.append(
                    "Comprar ahora (sin stock):\n" + "\n".join(
                        f"- Comprar {item.product_name}"
                        for item in alerts.shopping_list
                    )
                )
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

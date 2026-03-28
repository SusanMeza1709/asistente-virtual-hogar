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
from app.services.lgthinq_service import LGThinQService
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
        {
            "name": "Tortilla de verduras",
            "meal": ("desayuno", "cena"),
            "healthy": True,
            "required": ("huevo",),
            "optional": ("tomate", "cebolla", "espinaca", "pimiento"),
            "steps": (
                "Bate 2 huevos con una pizca de sal.",
                "Saltea verduras picadas 3 minutos.",
                "Agrega el huevo y cocina 2-3 minutos por lado.",
            ),
        },
        {
            "name": "Avena con fruta",
            "meal": ("desayuno",),
            "healthy": True,
            "required": ("avena",),
            "optional": ("leche", "platano", "banana", "papaya", "manzana", "fresa"),
            "steps": (
                "Cocina 1/3 taza de avena con agua o leche por 5 minutos.",
                "Sirve y agrega fruta picada por encima.",
                "Endulza ligeramente solo si lo necesitas.",
            ),
        },
        {
            "name": "Yogurt con fruta y granola",
            "meal": ("desayuno", "cena"),
            "healthy": True,
            "required": ("yogurt",),
            "optional": ("papaya", "platano", "banana", "fresa", "granola"),
            "steps": (
                "Sirve una taza de yogurt natural.",
                "Agrega fruta picada y 2 cucharadas de granola.",
                "Mezcla y consume al instante.",
            ),
        },
        {
            "name": "Pan con palta y huevo",
            "meal": ("desayuno", "cena"),
            "healthy": True,
            "required": ("palta", "huevo"),
            "optional": ("pan", "tomate", "limon"),
            "steps": (
                "Hierve o frie 1 huevo.",
                "Aplasta media palta con limon y sal.",
                "Unta en pan y coloca el huevo en rodajas.",
            ),
        },
        {
            "name": "Omelette de queso y tomate",
            "meal": ("desayuno", "cena"),
            "healthy": True,
            "required": ("huevo",),
            "optional": ("queso", "tomate", "oregano"),
            "steps": (
                "Bate 2 huevos.",
                "Vierte en sarten y agrega queso y tomate.",
                "Dobla el omelette y cocina 1 minuto mas.",
            ),
        },
        {
            "name": "Arroz con pollo y verduras",
            "meal": ("almuerzo", "cena"),
            "healthy": False,
            "required": ("arroz", "pollo"),
            "optional": ("zanahoria", "vainita", "arveja", "cebolla", "ajo"),
            "steps": (
                "Dora el pollo en trozos con ajo y cebolla.",
                "Agrega verduras picadas y sofrie 3 minutos.",
                "Incorpora arroz cocido y mezcla 2 minutos.",
            ),
        },
        {
            "name": "Ensalada de pollo",
            "meal": ("almuerzo", "cena"),
            "healthy": True,
            "required": ("pollo",),
            "optional": ("lechuga", "tomate", "pepino", "palta", "limon"),
            "steps": (
                "Cocina o saltea pechuga de pollo en tiras.",
                "Mezcla hojas verdes con tomate y pepino.",
                "Agrega pollo, palta y adereza con limon.",
            ),
        },
        {
            "name": "Salteado de verduras con huevo",
            "meal": ("almuerzo", "cena"),
            "healthy": True,
            "required": ("huevo",),
            "optional": ("brocoli", "zanahoria", "pimiento", "cebolla", "zapallito"),
            "steps": (
                "Saltea verduras en tiras con poco aceite.",
                "Agrega huevo batido y mezcla hasta cuajar.",
                "Rectifica sal y sirve caliente.",
            ),
        },
        {
            "name": "Sopa de verduras",
            "meal": ("almuerzo", "cena"),
            "healthy": True,
            "required": ("zanahoria",),
            "optional": ("papa", "apio", "zapallo", "cebolla", "ajo"),
            "steps": (
                "Hierve agua con ajo y cebolla.",
                "Agrega verduras picadas y cocina 15 minutos.",
                "Ajusta sal y sirve.",
            ),
        },
        {
            "name": "Pescado a la plancha con ensalada",
            "meal": ("almuerzo", "cena"),
            "healthy": True,
            "required": ("pescado",),
            "optional": ("lechuga", "tomate", "pepino", "limon"),
            "steps": (
                "Sazona el pescado con sal, pimienta y limon.",
                "Cocina 3-4 minutos por lado a la plancha.",
                "Acompaña con ensalada fresca.",
            ),
        },
        {
            "name": "Lentejas guisadas",
            "meal": ("almuerzo", "cena"),
            "healthy": True,
            "required": ("lenteja",),
            "optional": ("cebolla", "ajo", "zanahoria", "tomate", "arroz"),
            "steps": (
                "Hierve lentejas hasta que esten suaves.",
                "Prepara aderezo con cebolla, ajo y tomate.",
                "Mezcla todo y cocina 5 minutos mas.",
            ),
        },
        {
            "name": "Quinoa con verduras",
            "meal": ("almuerzo", "cena"),
            "healthy": True,
            "required": ("quinoa",),
            "optional": ("zanahoria", "brocoli", "pimiento", "cebolla"),
            "steps": (
                "Lava y cocina quinoa por 15 minutos.",
                "Saltea verduras en cubos.",
                "Mezcla quinoa con verduras y sirve.",
            ),
        },
        {
            "name": "Wrap de pollo",
            "meal": ("almuerzo", "cena"),
            "healthy": False,
            "required": ("pollo",),
            "optional": ("tortilla", "lechuga", "tomate", "palta", "yogurt"),
            "steps": (
                "Cocina pollo deshilachado o en tiras.",
                "Rellena tortilla con pollo y vegetales.",
                "Enrolla y sirve con salsa de yogurt.",
            ),
        },
        {
            "name": "Pasta con atun y tomate",
            "meal": ("almuerzo", "cena"),
            "healthy": False,
            "required": ("pasta",),
            "optional": ("atun", "tomate", "cebolla", "ajo"),
            "steps": (
                "Cocina pasta al dente.",
                "Saltea tomate, cebolla y ajo.",
                "Mezcla con atun y pasta cocida.",
            ),
        },
        {
            "name": "Sanguche de pollo",
            "meal": ("desayuno", "cena"),
            "healthy": False,
            "required": ("pollo", "pan"),
            "optional": ("palta", "tomate", "lechuga", "queso"),
            "steps": (
                "Deshilacha pollo cocido.",
                "Arma el sandwich con vegetales.",
                "Tuesta ligeramente el pan y sirve.",
            ),
        },
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
        "comprete": "comprar te",   # voice artifact: "compré té" → "Compreté"
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
    LGTHINQ_HINTS = (
        "lg thinq",
        "thinq",
        "conectar lg",
        "conecta lg",
        "vincular lg",
        "vincula lg",
        "alerta lg",
        "alertas lg",
        "lavadora termino",
        "lavadora terminó",
        "lavadora lg",
        "secadora lg",
        "refrigeradora lg",
        "electrodomesticos lg",
        "electrodomésticos lg",
        "dispositivos lg",
        "estado lavadora lg",
        "estado secadora lg",
        "estado de mi lavadora",
        "estado de mi secadora",
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
    def _detect_lg_cycle_type(text_n: str) -> str:
        cycle_aliases = (
            ("DELICATE", ("delicado", "delicada", "ropa delicada", "delicate")),
            ("QUICK", ("rapido", "rápido", "express", "express", "corto", "quick")),
            ("ECO", ("eco", "ahorro", "economico", "económico")),
            ("HEAVY", ("pesado", "pesada", "heavy", "intenso", "intensivo")),
            ("COTTON", ("algodon", "algodón", "cotton")),
            ("MIX", ("mixto", "mezcla", "mixed")),
            ("RINSE_SPIN", ("enjuague centrifugado", "enjuague y centrifugado", "rinse spin")),
            ("SPIN_ONLY", ("solo centrifugado", "solo centrifugar", "centrifugado", "spin")),
            ("TUB_CLEAN", ("limpieza de tambor", "limpiar tambor", "tub clean", "autolimpieza")),
            ("WOOL", ("lana", "wool")),
            ("BEDDING", ("edredon", "edredón", "ropa de cama", "bedding")),
            ("BABY_CARE", ("bebe", "bebé", "cuidado de bebe", "baby care")),
            ("SPORTSWEAR", ("deporte", "ropa deportiva", "sportswear")),
        )
        for code, hints in cycle_aliases:
            if ChatService._contains_any(text_n, hints):
                return code

        explicit_cycle = re.search(
            r"(?:ciclo|programa|lavado)\s+(?:de\s+)?(?P<name>[\w\sáéíóúñ-]{3,40})",
            text_n,
        )
        if explicit_cycle:
            cycle_name = explicit_cycle.group("name")
            cycle_name = re.sub(r"\b(lg|thinq|lavadora|secadora|ahora|ya|por\s+favor)\b", " ", cycle_name)
            cycle_name = re.sub(r"\s+", " ", cycle_name).strip(" .,-")
            stop_words = {"la", "el", "mi", "tu", "en", "de", "del", "al", "un", "una", "en la", "en el"}
            normalized = ChatService._normalize(cycle_name)
            if normalized and normalized not in stop_words:
                meaningful_tokens = [token for token in normalized.split() if token not in stop_words]
                if meaningful_tokens:
                    return "_".join(meaningful_tokens).upper()

        return "NORMAL"

    @staticmethod
    def _try_lgthinq_status(db: Session, text_n: str) -> str | None:
        hints_match = ChatService._contains_any(text_n, ChatService.LGTHINQ_HINTS)
        contains_lg = bool(re.search(r"\blg\b", text_n))
        contains_thinq = "thinq" in text_n
        asks_cycle_control = ChatService._contains_any(
            text_n,
            (
                "inicia", "iniciar", "empieza", "empezar", "comienza", "comenzar",
                "arranca", "arrancar", "ejecuta", "ejecutar", "deten", "detén", "detener",
                "detenla", "detenlo", "para", "apaga", "pausa", "reanuda", "resume",
            ),
        ) and ChatService._contains_any(text_n, ("lavadora", "secadora", "ciclo", "lavado", "programa"))
        asks_appliance_status = ChatService._contains_any(
            text_n,
            (
                "estado lavadora",
                "estado secadora",
                "estado refrigeradora",
                "estado refri",
                "lavadora",
                "secadora",
            ),
        ) and ChatService._contains_any(text_n, ("estado", "status", "como va", "cómo va"))

        if not (hints_match or contains_lg or contains_thinq or asks_appliance_status or asks_cycle_control):
            return None

        asks_setup = ChatService._contains_any(
            text_n,
            ("configurar", "conectar", "setup", "credenciales", "token", "api", "activar"),
        )
        asks_connect = ChatService._contains_any(
            text_n,
            ("conectar", "conecta", "vincular", "vincula", "iniciar sesion", "iniciar sesión", "login"),
        )
        asks_list = ChatService._contains_any(
            text_n,
            ("lista", "listar", "dispositivos", "equipos", "electrodomesticos", "electrodomésticos"),
        )
        asks_enable_alert = ChatService._contains_any(
            text_n,
            ("activar alerta", "activa alerta", "avisame", "avísame", "notificame", "notifícame"),
        ) and ChatService._contains_any(text_n, ("lg", "thinq", "lavadora", "secadora"))
        asks_disable_alert = ChatService._contains_any(
            text_n,
            ("desactivar alerta", "desactiva alerta", "apaga alerta", "quita alerta"),
        ) and ChatService._contains_any(text_n, ("lg", "thinq", "lavadora", "secadora"))
        asks_check_alert = ChatService._contains_any(
            text_n,
            ("revisar alerta", "revisa alerta", "ver alerta", "ultimo evento", "último evento", "probar alerta", "probar alertas"),
        ) and ChatService._contains_any(text_n, ("lg", "thinq", "lavadora", "secadora"))
        asks_diagnostics = ChatService._contains_any(
            text_n,
            ("diagnostico lg", "diagnóstico lg", "datos raw lg", "campos lg", "debug lg"),
        )
        asks_start_cycle = ChatService._contains_any(
            text_n,
            (
                "inicia", "iniciar", "empieza", "empezar", "comenzar", "comienza",
                "arranca", "arrancar", "poner en marcha", "pon en marcha", "activa el ciclo",
                "ejecuta", "ejecutar", "corre ciclo",
            ),
        ) and ChatService._contains_any(text_n, ("lg", "thinq", "lavadora", "secadora", "ciclo", "lavado", "programa"))
        asks_stop_cycle = ChatService._contains_any(
            text_n,
            (
                "detén", "deten", "detente", "para", "detener", "apaga", "quitar",
                "cancela", "pausa", "frena", "stop",
            ),
        ) and ChatService._contains_any(text_n, ("lg", "thinq", "lavadora", "secadora", "ciclo", "lavado", "programa"))

        preferred_name = None
        if "lavadora" in text_n:
            preferred_name = "lavadora"
        elif "secadora" in text_n:
            preferred_name = "secadora"
        elif "refrigeradora" in text_n or "refrigerador" in text_n or "refri" in text_n:
            preferred_name = "refrigeradora"

        if asks_diagnostics:
            if not LGThinQService.can_query(db):
                return "LG ThinQ no está configurado."
            ok_raw, raw_devices, raw_detail = LGThinQService.list_devices_raw(db)
            if not ok_raw:
                return f"No pude obtener datos. Detalle: {raw_detail}"
            if not raw_devices:
                return "La API respondió OK pero la lista de dispositivos llegó vacía."
            lines = ["Campos raw del primer dispositivo LG ThinQ:"]
            first = raw_devices[0]
            for k, v in list(first.items())[:30]:
                lines.append(f"  {k}: {v}")
            if len(raw_devices) > 1:
                lines.append(f"(Total dispositivos: {len(raw_devices)})")
            return "\n".join(lines)

        if asks_stop_cycle:
            if not LGThinQService.can_query(db):
                return "LG ThinQ no está configurado."
            ok, msg = LGThinQService.stop_cycle(db=db)
            return msg

        if asks_start_cycle:
            if not LGThinQService.can_query(db):
                return "LG ThinQ no está configurado."
            cycle_type = ChatService._detect_lg_cycle_type(text_n)
            ok, msg = LGThinQService.start_cycle(db=db, cycle_type=cycle_type)
            return msg

        if asks_connect:
            return (
                "La integración con LG ThinQ usa un Personal Access Token (PAT).\n"
                "El token ya debería estar configurado en el servidor. "
                + LGThinQService.setup_instructions()
            )

        if not LGThinQService.can_query(db):
            return (
                "Aún no tengo activa la integración con LG ThinQ. "
                + LGThinQService.setup_instructions()
                + " Luego prueba: 'conectar LG ThinQ', 'lista mis dispositivos LG' o 'estado de mi lavadora LG'."
            )

        if asks_enable_alert:
            config = LGThinQService.save_alert_config(db, enabled=True, device_hint=preferred_name or "lavadora")
            device_label = str(config.get("device_hint") or "lavadora")
            return (
                f"Listo. Activé las alertas automáticas de LG ThinQ para tu {device_label}. "
                "Cuando detecte que terminó el ciclo, registraré el evento y si WhatsApp está operativo intentaré avisarte ahí también. "
                "Si quieres probar ahora, dime: revisar alerta LG."
            )

        if asks_disable_alert:
            config = LGThinQService.save_alert_config(db, enabled=False, device_hint=preferred_name or None)
            device_label = str(config.get("device_hint") or "lavadora")
            return f"Hecho. Dejé desactivadas las alertas automáticas de LG ThinQ para {device_label}."

        if asks_check_alert:
            config = LGThinQService.alert_config(db)
            if config.get("enabled"):
                ok_poll, event, detail = LGThinQService.poll_for_alerts(db, preferred_name=preferred_name)
                if not ok_poll:
                    return f"No pude revisar la alerta LG ThinQ ahora mismo. Detalle: {detail}."
                if event:
                    return f"Detecté este evento LG ThinQ:\n{event.get('message', 'Evento registrado.')}\n{event.get('notification', '')}".strip()
            last_event = LGThinQService.last_event(db)
            if last_event:
                return (
                    "Último evento LG ThinQ registrado:\n"
                    f"- Equipo: {last_event.get('device_name', 'Dispositivo LG')}\n"
                    f"- Tipo: {last_event.get('type', 'evento')}\n"
                    f"- Mensaje: {last_event.get('message', 'Sin detalle')}\n"
                    f"- Fecha: {last_event.get('created_at', 'sin fecha')}"
                )
            return "Todavía no tengo eventos LG ThinQ registrados. Si quieres, activa la alerta con: avísame cuando termine la lavadora LG."

        if asks_setup:
            return (
                "La integración LG ThinQ ya está en el asistente. "
                + LGThinQService.setup_instructions()
            )

        if asks_list:
            ok, devices, detail = LGThinQService.list_devices(db)
            if not ok:
                return (
                    "No pude listar tus dispositivos LG ThinQ por ahora. "
                    f"Detalle: {detail}."
                )
            if not devices:
                return "Consulté LG ThinQ, pero no encontré dispositivos vinculados en tu cuenta."

            lines = ["Estos son tus dispositivos LG ThinQ:"]
            for device in devices[:8]:
                name = str(device.get("name", "Dispositivo LG")).strip()
                dtype = str(device.get("type", "")).strip()
                if dtype:
                    lines.append(f"- {name} ({dtype})")
                else:
                    lines.append(f"- {name}")
            if len(devices) > 8:
                lines.append(f"... y {len(devices) - 8} más")
            return "\n".join(lines)

        ok, device, status, detail = LGThinQService.get_device_status(db, preferred_name=preferred_name)
        if not ok:
            return f"No pude obtener el estado de LG ThinQ en este momento. Detalle: {detail}."

        device_name = str((device or {}).get("name") or "Tu dispositivo LG")
        lines = [f"Estado de {device_name}:"]
        status_lines = LGThinQService.summarize_status(status)
        if status_lines:
            lines.extend(status_lines)
        else:
            lines.append("- No recibí campos de estado legibles desde la API.")
        return "\n".join(lines)

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
        # Display money rounded to nearest 10 centimos for natural voice/text output.
        amount = round(round(float(value) * 10) / 10, 2)
        if amount < 0:
            return f"menos {ChatService._fmt_money(abs(amount))}"

        if amount < 1:
            return f"{amount:.2f} céntimos"

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

        # Only match when the search term is a prefix of the product name or vice versa.
        # This prevents e.g. "canela" (6 chars) from matching "Te Canela Y Clavo" because
        # "canela" appears as a middle word, not at the start of that product's name.
        partial = [
            item for key, item in by_normalized.items()
            if normalized_target and (
                key.startswith(normalized_target) or normalized_target.startswith(key)
            )
        ]
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

        exact_variants = {normalized_target}
        # Accept light singular/plural variations without enabling fuzzy matches.
        if len(normalized_target) > 3 and normalized_target.endswith("s"):
            exact_variants.add(normalized_target[:-1])
        if len(normalized_target) > 4 and normalized_target.endswith("es"):
            exact_variants.add(normalized_target[:-2])

        products = ProductService.list_products(db)
        for product in products:
            key = ChatService._normalize(product.name)
            if key in exact_variants:
                return product
        return None

    @staticmethod
    def _find_product_with_unit_preference(db: Session, raw_name: str, preferred_unit: str | None):
        """Find a product by name, preferring records that match the requested unit."""
        normalized_target = ChatService._normalize(raw_name)
        pref = ChatService._normalize_unit_label(preferred_unit)
        if not normalized_target or not pref:
            return ChatService._find_product_exact(db, raw_name)

        exact_variants = {normalized_target}
        if len(normalized_target) > 3 and normalized_target.endswith("s"):
            exact_variants.add(normalized_target[:-1])
        if len(normalized_target) > 4 and normalized_target.endswith("es"):
            exact_variants.add(normalized_target[:-2])

        products = ProductService.list_products(db)
        candidates = []
        for product in products:
            key = ChatService._normalize(product.name)
            if not key:
                continue
            if key in exact_variants:
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

        action = pending.get("action")

        if action == "recipe_choose":
            if ChatService._contains_any(text_n, ChatService.DENY_HINTS):
                _clear_pending_state()
                return "Listo, no elegimos receta por ahora. Cuando quieras te propongo nuevas opciones."

            selected_index = ChatService._extract_recipe_choice_index(text_n)
            options = pending.get("options") or []

            if selected_index is None and ChatService._contains_any(text_n, ChatService.CONFIRM_HINTS):
                selected_index = 1

            if not options:
                _clear_pending_state()
                return "Se perdió la lista de recetas. Pídeme recetas otra vez y te las muestro de nuevo."

            if selected_index is None:
                if ChatService._is_recipe_request(text_n):
                    _clear_pending_state()
                    return None
                return "Elige una opción: receta 1, receta 2, receta 3 o receta 4."

            if selected_index < 1 or selected_index > len(options):
                return f"Solo tengo {len(options)} opciones activas. Elige receta 1 hasta receta {len(options)}."

            chosen = options[selected_index - 1]
            try:
                ChatService._remember_recipe_usage(db, chosen.get("name", "Receta"))
            except Exception:
                pass
            recipe_text = ChatService._build_recipe_detail_text(
                chosen.get("name", "Receta"),
                list(chosen.get("matched") or []),
                list(chosen.get("missing") or []),
                list(chosen.get("optional_missing") or []),
                list(chosen.get("missing_in_fridge") or []),
                list(chosen.get("steps") or []),
                str(chosen.get("meal", "")),
                chosen.get("beverage_hint"),
                chosen.get("prep_minutes"),
                chosen.get("cost_estimate"),
                list(chosen.get("condiments_available") or []),
                list(chosen.get("condiments_missing") or []),
            )

            _clear_pending_state()
            _PENDING.update(
                {
                    "action": "recipe_whatsapp",
                    "recipe_name": chosen.get("name", "Receta"),
                    "recipe_text": recipe_text,
                }
            )

            return (
                recipe_text
                + "\n\n¿Deseas que te envíe la receta completa por WhatsApp? "
                "Responde sí o no."
            )

        if action == "recipe_whatsapp":
            if ChatService._contains_any(text_n, ChatService.DENY_HINTS):
                _clear_pending_state()
                return "Perfecto, no la envío por WhatsApp. Si quieres otra receta, te doy más opciones."

            is_confirmed = ChatService._contains_any(text_n, ChatService.CONFIRM_HINTS)
            phone_in_text = ChatService._extract_phone_number(text_n)

            if not is_confirmed and not phone_in_text:
                return "Si deseas enviarla por WhatsApp, responde sí. Si no, responde no."

            if phone_in_text:
                MemoryService.save_item(db, MemoryCreate(key=ChatService.WHATSAPP_TO_KEY, value=phone_in_text))

            target_phone = phone_in_text or ChatService._get_default_whatsapp_to(db)
            if not target_phone:
                return (
                    "Para enviarte la receta por WhatsApp necesito tu número. "
                    "Dímelo así: mi número de WhatsApp es 926342398."
                )

            recipe_text = str(pending.get("recipe_text", "")).strip() or "Aquí va tu receta completa."
            sent, detail = WhatsAppService.send_message(recipe_text, target_phone)
            _clear_pending_state()

            if sent:
                return f"Listo, te envié la receta completa por WhatsApp a +{target_phone}."

            encoded = quote(recipe_text)
            return (
                "No pude enviarla automáticamente por WhatsApp todavía. "
                f"Detalle: {detail}\n"
                "Te dejo el link manual:\n"
                f"https://wa.me/{target_phone}?text={encoded}"
            )

        if ChatService._contains_any(text_n, ChatService.CONFIRM_HINTS):
            if action == "create_product":
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
        if action == "recipe_choose":
            return "Antes de seguir, elige una opción: receta 1, receta 2, receta 3 o receta 4."
        if action == "recipe_whatsapp":
            return "Antes de seguir, confirma si te la envío por WhatsApp. Responde sí o no."

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
            already_count = len(already_present)
            already_header = (
                "Este producto ya existía en tu inventario:\n"
                if already_count == 1
                else "Estos productos ya existían en tu inventario:\n"
            )
            lines.append(already_header + "\n".join(f"- {item}" for item in already_present))
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
        missing_count = len(missing)
        if missing_count == 1:
            header = "Este producto está sin ubicación:\n"
        else:
            header = f"Estos {missing_count} productos están sin ubicación:\n"
        return header + "\n".join(lines)

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
    def _is_recipe_request(text_n: str) -> bool:
        if ChatService._contains_any(text_n, ChatService.RECIPE_HINTS):
            return True

        patterns = (
            r"\breceta(?:s)?\b",
            r"\brecomiend(?:a|ame|ame|ar)\s+.*\bcomer\b",
            r"\b(?:que|que|qué)\s+(?:puedo|podria|podria|podr[ií]a)\s+(?:cocinar|preparar|comer)\b",
            r"\b(?:que|qué)\s+hago\s+de\s+comer\b",
            r"\b(?:desayuno|almuerzo|cena)\b.*\b(?:receta|recetas|cocinar|preparar)\b",
            r"\b(?:quiero|necesito|dame|sugiere)\s+.*\b(?:desayuno|almuerzo|cena)\b",
            r"\bsaludable(?:s)?\b.*\b(?:receta|recetas|desayuno|almuerzo|cena|cocinar|preparar)\b",
            r"\b(?:quiero|necesito|dame|sugi[eé]reme)\s+algo\s+para\s+(?:desayunar|almorzar|cenar)\b",
            r"\b(?:que|qué)\s+puedo\s+(?:desayunar|almorzar|cenar)\b",
            r"\b(?:ideas|opciones)\s+para\s+(?:desayuno|almuerzo|cena)\b",
            r"\b(?:repite|repetir|otra\s+vez|de\s+nuevo)\b.*\b(?:receta|plato|cocinar|preparar|comer)\b",
            r"\b(?:la\s+del|receta\s+del)\s+(?:lunes|martes|miercoles|miércoles|jueves|viernes|sabado|sábado|domingo)\b",
        )
        return any(re.search(pattern, text_n) for pattern in patterns)

    @staticmethod
    def _extract_recipe_choice_index(text_n: str) -> int | None:
        match = re.search(r"\b(?:receta|opcion|opción)?\s*(1|2|3|4)\b", text_n)
        if match:
            return int(match.group(1))

        if re.search(r"\b(?:la\s+)?(?:opcion|opción|numero|número)\s+(1|2|3|4)\b", text_n):
            return int(re.search(r"\b(1|2|3|4)\b", text_n).group(1))

        words = {
            "uno": 1,
            "una": 1,
            "dos": 2,
            "tres": 3,
            "cuatro": 4,
            "primera": 1,
            "primero": 1,
            "segunda": 2,
            "segundo": 2,
            "tercera": 3,
            "tercero": 3,
            "cuarta": 4,
            "cuarto": 4,
            "ultima": 4,
            "ultimo": 4,
        }
        for token, number in words.items():
            if re.search(rf"\b{token}\b", text_n):
                return number
        return None

    @staticmethod
    def _format_ingredient_name(name: str) -> str:
        return str(name).strip().replace("_", " ")

    @staticmethod
    def _build_breakfast_beverage_suggestion(available_all: list[str]) -> str:
        options: list[str] = []

        def _add_option(name: str) -> None:
            if name not in options:
                options.append(name)

        if any("fresa" in item for item in available_all):
            if any("leche" in item for item in available_all):
                _add_option("Batido de fresa con leche")
            _add_option("Jugo de fresa")

        if any("papaya" in item for item in available_all):
            _add_option("Jugo de papaya")
        if any("naranja" in item for item in available_all):
            _add_option("Jugo de naranja")
        if any("manzana" in item for item in available_all):
            _add_option("Jugo de manzana")
        if any("platano" in item or "banana" in item for item in available_all):
            _add_option("Batido de banana")
        if any("pina" in item or "piña" in item for item in available_all):
            _add_option("Jugo de pina")
        if any("limon" in item or "limón" in item for item in available_all):
            _add_option("Limonada casera")

        if any(
            "7 semillas" in item
            or "siete semillas" in item
            or "kiwicha" in item
            or "harina de soya" in item
            for item in available_all
        ):
            _add_option("Bebida de 7 semillas en agua")

        if any("avena" in item or "quaker" in item for item in available_all):
            _add_option("Avena licuada ligera")
        if any("leche" in item for item in available_all):
            _add_option("Vaso de leche")

        if any(
            any(token in item for token in ("gaseosa", "cola", "kola", "soda", "pepsi", "coca", "inca kola", "fanta", "sprite"))
            for item in available_all
        ):
            _add_option("Vaso de gaseosa fria")

        if options:
            return random.choice(options)
        return "Infusion caliente o agua con limon"

    @staticmethod
    def _build_recipe_detail_text(
        recipe_name: str,
        matched: list[str],
        missing: list[str],
        optional_missing: list[str],
        missing_in_fridge: list[str],
        steps: list[str],
        meal_text: str,
        beverage_hint: str | None = None,
        prep_minutes: int | None = None,
        cost_estimate: float | None = None,
        condiments_available: list[str] | None = None,
        condiments_missing: list[str] | None = None,
    ) -> str:
        matched_unique = []
        seen = set()
        for item in matched:
            key = ChatService._normalize(item)
            if key in seen:
                continue
            seen.add(key)
            matched_unique.append(ChatService._format_ingredient_name(item))

        missing_unique = []
        seen_missing = set()
        for item in missing:
            key = ChatService._normalize(item)
            if key in seen_missing:
                continue
            seen_missing.add(key)
            missing_unique.append(ChatService._format_ingredient_name(item))

        optional_unique = []
        seen_optional = set()
        for item in optional_missing:
            key = ChatService._normalize(item)
            if key in seen_optional:
                continue
            seen_optional.add(key)
            optional_unique.append(ChatService._format_ingredient_name(item))

        fridge_missing_unique = []
        seen_fridge_missing = set()
        for item in missing_in_fridge:
            key = ChatService._normalize(item)
            if key in seen_fridge_missing:
                continue
            seen_fridge_missing.add(key)
            fridge_missing_unique.append(ChatService._format_ingredient_name(item))

        lines = [f"Receta: {recipe_name}", "Porcion: 1 persona"]
        if prep_minutes is not None:
            lines.append(f"Tiempo estimado: {prep_minutes} minutos")
        if cost_estimate is not None:
            lines.append(f"Costo estimado: S/ {ChatService._fmt_num(cost_estimate)}")
        lines.extend(["", "Ingredientes:"])
        if matched_unique:
            lines.extend(f"- {name}" for name in matched_unique)
        else:
            lines.append("- Revisa los ingredientes base de la receta")

        if missing_unique:
            lines.append("")
            lines.append("Te faltaria comprar:")
            lines.extend(f"- {name}" for name in missing_unique)

        if optional_unique:
            lines.append("")
            lines.append("Opcionales si deseas mejorar la receta:")
            lines.extend(f"- {name}" for name in optional_unique)

        if fridge_missing_unique:
            lines.append("")
            lines.append("En tu refri te faltaria (pero si tienes en casa):")
            lines.extend(f"- {name}" for name in fridge_missing_unique)

        condiments_available = condiments_available or []
        condiments_missing = condiments_missing or []
        if condiments_available or condiments_missing:
            lines.append("")
            lines.append("Condimentos sugeridos:")
            if condiments_available:
                lines.extend(f"- Tienes: {ChatService._format_ingredient_name(name)}" for name in condiments_available)
            if condiments_missing:
                lines.extend(f"- Te faltaria: {ChatService._format_ingredient_name(name)}" for name in condiments_missing)

        lines.append("")
        lines.append("Preparacion:")
        detailed_steps = ChatService._ensure_detailed_recipe_steps(recipe_name, matched_unique, steps)
        if detailed_steps:
            lines.extend(f"{idx}. {step}" for idx, step in enumerate(detailed_steps, start=1))
        else:
            lines.append("1. Cocina los ingredientes principales.")
            lines.append("2. Integra los complementos y ajusta sazon.")
            lines.append("3. Sirve caliente o fresco segun corresponda.")

        if "desayuno" in ChatService._normalize(meal_text) and beverage_hint:
            lines.append("")
            lines.append(f"Bebida sugerida: {beverage_hint}.")

        lines.append("")
        pet_guidance = ChatService._build_pet_guidance(recipe_name, matched_unique, meal_text)
        lines.append(f"Nota mascota: {pet_guidance}")
        return "\n".join(lines)

    @staticmethod
    def _build_pet_guidance(recipe_name: str, ingredients: list[str], meal_text: str) -> str:
        ingredients_text = ", ".join(ingredients[:10]) if ingredients else "sin detalle de ingredientes"
        api_key = os.getenv("GEMINI_API_KEY", "").strip()

        if api_key:
            try:
                import httpx
                import json as _json

                prompt = (
                    "Eres asistente de seguridad alimentaria para mascotas (perro/gato). "
                    "Con la siguiente receta, responde recomendaciones cortas y accionables para casa.\n"
                    f"Receta: {recipe_name}.\n"
                    f"Comida: {meal_text}.\n"
                    f"Ingredientes: {ingredients_text}.\n"
                    "Devuelve SOLO JSON válido con esta forma: "
                    '{"puede_dar":"...", "evitar":"...", "moderacion":"...", "alerta":"..."}. '
                    "No des dosis médicas. Máximo 18 palabras por campo."
                )

                url = (
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"gemini-2.0-flash:generateContent?key={api_key}"
                )
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.4, "maxOutputTokens": 300},
                }

                with httpx.Client(timeout=8.0) as client:
                    resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text.strip())
                    raw_text = re.sub(r"\s*```$", "", raw_text.strip())
                    parsed = _json.loads(raw_text)

                    puede = str(parsed.get("puede_dar", "")).strip()
                    evitar = str(parsed.get("evitar", "")).strip()
                    moderacion = str(parsed.get("moderacion", "")).strip()
                    alerta = str(parsed.get("alerta", "")).strip()

                    chunks = [item for item in (puede, moderacion, evitar, alerta) if item]
                    if chunks:
                        return " ".join(chunks)
            except Exception:
                pass

        # Fallback dinámico por ingredientes cuando Gemini no responde.
        ingredient_n = [ChatService._normalize(item) for item in ingredients]

        toxic_hits = []
        for token, warning in (
            ("cebolla", "No dar cebolla"),
            ("ajo", "No dar ajo"),
            ("uva", "No dar uvas"),
            ("chocolate", "No dar chocolate"),
            ("xilitol", "No dar xilitol"),
            ("alcohol", "No dar alcohol"),
            ("palta", "Palta solo en minima cantidad y sin pepa"),
            ("atun", "Atun solo ocasional y sin sal"),
            ("sal", "Evitar exceso de sal"),
        ):
            if any(token in name for name in ingredient_n):
                toxic_hits.append(warning)

        safe_hits = []
        for token, tip in (
            ("pollo", "Puedes dar pollo cocido sin hueso ni condimentos"),
            ("huevo", "Puedes dar huevo bien cocido en poca cantidad"),
            ("arroz", "Arroz cocido simple puede ayudar al estomago sensible"),
            ("zanahoria", "Zanahoria cocida en trozos pequeños suele ser segura"),
            ("pescado", "Pescado cocido sin espinas ni sal puede ser opcion"),
        ):
            if any(token in name for name in ingredient_n):
                safe_hits.append(tip)

        parts = []
        if safe_hits:
            parts.append(safe_hits[0] + ".")
        if toxic_hits:
            parts.append("; ".join(toxic_hits[:2]) + ".")
        parts.append("Ofrécelo siempre tibio, en porción pequeña y sin aderezos; ante vómito o diarrea consulta veterinario.")
        return " ".join(parts)

    @staticmethod
    def _ensure_detailed_recipe_steps(recipe_name: str, ingredients: list[str], steps: list[str]) -> list[str]:
        cleaned_steps = [str(step).strip() for step in (steps or []) if str(step).strip()]

        def _is_vague(text: str) -> bool:
            text_n = ChatService._normalize(text)
            vague_phrases = (
                "cocina por separado",
                "mezcla todo",
                "ajusta sabor",
                "sirve al momento",
                "saltea las verduras",
            )
            return any(phrase in text_n for phrase in vague_phrases)

        if cleaned_steps:
            avg_words = sum(len(step.split()) for step in cleaned_steps) / max(1, len(cleaned_steps))
            vague_count = sum(1 for step in cleaned_steps if _is_vague(step))
            time_or_heat_hits = sum(
                1
                for step in cleaned_steps
                if re.search(r"\b\d+\s*(?:min|mins|minuto|minutos)\b|\bfuego\b|\bhervor\b|\bdorar\b", ChatService._normalize(step))
            )
            # Keep existing steps only when they are already detailed enough.
            if len(cleaned_steps) >= 6 and avg_words >= 9 and vague_count <= 1 and time_or_heat_hits >= 3:
                return cleaned_steps

            expanded_steps: list[str] = []
            for index, step in enumerate(cleaned_steps[:8], start=1):
                step_clean = step.rstrip(".")
                has_time = bool(re.search(r"\b\d+\s*(?:min|mins|minuto|minutos)\b", ChatService._normalize(step_clean)))
                has_heat = bool(re.search(r"\bfuego\b|\bhervor\b|\bdorar\b", ChatService._normalize(step_clean)))

                if index == 1:
                    suffix = "a fuego medio por 2 a 3 minutos, mezclando de forma constante"
                elif index <= 3:
                    suffix = "por 4 a 6 minutos a fuego medio, hasta integrar sabores"
                elif index <= 5:
                    suffix = "a fuego medio-bajo por 5 minutos, controlando textura y humedad"
                else:
                    suffix = "durante 2 a 3 minutos finales, probando sal y punto de coccion"

                if has_time and has_heat:
                    expanded_steps.append(f"{step_clean}.")
                elif has_time or has_heat:
                    expanded_steps.append(f"{step_clean}, {suffix}.")
                else:
                    expanded_steps.append(f"{step_clean} {suffix}.")

            if len(expanded_steps) < 6:
                expanded_steps.append(
                    "Si necesita liquido, agrega 2 a 4 cucharadas de agua caliente y cocina 2 minutos a fuego bajo para estabilizar la salsa."
                )
            if len(expanded_steps) < 7:
                expanded_steps.append(
                    "Apaga, deja reposar 2 minutos, rectifica sal y sirve caliente para conservar aroma y textura."
                )
            return expanded_steps

        recipe_n = ChatService._normalize(recipe_name)
        ingredient_text = ", ".join(ingredients[:6]) if ingredients else "tus ingredientes disponibles"
        ingredient_n = [ChatService._normalize(item) for item in ingredients]
        has_legume = any(any(token in name for token in ("garban", "garganz", "lentej", "frijol")) for name in ingredient_n)
        has_potato = any("papa" in name for name in ingredient_n)
        has_onion = any("cebolla" in name for name in ingredient_n)
        has_garlic = any("ajo" in name for name in ingredient_n)
        has_lemon = any("limon" in name for name in ingredient_n)
        has_rice = any("arroz" in name for name in ingredient_n)
        has_pasta = any(any(token in name for token in ("fideo", "pasta")) for name in ingredient_n)
        has_protein = any(
            any(token in name for token in ("pollo", "huevo", "atun", "atun", "pescado", "carne"))
            for name in ingredient_n
        )

        is_stir_fry = any(token in recipe_n for token in ("saltado", "salteado", "chaufa"))
        is_sudado = "sudado" in recipe_n
        is_olla = "olla" in recipe_n
        is_guiso = "guiso" in recipe_n or has_legume or has_potato

        aderezo_parts = []
        if has_onion:
            aderezo_parts.append("cebolla")
        if has_garlic:
            aderezo_parts.append("ajo")
        aderezo_text = ", ".join(aderezo_parts) if aderezo_parts else "una base aromatica"

        detailed = [
            f"Alista la mise en place para 1 persona: separa {ingredient_text}, lava y desinfecta todo, y pica fino lo que vaya en aderezo.",
        ]

        if is_stir_fry:
            if has_rice:
                detailed.append(
                    "Si usaras arroz para chaufa, cocinalo antes y enfriarlo 10 minutos para que no se apelmace al saltear; usa 1 taza de arroz cocido por porcion."
                )
            if has_pasta:
                detailed.append(
                    "Si lleva fideo o pasta, hiervelos en agua con sal hasta punto al dente (8 a 10 minutos), escurre y reserva con unas gotas de aceite."
                )
            detailed.append(
                f"Calienta la sarten o wok a fuego alto, agrega 1 cucharada de aceite y saltea {aderezo_text} por 2 a 3 minutos sin dejar de mover."
            )
            if has_protein:
                detailed.append(
                    "Incorpora la proteina principal en tiras o cubos y dorala 4 a 6 minutos; primero sella, luego mueve para que no pierda jugos."
                )
            detailed.append(
                "Agrega el carbohidrato base y saltea 2 a 4 minutos, mezclando con movimientos envolventes para integrar sabores sin romper la textura."
            )
            detailed.append(
                "Sazona al final, prueba punto de sal y sirve de inmediato para mantener el salteado jugoso y con buen color."
            )
        elif is_sudado:
            detailed.append(
                f"En olla ancha, sofrie {aderezo_text} a fuego medio por 5 minutos hasta que el aderezo quede brillante y aromatico."
            )
            detailed.append(
                "Agrega 3/4 de taza de agua, tapa y deja hervir suave 3 minutos para formar base de coccion."
            )
            detailed.append(
                "Coloca la proteina encima sin mover demasiado y cocina tapado a fuego medio-bajo de 10 a 15 minutos para que se cocine al vapor del aderezo."
            )
            detailed.append(
                "Destapa, baña con su jugo, corrige sal y cocina 2 minutos mas hasta que la salsa espese ligeramente."
            )
        elif is_olla:
            detailed.append(
                f"Sella en olla la proteina o base del plato con 1 cucharada de aceite por 3 a 4 minutos; luego agrega {aderezo_text} y cocina 4 minutos."
            )
            detailed.append(
                "Cubre con agua caliente o caldo hasta apenas tapar, lleva a hervor y baja a fuego medio-bajo."
            )
            if has_potato:
                detailed.append(
                    "Incorpora la papa a mitad de coccion y cocina 15 minutos para que quede tierna sin deshacerse."
                )
            detailed.append(
                "Mantiene hervor suave 20 a 30 minutos, retirando espuma si aparece, hasta que el fondo quede concentrado y sabroso."
            )
        else:
            if has_legume:
                detailed.append(
                    "Si la legumbre esta seca, remojala 8 a 12 horas; luego cocinala en agua limpia 35 a 50 minutos hasta que este suave. Si ya esta cocida, enjuagala y reserva."
                )
            detailed.append(
                f"En una olla o sarten profunda, sofrie {aderezo_text} a fuego medio de 4 a 6 minutos hasta dorar ligeramente."
            )
            if has_potato:
                detailed.append(
                    "Agrega la papa en cubos, incorpora 1/2 taza de agua y cocina tapado 12 a 15 minutos a fuego medio-bajo hasta que este tierna."
                )
            detailed.append(
                "Integra la base principal del plato, sazona y cocina 5 a 8 minutos mas para que tome cuerpo; si espesa demasiado, añade 2 cucharadas de agua."
            )

        if has_lemon:
            detailed.append(
                "Apaga el fuego y termina con unas gotas de limón para levantar el sabor; prueba y corrige sal antes de servir."
            )
        else:
            detailed.append(
                "Apaga el fuego, deja reposar 2 minutos y ajusta el punto de sal antes de servir."
            )

        detailed.append(
            "Sirve caliente en porción individual y, si queda para después, enfría y guarda en recipiente tapado dentro de 2 horas para conservar textura y seguridad."
        )
        return detailed

    @staticmethod
    def _get_weekly_recipe_history(db: Session) -> list[dict]:
        item = MemoryService.get_by_key(db, "__recipe_history__")
        if not item or not item.value:
            return []
        try:
            import json

            data = json.loads(item.value)
            if isinstance(data, list):
                return [entry for entry in data if isinstance(entry, dict)]
        except Exception:
            return []
        return []

    @staticmethod
    def _save_weekly_recipe_history(db: Session, history: list[dict]) -> None:
        import json

        # Keep only recent entries to avoid unbounded growth.
        compact = history[-60:]
        MemoryService.save_item(
            db,
            MemoryCreate(key="__recipe_history__", value=json.dumps(compact, ensure_ascii=False)),
        )

    @staticmethod
    def _remember_recipe_usage(db: Session, recipe_name: str) -> None:
        today = datetime.utcnow().date()
        weekday_names = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
        iso_week = today.isocalendar()
        week_marker = f"{iso_week.year}-W{iso_week.week:02d}"
        entry = {
            "name": str(recipe_name).strip(),
            "date": today.isoformat(),
            "week": week_marker,
            "weekday": weekday_names[today.weekday()],
        }

        history = ChatService._get_weekly_recipe_history(db)
        history.append(entry)
        ChatService._save_weekly_recipe_history(db, history)

    @staticmethod
    def _weekly_used_recipe_names(db: Session) -> list[str]:
        today = datetime.utcnow().date()
        iso_week = today.isocalendar()
        week_marker = f"{iso_week.year}-W{iso_week.week:02d}"
        history = ChatService._get_weekly_recipe_history(db)

        names: list[str] = []
        seen = set()
        for entry in history:
            if str(entry.get("week", "")) != week_marker:
                continue
            name = str(entry.get("name", "")).strip()
            name_n = ChatService._normalize(name)
            if not name or name_n in seen:
                continue
            seen.add(name_n)
            names.append(name)
        return names

    @staticmethod
    def _is_explicit_repeat_request(text_n: str, weekly_recipe_names: list[str]) -> bool:
        repeat_hints = (
            "repite",
            "repetir",
            "repitela",
            "repitelo",
            "la misma",
            "otra vez",
            "de nuevo",
        )
        weekday_hints = ("lunes", "martes", "miercoles", "miércoles", "jueves", "viernes", "sabado", "sábado", "domingo")

        if ChatService._contains_any(text_n, repeat_hints) or ChatService._contains_any(text_n, weekday_hints):
            return True

        for name in weekly_recipe_names:
            name_n = ChatService._normalize(name)
            if len(name_n) < 4:
                continue
            if re.search(rf"\b{re.escape(name_n)}\b", text_n):
                return True
        return False

    @staticmethod
    def _extract_weekday_reference(text_n: str) -> str | None:
        weekday_aliases = {
            "lunes": "lunes",
            "martes": "martes",
            "miercoles": "miercoles",
            "miércoles": "miercoles",
            "jueves": "jueves",
            "viernes": "viernes",
            "sabado": "sabado",
            "sábado": "sabado",
            "domingo": "domingo",
        }
        for token, canonical in weekday_aliases.items():
            if re.search(rf"\b{re.escape(token)}\b", text_n):
                return canonical
        return None

    @staticmethod
    def _weekly_recipe_for_weekday(db: Session, weekday: str) -> dict | None:
        today = datetime.utcnow().date()
        iso_week = today.isocalendar()
        week_marker = f"{iso_week.year}-W{iso_week.week:02d}"
        history = ChatService._get_weekly_recipe_history(db)
        # Return the latest recipe used for the requested day in the current week.
        for entry in reversed(history):
            if str(entry.get("week", "")) != week_marker:
                continue
            if ChatService._normalize(str(entry.get("weekday", ""))) != ChatService._normalize(weekday):
                continue
            if str(entry.get("name", "")).strip():
                return entry
        return None

    @staticmethod
    def _call_gemini_for_recipes(
        available_ingredients: list[str],
        meal_hints: list[str],
        healthy: bool,
        last_shown: list[str],
    ) -> list[dict] | None:
        """Call Gemini API to generate recipe suggestions based on available ingredients.
        Returns candidate dicts compatible with the local engine, or None on any error."""
        import json as _json

        try:
            import httpx
        except ImportError:
            return None

        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            return None

        meals_text = "/".join(meal_hints) if meal_hints else "almuerzo o cena"
        healthy_clause = " Prioriza recetas saludables y balanceadas." if healthy else ""
        avoid_clause = (
            f" Evita repetir estas recetas que ya le mostré: {', '.join(last_shown[:6])}."
            if last_shown
            else ""
        )
        ingredients_text = ", ".join(available_ingredients[:40])
        available_norm = [ChatService._normalize(item) for item in available_ingredients if str(item).strip()]
        available_set = {item for item in available_norm if item}
        staple_whitelist = {
            "agua",
            "sal",
            "pimienta",
            "aceite",
            "oregano",
            "orégano",
            "comino",
            "ajo",
            "cebolla",
            "limon",
            "limón",
        }

        def _ingredient_is_available(raw_ingredient: str) -> bool:
            ing = ChatService._normalize(raw_ingredient)
            if not ing:
                return False
            if ing in available_set or ing in staple_whitelist:
                return True
            for available in available_set:
                if len(ing) <= 4:
                    if re.search(rf"\b{re.escape(ing)}\b", available):
                        return True
                elif ing in available or available in ing:
                    return True
            return False

        def _build_prompt(repair_mode: bool = False) -> str:
            extra = ""
            if repair_mode:
                extra = (
                    " MODO REPARACION: si una receta usa ingrediente no disponible o pasos ambiguos, reemplazala por otra valida."
                )
            return (
                f"Tengo estos ingredientes en casa: {ingredients_text}.\n"
                f"Genera exactamente 4 recetas peruanas COMPLETAS para {meals_text}.{healthy_clause}"
                f"{avoid_clause}{extra}\n"
                "REGLAS ESTRICTAS:\n"
                "- Usa SOLO los ingredientes que te di; no inventes ingredientes fuera de lista.\n"
                "- Cada receta debe poder prepararse completamente con lo disponible.\n"
                "- Genera recetas variadas (distintos tipos de platos).\n"
                "- Preparacion obligatoria de 6 a 8 pasos concretos, sin ambiguedad.\n"
                "- En al menos 4 pasos, incluye tiempo (minutos) y fuego/textura objetivo.\n"
                "- Incluye cantidades aproximadas para 1 persona cuando aplique.\n"
                "Responde SOLO JSON válido:\n"
                '{"recetas": [{"nombre": "...", "tipo_comida": "almuerzo", '
                '"ingredientes": ["ingrediente1"], "pasos": ["Paso 1..."], "tiempo_minutos": 25}]}'
            )

        def _request_json(prompt_text: str, temperature: float) -> dict | None:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.0-flash:generateContent?key={api_key}"
            )
            payload = {
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {"temperature": temperature, "maxOutputTokens": 1800},
            }
            try:
                with httpx.Client(timeout=12.0) as client:
                    resp = client.post(url, json=payload)
                if resp.status_code != 200:
                    return None
                data = resp.json()
                raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text.strip())
                raw_text = re.sub(r"\s*```$", "", raw_text.strip())
                try:
                    return _json.loads(raw_text)
                except Exception:
                    json_match = re.search(r"\{[\s\S]*\}", raw_text)
                    if not json_match:
                        return None
                    return _json.loads(json_match.group(0))
            except Exception:
                return None

        def _sanitize_recipe(raw_recipe: dict) -> dict | None:
            name = str(raw_recipe.get("nombre", "")).strip()
            meal_raw = ChatService._normalize(str(raw_recipe.get("tipo_comida", "almuerzo")).strip())
            meal_tuple: tuple[str, ...] = (
                (meal_raw,) if meal_raw in ("desayuno", "almuerzo", "cena") else ("almuerzo", "cena")
            )

            raw_ingredients = [str(item).strip() for item in (raw_recipe.get("ingredientes") or []) if str(item).strip()]
            if not name or not raw_ingredients:
                return None

            matched: list[str] = []
            missing: list[str] = []
            seen_ing = set()
            for ingredient in raw_ingredients:
                ing_n = ChatService._normalize(ingredient)
                if not ing_n or ing_n in seen_ing:
                    continue
                seen_ing.add(ing_n)
                if _ingredient_is_available(ingredient):
                    matched.append(ing_n)
                else:
                    missing.append(ing_n)

            # Reject Gemini outputs that invent unavailable ingredients.
            if not matched or missing:
                return None

            raw_steps = [str(step).strip() for step in (raw_recipe.get("pasos") or []) if str(step).strip()]
            steps = ChatService._ensure_detailed_recipe_steps(name, matched, raw_steps)
            if len(steps) < 6:
                return None

            detail_hits = sum(
                1
                for step in steps
                if re.search(r"\b\d+\s*(?:min|mins|minuto|minutos)\b|\bfuego\b|\bhervor\b|\bdorar\b", ChatService._normalize(step))
            )
            if detail_hits < 3:
                return None

            try:
                minutes = int(raw_recipe.get("tiempo_minutos") or 25)
            except Exception:
                minutes = 25
            minutes = max(8, min(minutes, 120))

            return {
                "name": name,
                "meal": ", ".join(meal_tuple),
                "is_complete": True,
                "score": 100,
                "matched": matched,
                "missing": [],
                "optional_missing": [],
                "missing_in_fridge": [],
                "steps": steps,
                "beverage_hint": None,
                "prep_minutes": minutes,
                "cost_estimate": None,
                "source": "gemini",
            }

        # Attempt 1: diverse generation. Attempt 2: repair mode for stricter correction.
        attempts = [
            (_build_prompt(repair_mode=False), 0.85),
            (_build_prompt(repair_mode=True), 0.35),
        ]

        result: list[dict] = []
        seen_names: set[str] = set()

        for prompt_text, temperature in attempts:
            parsed = _request_json(prompt_text, temperature)
            if not parsed:
                continue

            for raw_recipe in (parsed.get("recetas") or [])[:6]:
                if not isinstance(raw_recipe, dict):
                    continue
                candidate = _sanitize_recipe(raw_recipe)
                if not candidate:
                    continue

                key = ChatService._normalize(str(candidate.get("name", "")))
                if not key or key in seen_names:
                    continue
                seen_names.add(key)
                result.append(candidate)
                if len(result) >= 4:
                    return result

        return result or None

    @staticmethod
    def _build_recipes_reply(db: Session, text_n: str) -> str:
        global _PENDING

        def _requested_meals(user_text_n: str) -> list[str]:
            requested: list[str] = []
            if re.search(r"\bdesayuno\b", user_text_n):
                requested.append("desayuno")
            if re.search(r"\balmuerzo\b", user_text_n):
                requested.append("almuerzo")
            if re.search(r"\bcena\b", user_text_n):
                requested.append("cena")
            return requested or ["desayuno", "almuerzo", "cena"]

        def _is_healthy_requested(user_text_n: str) -> bool:
            healthy_patterns = (
                r"\bsaludable\b",
                r"\bsaludables\b",
                r"\bsano\b",
                r"\bsana\b",
                r"\bligero\b",
                r"\bligera\b",
                r"\bdieta\b",
                r"\bfitness\b",
                r"\bfit\b",
                r"\bbalancead[oa]s?\b",
                r"\bbajo\s+en\s+grasa\b",
            )
            return any(re.search(pattern, user_text_n) for pattern in healthy_patterns)

        def _requested_time_preference(user_text_n: str) -> str | None:
            if re.search(r"\b(rapido|rápido|facil|fácil|al\s+toque|express)\b", user_text_n):
                return "rapido"
            if re.search(r"\b(elaborado|especial|tranquilo|con\s+tiempo|gourmet)\b", user_text_n):
                return "elaborado"
            return None

        def _requested_budget_preference(user_text_n: str) -> bool:
            return bool(re.search(r"\b(barato|economico|económico|ahorrar|ahorro|bajo\s+costo)\b", user_text_n))

        def _breakfast_block_label(recipe_name: str) -> str:
            name_n = ChatService._normalize(recipe_name)
            if "combo desayuno" in name_n:
                return "Combos"
            if any(token in name_n for token in ("jugo", "batido", "licuado", "bebida", "limonada", "refresco", "gaseosa", "cola", "kola", "soda", "infusion", "infusion")):
                return "Bebidas"
            if any(token in name_n for token in ("pan", "tostada", "sanguche", "sandwich", "sanduche")):
                return "Panes/Sanguches"
            if any(token in name_n for token in ("fruta", "ensalada de frutas", "yogurt")):
                return "Frutas"
            return "Otras opciones"

        def _merge_unique_items(values: list[str]) -> list[str]:
            merged: list[str] = []
            seen = set()
            for raw in values:
                key = ChatService._normalize(str(raw))
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(str(raw))
            return merged

        def _collapse_inventory_compounds(items: list[str], available: list[str]) -> list[str]:
            """Merge split item names into compound inventory products when possible.
            Example: if inventory has 'hongos laurel' and output has 'hongos' + 'laurel',
            replace both with 'hongos laurel'.
            """
            if not items:
                return []

            normalized_items = _merge_unique_items(items)
            token_stopwords = {"de", "del", "la", "el", "los", "las", "con", "y"}

            # Build compound candidates from current stock names (2+ meaningful words).
            compounds: list[tuple[str, list[str]]] = []
            seen_compounds = set()
            for raw_name in available:
                compound_name = ChatService._normalize(str(raw_name))
                if not compound_name or compound_name in seen_compounds:
                    continue
                tokens = [t for t in compound_name.split() if t not in token_stopwords]
                if len(tokens) < 2:
                    continue
                seen_compounds.add(compound_name)
                compounds.append((compound_name, tokens))

            # Prefer longer compounds first.
            compounds.sort(key=lambda item: len(item[1]), reverse=True)

            current = list(normalized_items)

            def _item_contains_token(item_n: str, token: str) -> bool:
                return re.search(rf"\b{re.escape(token)}\b", item_n) is not None

            for compound_name, tokens in compounds:
                if any(ChatService._normalize(entry) == compound_name for entry in current):
                    continue

                matched_indexes: list[int] = []
                used_indexes = set()
                for token in tokens:
                    found_index = None
                    for idx, raw_item in enumerate(current):
                        if idx in used_indexes:
                            continue
                        item_n = ChatService._normalize(raw_item)
                        if _item_contains_token(item_n, token):
                            found_index = idx
                            break
                    if found_index is None:
                        matched_indexes = []
                        break
                    used_indexes.add(found_index)
                    matched_indexes.append(found_index)

                if not matched_indexes:
                    continue

                # Remove split parts and add compound label.
                for idx in sorted(matched_indexes, reverse=True):
                    current.pop(idx)
                current.append(compound_name)

            return _merge_unique_items(current)

        def _build_breakfast_combo_options(pool: list[dict], max_options: int = 4) -> list[dict]:
            beverages = [item for item in pool if _breakfast_block_label(str(item.get("name", ""))) == "Bebidas"]
            companions = [
                item for item in pool
                if _breakfast_block_label(str(item.get("name", ""))) in ("Panes/Sanguches", "Frutas", "Otras opciones")
            ]

            if not beverages or not companions:
                return []

            def _companion_priority(item: dict) -> int:
                label = _breakfast_block_label(str(item.get("name", "")))
                if label == "Panes/Sanguches":
                    return 0
                if label == "Frutas":
                    return 1
                return 2

            companions = sorted(companions, key=_companion_priority)
            combos: list[dict] = []
            used_pairs = set()

            for index, companion in enumerate(companions):
                if len(combos) >= max_options:
                    break

                beverage = beverages[index % len(beverages)]
                pair_key = (
                    ChatService._normalize(str(beverage.get("name", ""))),
                    ChatService._normalize(str(companion.get("name", ""))),
                )
                if pair_key in used_pairs:
                    continue
                used_pairs.add(pair_key)

                b_name = str(beverage.get("name", "Bebida"))
                c_name = str(companion.get("name", "Acompanamiento"))
                b_steps = list(beverage.get("steps") or [])[:4]
                c_steps = list(companion.get("steps") or [])[:4]

                combo_steps = [f"Bebida - {b_name}: {step}" for step in b_steps] + [
                    f"Acompanamiento - {c_name}: {step}" for step in c_steps
                ]

                b_matched = list(beverage.get("matched") or [])
                c_matched = list(companion.get("matched") or [])
                b_missing = list(beverage.get("missing") or [])
                c_missing = list(companion.get("missing") or [])
                b_missing_optional = list(beverage.get("optional_missing") or [])
                c_missing_optional = list(companion.get("optional_missing") or [])
                b_missing_fridge = list(beverage.get("missing_in_fridge") or [])
                c_missing_fridge = list(companion.get("missing_in_fridge") or [])
                b_cond_ok = list(beverage.get("condiments_available") or [])
                c_cond_ok = list(companion.get("condiments_available") or [])
                b_cond_miss = list(beverage.get("condiments_missing") or [])
                c_cond_miss = list(companion.get("condiments_missing") or [])

                b_cost = beverage.get("cost_estimate")
                c_cost = companion.get("cost_estimate")
                combo_cost = None
                if b_cost is not None or c_cost is not None:
                    combo_cost = round(float(b_cost or 0) + float(c_cost or 0), 2)

                b_prep = int(beverage.get("prep_minutes") or 0)
                c_prep = int(companion.get("prep_minutes") or 0)
                combo_prep = max(8, b_prep + c_prep)

                combos.append(
                    {
                        "name": f"Combo desayuno: {b_name} + {c_name}",
                        "meal": "desayuno",
                        "is_complete": bool(beverage.get("is_complete", False)) and bool(companion.get("is_complete", False)),
                        "score": int(beverage.get("score", 0)) + int(companion.get("score", 0)) + 20,
                        "matched": _merge_unique_items(b_matched + c_matched),
                        "missing": _merge_unique_items(b_missing + c_missing),
                        "optional_missing": _merge_unique_items(b_missing_optional + c_missing_optional),
                        "missing_in_fridge": _merge_unique_items(b_missing_fridge + c_missing_fridge),
                        "steps": combo_steps,
                        "beverage_hint": b_name,
                        "prep_minutes": combo_prep,
                        "cost_estimate": combo_cost,
                        "condiments_available": _merge_unique_items(b_cond_ok + c_cond_ok),
                        "condiments_missing": _merge_unique_items(b_cond_miss + c_cond_miss),
                        "source": str(beverage.get("source") or companion.get("source") or "combo"),
                    }
                )

            return combos[:max_options]

        def _build_lunch_combo_options(pool: list[dict], max_options: int = 4) -> list[dict]:
            """Pick lunch combos with exactly 2 elements: plato principal + bebida/acompanamiento."""
            def _is_beverage(item: dict) -> bool:
                name_n = ChatService._normalize(str(item.get("name", "")))
                return any(token in name_n for token in ("jugo", "batido", "licuado", "bebida", "limonada", "refresco", "gaseosa", "cola", "kola", "soda", "infusion"))

            def _is_side(item: dict) -> bool:
                name_n = ChatService._normalize(str(item.get("name", "")))
                return any(token in name_n for token in ("ensalada", "fruta", "yogurt", "pan", "tostada", "sanguche", "sandwich"))

            beverages = [item for item in pool if _is_beverage(item)]
            mains = [item for item in pool if not _is_beverage(item) and not _is_side(item)]
            companions = [item for item in pool if _is_side(item)]

            if not mains:
                return []

            combos: list[dict] = []
            used_pairs = set()

            for index, main in enumerate(mains):
                if len(combos) >= max_options:
                    break

                partner = None
                if beverages:
                    partner = beverages[index % len(beverages)]
                elif companions:
                    partner = companions[index % len(companions)]

                if not partner:
                    continue

                pair_key = (
                    ChatService._normalize(str(main.get("name", ""))),
                    ChatService._normalize(str(partner.get("name", ""))),
                )
                if pair_key in used_pairs:
                    continue
                used_pairs.add(pair_key)

                m_name = str(main.get("name", "Plato principal"))
                p_name = str(partner.get("name", "Acompanamiento"))
                m_steps = list(main.get("steps") or [])[:5]
                p_steps = list(partner.get("steps") or [])[:3]

                combo_steps = [f"Plato principal - {m_name}: {step}" for step in m_steps] + [
                    f"Bebida/Acompanamiento - {p_name}: {step}" for step in p_steps
                ]

                m_matched = list(main.get("matched") or [])
                p_matched = list(partner.get("matched") or [])
                m_missing = list(main.get("missing") or [])
                p_missing = list(partner.get("missing") or [])
                m_missing_optional = list(main.get("optional_missing") or [])
                p_missing_optional = list(partner.get("optional_missing") or [])
                m_missing_fridge = list(main.get("missing_in_fridge") or [])
                p_missing_fridge = list(partner.get("missing_in_fridge") or [])
                m_cond_ok = list(main.get("condiments_available") or [])
                p_cond_ok = list(partner.get("condiments_available") or [])
                m_cond_miss = list(main.get("condiments_missing") or [])
                p_cond_miss = list(partner.get("condiments_missing") or [])

                m_cost = main.get("cost_estimate")
                p_cost = partner.get("cost_estimate")
                combo_cost = None
                if m_cost is not None or p_cost is not None:
                    combo_cost = round(float(m_cost or 0) + float(p_cost or 0), 2)

                m_prep = int(main.get("prep_minutes") or 0)
                p_prep = int(partner.get("prep_minutes") or 0)
                combo_prep = max(15, m_prep + p_prep)

                combos.append(
                    {
                        "name": f"Combo almuerzo: {m_name} + {p_name}",
                        "meal": "almuerzo",
                        "is_complete": bool(main.get("is_complete", False)) and bool(partner.get("is_complete", False)),
                        "score": int(main.get("score", 0)) + int(partner.get("score", 0)) + 20,
                        "matched": _merge_unique_items(m_matched + p_matched),
                        "missing": _merge_unique_items(m_missing + p_missing),
                        "optional_missing": _merge_unique_items(m_missing_optional + p_missing_optional),
                        "missing_in_fridge": _merge_unique_items(m_missing_fridge + p_missing_fridge),
                        "steps": combo_steps,
                        "beverage_hint": p_name if _breakfast_block_label(p_name) == "Bebidas" else None,
                        "prep_minutes": combo_prep,
                        "cost_estimate": combo_cost,
                        "condiments_available": _merge_unique_items(m_cond_ok + p_cond_ok),
                        "condiments_missing": _merge_unique_items(m_cond_miss + p_cond_miss),
                        "source": str(main.get("source") or partner.get("source") or "combo"),
                    }
                )

            return combos[:max_options]

        def _select_breakfast_varied_options(pool: list[dict], max_options: int = 4) -> list[dict]:
            """Pick breakfast options with block diversity using ranked pool order.
            Target mix: up to 2 bebidas, up to 1 pan/sanguche, up to 1 frutas."""
            grouped: dict[str, list[dict]] = {
                "Bebidas": [],
                "Panes/Sanguches": [],
                "Frutas": [],
                "Otras opciones": [],
            }

            for item in pool:
                block = _breakfast_block_label(str(item.get("name", "")))
                grouped.setdefault(block, []).append(item)

            selected: list[dict] = []

            def _take(block: str, limit: int) -> None:
                for candidate in grouped.get(block, []):
                    if len(selected) >= max_options:
                        return
                    if candidate in selected:
                        continue
                    if limit <= 0:
                        return
                    selected.append(candidate)
                    limit -= 1

            _take("Bebidas", 2)
            _take("Panes/Sanguches", 1)
            _take("Frutas", 1)

            if len(selected) < max_options:
                for block in ("Bebidas", "Panes/Sanguches", "Frutas", "Otras opciones"):
                    for candidate in grouped.get(block, []):
                        if len(selected) >= max_options:
                            break
                        if candidate in selected:
                            continue
                        selected.append(candidate)
                    if len(selected) >= max_options:
                        break

            return selected[:max_options]

        ingredient_aliases: dict[str, tuple[str, ...]] = {
            "avena": ("avena", "quaker"),
            "quaker": ("avena", "quaker"),
            "platano": ("platano", "banana"),
            "banana": ("platano", "banana"),
            "pina": ("pina", "piña"),
            "piña": ("pina", "piña"),
            "limon": ("limon", "limón"),
            "limón": ("limon", "limón"),
            "atun": ("atun", "atún"),
            "atún": ("atun", "atún"),
            "lenteja": ("lenteja", "lentejas"),
            "garbanzo": ("garbanzo", "garbanzos", "garganzo", "garganzos"),
            "garganzo": ("garbanzo", "garbanzos", "garganzo", "garganzos"),
            "7 semillas": ("7 semillas", "siete semillas", "harina 7 semillas", "harina de soya", "kiwicha"),
            "sillao": ("sillao", "soya", "salsa de soya"),
            "ajinomoto": ("ajinomoto",),
            "hongos laurel": (
                "hongos laurel",
                "hongo laurel",
                "laurel hongos",
                "salsa de hongos laurel",
                "salsa hongos laurel",
            ),
            "hongos": ("hongos", "salsa de hongos"),
            "laurel": ("laurel", "hoja de laurel", "hojas de laurel"),
            "mayonesa": ("mayonesa",),
            "ketchup": ("ketchup", "catsup"),
            "pimienta": ("pimienta",),
            "sal": ("sal",),
        }

        base_condiments = ("sal", "pimienta")

        def _variants(ingredient: str) -> tuple[str, ...]:
            key = ChatService._normalize(ingredient)
            return ingredient_aliases.get(key, (key,))

        def _variant_in_name(variant: str, name: str) -> bool:
            variant_n = ChatService._normalize(variant)
            name_n = ChatService._normalize(name)
            if not variant_n or not name_n:
                return False
            # Short tokens must match whole words (e.g. "sal" should not match "ensalada").
            if len(variant_n) <= 4:
                return re.search(rf"\b{re.escape(variant_n)}\b", name_n) is not None
            return variant_n in name_n

        def _has_ingredient(ingredient: str, available: list[str]) -> bool:
            variants = _variants(ingredient)
            return any(any(_variant_in_name(variant, name) for variant in variants) for name in available)

        def _is_in_fridge(ingredient: str, fridge_items: list[str]) -> bool:
            variants = _variants(ingredient)
            return any(any(_variant_in_name(variant, name) for variant in variants) for name in fridge_items)

        def _is_allowed_location(raw_location: str | None) -> bool:
            loc = ChatService._normalize(raw_location or "")
            return any(tag in loc for tag in ("refrigerador", "refri", "nevera", "cocina"))

        def _suggest_condiments_for_name(recipe_name: str) -> tuple[str, ...]:
            name_n = ChatService._normalize(recipe_name)
            suggested = list(base_condiments)

            if any(token in name_n for token in ("arroz con pollo", "saltado", "salteado", "chaufa")):
                suggested.extend(["sillao", "ajinomoto"])
            if any(token in name_n for token in ("guiso", "olla", "lenteja", "garbanzo", "sopa", "sudado")):
                suggested.extend(["laurel", "hongos"])
            if any(token in name_n for token in ("sandwich", "sanguche", "ensalada", "plancha")):
                suggested.extend(["mayonesa", "ketchup"])

            # Keep order without duplicates.
            ordered: list[str] = []
            seen = set()
            for item in suggested:
                key = ChatService._normalize(item)
                if key in seen:
                    continue
                seen.add(key)
                ordered.append(item)
            return tuple(ordered)

        def _is_missing_list_request(user_text_n: str) -> bool:
            return bool(
                re.search(
                    r"\b(?:que\s+me\s+falta|qué\s+me\s+falta|que\s+falta|qué\s+falta|faltan|lista|listado|comprar)\b",
                    user_text_n,
                )
            )

        def _find_requested_recipe(user_text_n: str, recipes: list[dict]) -> dict | None:
            stopwords = {"con", "de", "y", "la", "el", "al", "casero", "saludable"}
            best: dict | None = None
            best_score = 0
            for recipe in recipes:
                name_n = ChatService._normalize(str(recipe.get("name", "")))
                if not name_n:
                    continue

                score = 0
                if name_n in user_text_n or user_text_n in name_n:
                    score += 6

                core_words = [w for w in name_n.split() if len(w) > 2 and w not in stopwords]
                overlap = sum(1 for word in core_words if re.search(rf"\b{re.escape(word)}\b", user_text_n))
                score += overlap

                if score > best_score:
                    best = recipe
                    best_score = score

            return best if best_score >= 3 else None

        def _estimate_prep_minutes(recipe_data: dict) -> int:
            explicit = recipe_data.get("prep_minutes")
            if isinstance(explicit, int) and explicit > 0:
                return explicit
            name_n = ChatService._normalize(str(recipe_data.get("name", "")))
            if any(token in name_n for token in ("sopa", "guiso", "olla", "sudado", "lentejas", "garbanzo")):
                return 35
            if any(token in name_n for token in ("salteado", "saltado", "chaufa", "omelette", "tortilla", "sandwich", "sanguche", "wrap")):
                return 18
            if any(token in name_n for token in ("jugo", "limonada", "bebida", "yogurt", "avena")):
                return 10
            return 25

        def _build_template_recipes() -> list[dict]:
            protein_options = ["pollo", "huevo", "atun", "atún", "pescado", "lenteja", "garbanzo", "garganzo"]
            carb_options = ["arroz", "quinoa", "pasta", "fideo", "avena", "quaker", "papa"]
            veg_options = ["zanahoria", "tomate", "cebolla", "pepino", "lechuga", "vainita", "brocoli", "pimiento", "palta"]

            def _peruvian_template_name(protein: str, carb: str, has_veggies: bool) -> str:
                p = ChatService._format_ingredient_name(protein)
                c = ChatService._format_ingredient_name(carb)
                n_protein = ChatService._normalize(protein)
                n_carb = ChatService._normalize(carb)

                if n_protein in ("lenteja", "garbanzo", "garganzo") and n_carb == "arroz":
                    return f"Guiso de {p} con arroz"
                if n_protein == "pescado" and n_carb == "arroz":
                    return "Sudado de pescado con arroz"
                if n_protein == "pollo" and n_carb == "arroz":
                    return "Arroz con pollo casero"
                if n_protein == "huevo" and n_carb == "arroz":
                    return "Chaufa saludable de huevo"
                if n_carb in ("arroz", "quinoa"):
                    suffix = " y verduras" if has_veggies else ""
                    return f"Saltado de {p} con {c}{suffix}"
                if n_carb in ("fideo", "pasta"):
                    return f"Salteado de {p} con {c}"
                return f"Guiso casero de {p} con {c}"

            proteins = [item for item in protein_options if _has_ingredient(item, available_names)]
            carbs = [item for item in carb_options if _has_ingredient(item, available_names)]
            veggies = [item for item in veg_options if _has_ingredient(item, available_names)]

            templates: list[dict] = []
            generated_names: set[str] = set()

            for protein in proteins:
                for carb in carbs:
                    veg_slice = veggies[:3] if veggies else []
                    name = _peruvian_template_name(protein, carb, bool(veg_slice))
                    norm_name = ChatService._normalize(name)
                    if norm_name in generated_names:
                        continue
                    generated_names.add(norm_name)

                    optional = tuple(veg_slice) + ("ajo", "limon")
                    templates.append(
                        {
                            "name": name,
                            "meal": ("almuerzo", "cena"),
                            "healthy": True,
                            "required": (protein, carb),
                            "optional": optional,
                            "steps": (
                                f"Cocina {ChatService._format_ingredient_name(protein)} y {ChatService._format_ingredient_name(carb)} por separado.",
                                "Saltea las verduras con poco aceite y sal.",
                                "Mezcla todo y ajusta sabor con limon o especias.",
                            ),
                        }
                    )

            # Breakfast templates: cereal/harina + beverage
            cereal_keys = ["avena", "quaker", "7 semillas"]
            cereals = [item for item in cereal_keys if _has_ingredient(item, available_names)]
            for cereal in cereals:
                name = f"Desayuno de {ChatService._format_ingredient_name(cereal)} con bebida"
                norm_name = ChatService._normalize(name)
                if norm_name in generated_names:
                    continue
                generated_names.add(norm_name)
                templates.append(
                    {
                        "name": name,
                        "meal": ("desayuno",),
                        "healthy": True,
                        "required": (cereal,),
                        "optional": ("leche", "agua", "papaya", "banana", "platano", "limon"),
                        "steps": (
                            f"Prepara {ChatService._format_ingredient_name(cereal)} con agua o leche.",
                            "Acompana con fruta o una bebida ligera.",
                            "Sirve en porcion para 1 persona.",
                        ),
                    }
                )

            return templates

        products = ProductService.list_products(db)
        available_products = [
            product
            for product in products
            if product.stock_current > 0 and _is_allowed_location(product.location)
        ]

        in_fridge = [
            ChatService._normalize(product.name)
            for product in available_products
            if any(tag in ChatService._normalize(product.location or "") for tag in ("refrigerador", "refri", "nevera"))
        ]

        available_all = [
            ChatService._normalize(product.name)
            for product in available_products
        ]

        available_names = available_all

        if not available_names:
            return (
                "No encontré ingredientes con stock ubicados en cocina o refrigerador. "
                "Si actualizas ubicación de productos, te sugiero recetas al toque."
            )

        requested_meals = _requested_meals(text_n)
        breakfast_only = len(requested_meals) == 1 and requested_meals[0] == "desayuno"
        lunch_only = len(requested_meals) == 1 and requested_meals[0] == "almuerzo"
        dinner_only = len(requested_meals) == 1 and requested_meals[0] == "cena"
        healthy_only = _is_healthy_requested(text_n)
        time_pref = _requested_time_preference(text_n)
        budget_pref = _requested_budget_preference(text_n)
        weekly_used_names = ChatService._weekly_used_recipe_names(db)
        weekly_used_norm = {ChatService._normalize(name) for name in weekly_used_names}
        explicit_repeat = ChatService._is_explicit_repeat_request(text_n, weekly_used_names)
        weekday_ref = ChatService._extract_weekday_reference(text_n)
        weekday_recipe_entry = ChatService._weekly_recipe_for_weekday(db, weekday_ref) if weekday_ref else None

        def _has_any(keys: tuple[str, ...]) -> bool:
            return any(_has_ingredient(key, available_names) for key in keys)

        dynamic_recipes: list[dict] = []
        dynamic_names: set[str] = set()

        def _add_dynamic_recipe(recipe: dict) -> None:
            name = ChatService._normalize(str(recipe.get("name", "")))
            if not name or name in dynamic_names:
                return
            dynamic_names.add(name)
            dynamic_recipes.append(recipe)

        if _has_any(("7 semillas",)):
            _add_dynamic_recipe(
                {
                    "name": "Bebida de 7 semillas",
                    "meal": ("desayuno", "almuerzo", "cena"),
                    "healthy": True,
                    "required": ("7 semillas",),
                    "optional": ("agua", "canela", "miel"),
                    "steps": (
                        "Hierve una taza de agua.",
                        "Disuelve 2 cucharadas de 7 semillas en polvo.",
                        "Remueve bien y sirve tibio.",
                    ),
                }
            )

        # ── Desayunos dinámicos según inventario ─────────────────────────────
        if _has_any(("fresa",)) and _has_any(("leche",)):
            _add_dynamic_recipe(
                {
                    "name": "Batido de fresa con leche",
                    "meal": ("desayuno", "almuerzo", "cena"),
                    "healthy": True,
                    "required": ("fresa", "leche"),
                    "optional": ("miel", "avena"),
                    "steps": (
                        "Lava y desinfecta 1 taza de fresas.",
                        "Licua con 1 taza de leche fria por 30 segundos.",
                        "Endulza ligeramente si deseas y sirve al momento.",
                    ),
                }
            )

        if _has_any(("fresa",)):
            _add_dynamic_recipe(
                {
                    "name": "Jugo de fresa",
                    "meal": ("desayuno", "almuerzo", "cena"),
                    "healthy": True,
                    "required": ("fresa",),
                    "optional": ("agua", "miel", "limon"),
                    "steps": (
                        "Lava una taza de fresa y retira tallos.",
                        "Licua con agua fria hasta textura uniforme.",
                        "Ajusta dulzor y sirve de inmediato.",
                    ),
                }
            )

        if _has_any(("papaya",)):
            _add_dynamic_recipe(
                {
                    "name": "Jugo de papaya",
                    "meal": ("desayuno", "almuerzo", "cena"),
                    "healthy": True,
                    "required": ("papaya",),
                    "optional": ("leche", "agua", "miel"),
                    "steps": (
                        "Pela y corta 1 taza de papaya en cubos.",
                        "Licua con agua o leche fria hasta lograr textura suave.",
                        "Sirve inmediatamente para conservar sabor y nutrientes.",
                    ),
                }
            )

        fruit_candidates = ["fresa", "papaya", "platano", "banana", "manzana"]
        available_fruits = [fruit for fruit in fruit_candidates if _has_any((fruit,))]
        if len(available_fruits) >= 2:
            required_fruits = tuple(available_fruits[:2])
            optional_fruits = tuple(available_fruits[2:])
            _add_dynamic_recipe(
                {
                    "name": "Ensalada de frutas casera",
                    "meal": ("desayuno", "cena"),
                    "healthy": True,
                    "required": required_fruits,
                    "optional": optional_fruits + ("yogurt", "miel"),
                    "steps": (
                        "Pica la fruta disponible en cubos pequeños.",
                        "Mezcla en un bowl y agrega yogurt o un toque de miel si tienes.",
                        "Consume fresca para mejor textura.",
                    ),
                }
            )

        if _has_any(("pan",)) and _has_any(("mantequilla",)):
            _add_dynamic_recipe(
                {
                    "name": "Tostadas con mantequilla",
                    "meal": ("desayuno",),
                    "healthy": False,
                    "required": ("pan", "mantequilla"),
                    "optional": ("mermelada",),
                    "steps": (
                        "Tuesta 2 rebanadas de pan hasta dorar.",
                        "Unta mantequilla mientras el pan está caliente.",
                        "Sirve con bebida caliente o jugo.",
                    ),
                }
            )

        if _has_any(("pan",)) and _has_any(("mermelada",)):
            _add_dynamic_recipe(
                {
                    "name": "Pan con mermelada",
                    "meal": ("desayuno", "cena"),
                    "healthy": False,
                    "required": ("pan", "mermelada"),
                    "optional": ("mantequilla", "queso"),
                    "steps": (
                        "Tuesta ligeramente el pan si prefieres textura crocante.",
                        "Unta una capa delgada de mantequilla (opcional).",
                        "Agrega mermelada al gusto y sirve.",
                    ),
                }
            )

        if _has_any(("pan",)) and _has_any(("huevo",)):
            _add_dynamic_recipe(
                {
                    "name": "Sanguche de huevo",
                    "meal": ("desayuno", "cena"),
                    "healthy": True,
                    "required": ("pan", "huevo"),
                    "optional": ("mayonesa", "tomate", "lechuga"),
                    "steps": (
                        "Cocina 1 o 2 huevos (hervidos o revueltos).",
                        "Arma el sanguche con pan y añade tomate o lechuga si tienes.",
                        "Usa poca mayonesa para equilibrar el sabor.",
                    ),
                }
            )

        if _has_any(("pan",)) and _has_any(("queso",)):
            _add_dynamic_recipe(
                {
                    "name": "Sanguche de queso tostado",
                    "meal": ("desayuno", "cena"),
                    "healthy": False,
                    "required": ("pan", "queso"),
                    "optional": ("mantequilla", "ketchup"),
                    "steps": (
                        "Coloca queso entre dos panes.",
                        "Tuesta en sartén con una pequeña capa de mantequilla hasta derretir el queso.",
                        "Sirve caliente; agrega ketchup solo si deseas.",
                    ),
                }
            )

        if _has_any(("limon", "limón")):
            _add_dynamic_recipe(
                {
                    "name": "Limonada casera",
                    "meal": ("desayuno", "almuerzo", "cena"),
                    "healthy": True,
                    "required": ("limon",),
                    "optional": ("agua", "miel", "azucar"),
                    "steps": (
                        "Exprime 1 o 2 limones.",
                        "Mezcla con agua fria y endulza a gusto.",
                        "Sirve al momento.",
                    ),
                }
            )

        if _has_any(("naranja",)):
            _add_dynamic_recipe(
                {
                    "name": "Jugo de naranja",
                    "meal": ("desayuno", "almuerzo", "cena"),
                    "healthy": True,
                    "required": ("naranja",),
                    "optional": ("agua", "miel"),
                    "steps": (
                        "Exprime 2 o 3 naranjas frescas.",
                        "Mezcla con un poco de agua fria si deseas bajar acidez.",
                        "Sirve al instante para mantener vitaminas.",
                    ),
                }
            )

        if _has_any(("manzana",)):
            _add_dynamic_recipe(
                {
                    "name": "Jugo de manzana",
                    "meal": ("desayuno", "almuerzo", "cena"),
                    "healthy": True,
                    "required": ("manzana",),
                    "optional": ("agua", "limon", "miel"),
                    "steps": (
                        "Lava y trocea 1 manzana retirando semillas.",
                        "Licua con agua fria hasta obtener textura ligera.",
                        "Cuela si prefieres y sirve frio.",
                    ),
                }
            )

        if _has_any(("gaseosa", "cola", "kola", "soda", "pepsi", "coca", "inca kola", "fanta", "sprite")):
            _add_dynamic_recipe(
                {
                    "name": "Vaso de gaseosa fria",
                    "meal": ("desayuno", "almuerzo", "cena"),
                    "healthy": False,
                    "required": ("gaseosa",),
                    "optional": ("hielo", "limon"),
                    "steps": (
                        "Enfria la gaseosa antes de servir.",
                        "Sirve en vaso con hielo si tienes.",
                        "Acompana con el plato elegido.",
                    ),
                }
            )

        if _has_any(("pollo",)):
            _add_dynamic_recipe(
                {
                    "name": "Pollo a la olla",
                    "meal": ("almuerzo", "cena"),
                    "healthy": True,
                    "required": ("pollo",),
                    "optional": ("papa", "zanahoria", "cebolla", "ajo", "arroz"),
                    "steps": (
                        "Sella el pollo y agrega agua hasta cubrir.",
                        "Incorpora verduras y cocina hasta que todo este suave.",
                        "Sirve solo o acompanado con arroz.",
                    ),
                }
            )

        if _has_any(("arroz",)) and _has_any(("lenteja",)):
            _add_dynamic_recipe(
                {
                    "name": "Lentejas con arroz",
                    "meal": ("almuerzo", "cena"),
                    "healthy": True,
                    "required": ("lenteja", "arroz"),
                    "optional": ("cebolla", "ajo", "tomate", "zanahoria"),
                    "steps": (
                        "Cocina las lentejas con un aderezo basico.",
                        "Prepara arroz blanco aparte.",
                        "Sirve las lentejas junto al arroz.",
                    ),
                }
            )

        if _has_any(("arroz",)) and _has_any(("garbanzo", "garganzo")):
            _add_dynamic_recipe(
                {
                    "name": "Garbanzo con arroz",
                    "meal": ("almuerzo", "cena"),
                    "healthy": True,
                    "required": ("garbanzo", "arroz"),
                    "optional": ("cebolla", "ajo", "tomate", "comino"),
                    "steps": (
                        "Cocina garbanzos hasta que esten tiernos.",
                        "Prepara un aderezo y mezcla con los garbanzos.",
                        "Acompana con arroz cocido.",
                    ),
                }
            )

        candidates: list[dict] = []
        template_recipes = _build_template_recipes()
        all_recipes = list(ChatService.RECIPE_BOOK) + dynamic_recipes + template_recipes

        requested_recipe = _find_requested_recipe(text_n, all_recipes)
        if requested_recipe and _is_missing_list_request(text_n):
            required = list(requested_recipe.get("required", ()))
            optional = list(requested_recipe.get("optional", ()))
            condiments = list(_suggest_condiments_for_name(str(requested_recipe.get("name", "Receta"))))

            for condiment in condiments:
                if condiment not in optional and condiment not in required:
                    optional.append(condiment)

            missing_required = [ingredient for ingredient in required if not _has_ingredient(ingredient, available_names)]
            missing_optional = [ingredient for ingredient in optional if not _has_ingredient(ingredient, available_names)]
            matched_required = [ingredient for ingredient in required if _has_ingredient(ingredient, available_names)]

            missing_all: list[str] = []
            seen_missing = set()
            for item in missing_required + missing_optional:
                key = ChatService._normalize(item)
                if key in seen_missing:
                    continue
                seen_missing.add(key)
                missing_all.append(item)

            missing_all = _collapse_inventory_compounds(missing_all, available_names)

            lines = [f"Para preparar {requested_recipe.get('name', 'esa receta')} te comparto el listado completo:", ""]
            lines.append("Te falta comprar:")
            if missing_all:
                lines.extend(f"- {ChatService._format_ingredient_name(item)}" for item in missing_all)
            else:
                lines.append("- No te falta nada. Puedes prepararlo con lo que tienes.")

            if matched_required:
                lines.append("")
                lines.append("Ya tienes en casa (base):")
                lines.extend(f"- {ChatService._format_ingredient_name(item)}" for item in matched_required)

            shopping_text = "\n".join(lines)

            target_phone = ChatService._extract_phone_number(text_n) or ChatService._get_default_whatsapp_to(db)
            if not target_phone:
                return (
                    shopping_text
                    + "\n\nPara enviarlo por WhatsApp, dime tu número así: mi número de WhatsApp es 9XXXXXXXX."
                )

            sent, detail = WhatsAppService.send_message(shopping_text, target_phone)
            if sent:
                return shopping_text + f"\n\nListo, te envié este listado completo por WhatsApp a +{target_phone}."

            encoded = quote(shopping_text)
            return (
                shopping_text
                + "\n\nNo pude enviarlo automáticamente por ahora. "
                + f"Detalle: {detail}\n"
                + f"Enlace manual: https://wa.me/{target_phone}?text={encoded}"
            )

        for recipe in all_recipes:
            recipe_meals = recipe.get("meal", ())
            if not any(meal in recipe_meals for meal in requested_meals):
                continue
            if healthy_only and not recipe.get("healthy"):
                continue

            required = list(recipe.get("required", ()))
            optional = list(recipe.get("optional", ()))
            condiments = list(_suggest_condiments_for_name(str(recipe.get("name", "Receta"))))
            for condiment in condiments:
                if condiment not in optional and condiment not in required:
                    optional.append(condiment)

            matched_required = [ingredient for ingredient in required if _has_ingredient(ingredient, available_names)]
            missing_required = [ingredient for ingredient in required if ingredient not in matched_required]
            matched_optional = [ingredient for ingredient in optional if _has_ingredient(ingredient, available_names)]
            missing_optional = [ingredient for ingredient in optional if ingredient not in matched_optional]
            condiments_available = [ingredient for ingredient in condiments if _has_ingredient(ingredient, available_names)]
            condiments_missing = [ingredient for ingredient in condiments if ingredient not in condiments_available]

            matched_required = _collapse_inventory_compounds(matched_required, available_names)
            matched_optional = _collapse_inventory_compounds(matched_optional, available_names)
            missing_required = _collapse_inventory_compounds(missing_required, available_names)
            missing_optional = _collapse_inventory_compounds(missing_optional, available_names)
            condiments_available = _collapse_inventory_compounds(condiments_available, available_names)
            condiments_missing = _collapse_inventory_compounds(condiments_missing, available_names)

            fridge_hits = sum(1 for ingredient in (matched_required + matched_optional) if _is_in_fridge(ingredient, in_fridge))

            missing_total = len(missing_required) + len(missing_optional)
            is_complete = len(missing_required) == 0
            near_complete = len(missing_required) <= 1 and missing_total <= 3

            if not is_complete and not near_complete:
                continue

            score = (
                (50 if is_complete else 0)
                + (len(matched_required) * 12)
                + (len(matched_optional) * 3)
                + (fridge_hits * 2)
                - (missing_total * 2)
            )

            prep_minutes = _estimate_prep_minutes(recipe)

            if dinner_only:
                recipe_name_n = ChatService._normalize(str(recipe.get("name", "")))
                is_heavy_name = any(
                    token in recipe_name_n
                    for token in ("guiso", "olla", "arroz con pollo", "lentejas", "garbanzo", "chaufa", "saltado", "salteado")
                )
                is_light = (recipe.get("healthy") and prep_minutes <= 20) or any(
                    token in recipe_name_n for token in ("ensalada", "yogurt", "tortilla", "omelette", "jugo", "batido", "sopa ligera")
                )
                if is_heavy_name or not is_light:
                    continue

            if time_pref == "rapido":
                score += max(0, 40 - prep_minutes)
            elif time_pref == "elaborado":
                score += prep_minutes // 2
            else:
                score += max(0, 30 - prep_minutes) // 2

            def _ingredient_cost(ingredient: str) -> float | None:
                variants = _variants(ingredient)
                candidate = None
                for product in available_products:
                    product_name_n = ChatService._normalize(product.name)
                    if any(variant in product_name_n for variant in variants):
                        candidate = product
                        break
                if not candidate:
                    return None
                current_price = ChatService._get_current_price_for_product(db, candidate)
                if current_price is not None:
                    return float(current_price)
                latest = PurchaseService.get_latest_purchase_for_product(db, candidate)
                if latest and latest.unit_price is not None:
                    return float(latest.unit_price)
                return None

            cost_parts = [c for c in (_ingredient_cost(item) for item in matched_required[:3]) if c is not None]
            cost_estimate = round(sum(cost_parts), 2) if cost_parts else None

            if cost_estimate is not None:
                score += max(0, int(25 - cost_estimate))
                if budget_pref:
                    score += max(0, int(30 - cost_estimate))

            beverage_hint = None
            if "desayuno" in recipe_meals:
                beverage_hint = ChatService._build_breakfast_beverage_suggestion(available_all)

            matched_all = matched_required + matched_optional
            missing_in_fridge = [ingredient for ingredient in matched_all if not _is_in_fridge(ingredient, in_fridge)]

            candidates.append(
                {
                    "name": recipe["name"],
                    "meal": ", ".join(recipe_meals),
                    "is_complete": is_complete,
                    "score": score,
                    "matched": matched_required + matched_optional,
                    # Show only required missing items as real purchase needs.
                    # Optional ingredients are useful suggestions but should not
                    # appear as blockers in "Te faltaria comprar".
                    "missing": missing_required,
                    "optional_missing": missing_optional,
                    "missing_in_fridge": missing_in_fridge[:3],
                    "steps": list(recipe.get("steps", ())),
                    "beverage_hint": beverage_hint,
                    "prep_minutes": prep_minutes,
                    "cost_estimate": cost_estimate,
                    "condiments_available": condiments_available,
                    "condiments_missing": condiments_missing,
                }
            )

        # ── Load last-shown names for variety tracking ───────────────────────
        try:
            _last_item = MemoryService.get_by_key(db, "__last_recipes__")
            last_shown: list[str] = _last_item.value.split("|") if _last_item and _last_item.value else []
        except Exception:
            last_shown = []

        # ── Try Gemini API first for richer, varied recipes ───────────────────
        gemini_options = ChatService._call_gemini_for_recipes(
            available_ingredients=available_names,
            meal_hints=requested_meals,
            healthy=healthy_only,
            last_shown=last_shown,
        )

        if gemini_options and weekly_used_norm and not explicit_repeat:
            gemini_options = [
                item for item in gemini_options
                if ChatService._normalize(item.get("name", "")) not in weekly_used_norm
            ]

        if gemini_options:
            if breakfast_only:
                options = _build_breakfast_combo_options(gemini_options, 4)
            elif lunch_only:
                options = _build_lunch_combo_options(gemini_options, 4)
            else:
                options = gemini_options[:4]
            source_note = " (con IA)"
        else:
            if weekly_used_norm and not explicit_repeat:
                candidates = [
                    item for item in candidates
                    if ChatService._normalize(item.get("name", "")) not in weekly_used_norm
                ]

            # Local engine: prefer complete recipes, shuffle within tier for variety
            candidates.sort(key=lambda item: (item["is_complete"], item["score"]), reverse=True)
            complete = [c for c in candidates if c["is_complete"]]
            incomplete = [c for c in candidates if not c["is_complete"]]

            last_shown_set = set(last_shown)
            fresh_complete = [c for c in complete if c["name"] not in last_shown_set]
            stale_complete = [c for c in complete if c["name"] in last_shown_set]

            random.shuffle(fresh_complete)
            random.shuffle(stale_complete)
            random.shuffle(incomplete)

            # Fresh complete first, then stale complete, incomplete only as last resort
            ranked_pool = fresh_complete + stale_complete + incomplete
            if breakfast_only:
                options = _build_breakfast_combo_options(ranked_pool, 4)
            elif lunch_only:
                options = _build_lunch_combo_options(ranked_pool, 4)
            else:
                options = ranked_pool[:4]
            source_note = ""

        if not options:
            if breakfast_only:
                return (
                    "Para desayuno en modo combo necesito al menos una bebida y un acompañamiento "
                    "(pan/sanguche o fruta/ensalada) con stock en cocina o refri. "
                    "Actualiza stock y te armo combos completos de 2 elementos."
                )
            if lunch_only:
                return (
                    "Para almuerzo en modo combo necesito al menos un plato principal y una bebida/acompañamiento "
                    "con stock en cocina o refri. Actualiza stock y te armo combos completos de 2 elementos."
                )
            if dinner_only:
                return (
                    "Para cena te estoy proponiendo solo opciones ligeras. "
                    "Si quieres más variedad, agrega ingredientes livianos (frutas, yogurt, verduras, pan, huevo)."
                )
            healthy_note = " saludables" if healthy_only else ""
            repeat_note = (
                " Esta semana evité repetir las que ya preparaste. Si quieres repetir una puntual, "
                "pídela por nombre o por día (ej: la del lunes)."
                if weekly_used_norm and not explicit_repeat
                else ""
            )
            return (
                f"No encontré recetas completas{healthy_note} con lo que tienes en cocina y refri. "
                "Agrega más ingredientes y te doy nuevas opciones."
                + repeat_note
            )

        _PENDING.clear()
        _PENDING.update({"action": "recipe_choose", "options": options})

        # Track shown recipe names for variety on next request
        try:
            shown_names = [item["name"] for item in options]
            new_last = shown_names + [n for n in last_shown if n not in shown_names]
            MemoryService.save_item(
                db, MemoryCreate(key="__last_recipes__", value="|".join(new_last[:12]))
            )
        except Exception:
            pass

        lines = []
        if breakfast_only:
            grouped: dict[str, list[tuple[int, dict]]] = {
                "Combos": [],
                "Bebidas": [],
                "Panes/Sanguches": [],
                "Frutas": [],
                "Otras opciones": [],
            }

            for idx, item in enumerate(options, start=1):
                label = _breakfast_block_label(str(item.get("name", "")))
                grouped[label].append((idx, item))

            ordered_blocks = ("Combos", "Bebidas", "Panes/Sanguches", "Frutas", "Otras opciones")
            for block in ordered_blocks:
                block_items = grouped[block]
                if not block_items:
                    continue
                lines.append(f"{block}:")
                for idx, item in block_items:
                    notes: list[str] = []
                    if item.get("missing"):
                        missing_text = ", ".join(item["missing"])
                        notes.append(f"te faltaria: {missing_text}")
                    else:
                        notes.append("completa con lo que tienes")

                    if item.get("prep_minutes") is not None:
                        notes.append(f"{item['prep_minutes']} min")
                    if item.get("cost_estimate") is not None:
                        notes.append(f"S/ {ChatService._fmt_num(item['cost_estimate'])}")

                    suffix = " | ".join(notes)
                    lines.append(f"Receta {idx}: {item['name']} ({item['meal']}) - {suffix}")
                lines.append("")

            if lines and lines[-1] == "":
                lines.pop()
        else:
            for idx, item in enumerate(options, start=1):
                notes: list[str] = []
                if item.get("missing"):
                    missing_text = ", ".join(item["missing"])
                    notes.append(f"te faltaria: {missing_text}")
                else:
                    notes.append("completa con lo que tienes")

                if item.get("prep_minutes") is not None:
                    notes.append(f"{item['prep_minutes']} min")
                if item.get("cost_estimate") is not None:
                    notes.append(f"S/ {ChatService._fmt_num(item['cost_estimate'])}")

                suffix = " | ".join(notes)
                lines.append(f"Receta {idx}: {item['name']} ({item['meal']}) - {suffix}")

        healthy_header = " saludables" if healthy_only else ""
        weekly_note = (
            " sin repetir las que ya preparaste esta semana"
            if weekly_used_norm and not explicit_repeat
            else ""
        )
        recall_prefix = ""
        if weekday_recipe_entry:
            day_text = str(weekday_recipe_entry.get("weekday", weekday_ref or "")).strip()
            recipe_text = str(weekday_recipe_entry.get("name", "")).strip()
            if day_text and recipe_text:
                recall_prefix = f"El {day_text} preparaste {recipe_text}.\n"
        return (
            recall_prefix
            + f"Te propongo estas recetas{healthy_header} con lo que tienes en cocina y refri{source_note}{weekly_note}, priorizando las completas:\n"
            + "\n".join(lines)
            + "\n\nDime cuál deseas: receta 1, receta 2, receta 3 o receta 4."
        )

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

        # 0. Pending confirmation takes priority
        pending_reply = ChatService._try_pending_confirmation(db, text_i)
        if pending_reply:
            return pending_reply

        # 1. WhatsApp destination preference
        whatsapp_number_reply = ChatService._try_set_whatsapp_number(db, text_i)
        if whatsapp_number_reply:
            return whatsapp_number_reply

        # 2. Locale preference
        locale_reply = ChatService._maybe_update_locale(db, text_i)
        if locale_reply:
            return locale_reply

        # 3. Conversational tone preference
        tone_reply = ChatService._maybe_update_tone(db, text_i)
        if tone_reply:
            return tone_reply

        # 3.5 Recipes (early routing so greetings do not swallow recipe requests)
        if ChatService._is_recipe_request(text_i):
            return ChatService._build_recipes_reply(db, text_i)

        # 4. Social / conversational
        social_reply = ChatService._try_social_reply(db, text_i)
        if social_reply:
            return social_reply

        # 4.1 LG ThinQ devices/status
        lgthinq_reply = ChatService._try_lgthinq_status(db, text_i)
        if lgthinq_reply:
            return lgthinq_reply

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

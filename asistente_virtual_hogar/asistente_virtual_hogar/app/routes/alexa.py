from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.services.chat_service import ChatService

router = APIRouter(prefix="/alexa", tags=["Alexa"])


def _command_from_intent_name(intent_name: str) -> str:
    normalized = (intent_name or "").strip().lower()
    if not normalized:
        return ""

    direct_map = {
        "inventariointent": "inventario",
        "comprasintent": "lista de compras",
        "resumencomprasintent": "resumen de compras",
        "consumointent": "alerta de consumo",
        "alertasintent": "alerta de consumo",
        "estadolavadoraintent": "estado de mi lavadora lg",
        "iniciarciclodelicadointent": "inicia ciclo delicado en la lavadora lg",
        "iniciarciclointent": "inicia ciclo en la lavadora lg",
    }
    if normalized in direct_map:
        return direct_map[normalized]

    # If user creates custom intent names, infer a useful command from keywords.
    if "inventario" in normalized:
        return "inventario"
    if "compra" in normalized:
        return "lista de compras"
    if "consumo" in normalized or "alerta" in normalized:
        return "alerta de consumo"
    if "lavadora" in normalized and "estado" in normalized:
        return "estado de mi lavadora lg"
    if "lavadora" in normalized and "ciclo" in normalized and "delicado" in normalized:
        return "inicia ciclo delicado en la lavadora lg"
    if "lavadora" in normalized and "ciclo" in normalized:
        return "inicia ciclo en la lavadora lg"
    return ""


def _extract_command_text(payload: dict) -> str:
    request_data = payload.get("request") or {}
    intent_data = request_data.get("intent") or {}
    slots = intent_data.get("slots") or {}

    preferred_keys = (
        "message",
        "query",
        "command",
        "texto",
        "accion",
        "action",
    )

    for key in preferred_keys:
        slot = slots.get(key)
        if isinstance(slot, dict):
            value = (slot.get("value") or "").strip()
            if value:
                return value

    for slot in slots.values():
        if isinstance(slot, dict):
            value = (slot.get("value") or "").strip()
            if value:
                return value

    return ""


def _alexa_response(text: str, should_end_session: bool = False) -> dict:
    return {
        "version": "1.0",
        "response": {
            "outputSpeech": {
                "type": "PlainText",
                "text": " ".join((text or "").split()),
            },
            "shouldEndSession": should_end_session,
        },
    }


@router.post("/webhook")
async def alexa_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    request_data = payload.get("request") or {}
    request_type = (request_data.get("type") or "").strip()

    if request_type == "LaunchRequest":
        return _alexa_response(
            "Asistente Virtual de Hogar conectado. Dime un comando, por ejemplo: estado de mi lavadora LG."
        )

    if request_type == "SessionEndedRequest":
        return {"version": "1.0", "response": {"shouldEndSession": True}}

    if request_type != "IntentRequest":
        return _alexa_response("No pude procesar esa solicitud de Alexa.")

    intent_data = request_data.get("intent") or {}
    intent_name = (intent_data.get("name") or "").strip()

    if intent_name == "AMAZON.HelpIntent":
        return _alexa_response(
            "Puedes pedirme compras, inventario o lavadora LG. Ejemplo: inicia ciclo delicado en la lavadora LG."
        )

    if intent_name in {"AMAZON.StopIntent", "AMAZON.CancelIntent"}:
        return _alexa_response("Listo. Hasta luego.", should_end_session=True)

    if intent_name == "AMAZON.FallbackIntent":
        return _alexa_response("No entendí esa orden. Intenta decirla de otra manera.")

    command_text = _extract_command_text(payload)
    if not command_text:
        command_text = _command_from_intent_name(intent_name)

    if not command_text:
        return _alexa_response(
            "No detecte el comando. Puedes decir: quiero inventario, necesito ver compras o dime el estado de mi lavadora LG."
        )

    try:
        reply_text = ChatService.reply(db, command_text)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        reply_text = "Tuve un problema interno procesando tu solicitud. Intenta de nuevo en unos segundos."

    return _alexa_response(reply_text)

import json
import os
from datetime import datetime
import urllib.parse
import urllib.request

from sqlalchemy.orm import Session

from app.models.schemas import MemoryCreate
from app.services.memory_service import MemoryService
from app.services.whatsapp_service import WhatsAppService


class LGThinQService:
    """Simple client for LG ThinQ API using Personal Access Token (PAT)."""

    ALERT_CONFIG_KEY = "__lgthinq_alert_config__"
    LAST_EVENT_KEY = "__lgthinq_last_event__"
    LAST_STATUS_PREFIX = "__lgthinq_last_status__::"

    @staticmethod
    def _base_url() -> str:
        return (os.getenv("LGTHINQ_API_BASE_URL") or "").strip().rstrip("/")

    @staticmethod
    def _api_pat() -> str:
        return (os.getenv("LGTHINQ_API_PAT") or "").strip()

    @staticmethod
    def can_query(db: Session | None = None) -> bool:
        return bool(LGThinQService._base_url() and LGThinQService._api_pat())

    @staticmethod
    def setup_instructions() -> str:
        return (
            "Para integrar LG ThinQ configura en Render: "
            "LGTHINQ_API_BASE_URL (ej: https://iot.lgeapi.com) y "
            "LGTHINQ_API_PAT (tu Personal Access Token). "
            "Opcional: LGTHINQ_DEFAULT_DEVICE_ID y LGTHINQ_WEBHOOK_SECRET."
        )

    @staticmethod
    def _load_json_memory(db: Session, key: str) -> dict:
        item = MemoryService.get_by_key(db, key)
        if not item or not item.value.strip():
            return {}
        try:
            parsed = json.loads(item.value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _status_memory_key(device_id: str) -> str:
        return f"{LGThinQService.LAST_STATUS_PREFIX}{device_id.strip()}"

    @staticmethod
    def _now_timestamp() -> int:
        import time
        return int(time.time())

    @staticmethod
    def _iso_now() -> str:
        return datetime.utcnow().isoformat(timespec="seconds")

    @staticmethod
    def alert_config(db: Session) -> dict:
        config = LGThinQService._load_json_memory(db, LGThinQService.ALERT_CONFIG_KEY)
        if not config:
            return {"enabled": False, "device_hint": "lavadora", "notify_whatsapp": True}
        config.setdefault("enabled", False)
        config.setdefault("device_hint", "lavadora")
        config.setdefault("notify_whatsapp", True)
        return config

    @staticmethod
    def save_alert_config(db: Session, enabled: bool, device_hint: str | None = None, notify_whatsapp: bool = True) -> dict:
        current = LGThinQService.alert_config(db)
        current["enabled"] = bool(enabled)
        if device_hint:
            current["device_hint"] = str(device_hint).strip().lower()
        current["notify_whatsapp"] = bool(notify_whatsapp)
        current["updated_at"] = LGThinQService._iso_now()
        LGThinQService._save_json_memory(db, LGThinQService.ALERT_CONFIG_KEY, current)
        return current

    @staticmethod
    def last_event(db: Session) -> dict:
        return LGThinQService._load_json_memory(db, LGThinQService.LAST_EVENT_KEY)

    @staticmethod
    def _store_last_event(db: Session, payload: dict) -> None:
        LGThinQService._save_json_memory(db, LGThinQService.LAST_EVENT_KEY, payload)

    @staticmethod
    def _load_last_status(db: Session, device_id: str) -> dict:
        return LGThinQService._load_json_memory(db, LGThinQService._status_memory_key(device_id))

    @staticmethod
    def _store_last_status(db: Session, device_id: str, payload: dict) -> None:
        LGThinQService._save_json_memory(db, LGThinQService._status_memory_key(device_id), payload)

    @staticmethod
    def _webhook_secret() -> str:
        return (os.getenv("LGTHINQ_WEBHOOK_SECRET") or "").strip()

    @staticmethod
    def _status_value(status: dict, *keys: str) -> str:
        for key in keys:
            value = status.get(key)
            if value not in (None, "", [], {}):
                return str(value).strip()
        return ""

    @staticmethod
    def _status_snapshot(status: dict) -> dict:
        return {
            "state": LGThinQService._status_value(status, "runState", "state", "operation", "processState"),
            "remaining": LGThinQService._status_value(status, "remainingTime", "remainTimeMinute"),
            "course": LGThinQService._status_value(status, "course"),
            "error": LGThinQService._status_value(status, "error", "errorCode"),
        }

    @staticmethod
    def _looks_active(snapshot: dict) -> bool:
        text = " ".join(str(snapshot.get(key, "")) for key in ("state", "remaining", "course")).lower()
        if any(token in text for token in ("run", "washing", "rinse", "spin", "dry", "progress", "active", "on")):
            return True
        remaining = str(snapshot.get("remaining") or "").strip().lower()
        return bool(remaining and remaining not in ("0", "0:00", "00:00", "0 min", "0 minutes"))

    @staticmethod
    def _looks_finished(snapshot: dict) -> bool:
        text = " ".join(str(snapshot.get(key, "")) for key in ("state", "remaining")).lower()
        if any(token in text for token in ("complete", "completed", "finish", "finished", "end", "ended", "idle", "off")):
            return True
        remaining = str(snapshot.get("remaining") or "").strip().lower()
        return remaining in ("0", "0:00", "00:00", "0 min", "0 minutes")

    @staticmethod
    def detect_event(device: dict, previous_status: dict, current_status: dict) -> dict | None:
        prev = LGThinQService._status_snapshot(previous_status)
        curr = LGThinQService._status_snapshot(current_status)
        if not prev.get("state") and not prev.get("remaining"):
            return None
        if not LGThinQService._looks_active(prev):
            return None
        if not LGThinQService._looks_finished(curr):
            return None

        device_name = str(device.get("name") or "tu equipo LG").strip()
        message = f"La {device_name} terminó su ciclo."
        if curr.get("course"):
            message += f" Programa: {curr['course']}."
        if curr.get("error"):
            message += f" Revisa este detalle: {curr['error']}."

        return {
            "type": "cycle_finished",
            "device_id": str(device.get("id") or "").strip(),
            "device_name": device_name,
            "message": message,
            "previous": prev,
            "current": curr,
            "created_at": LGThinQService._iso_now(),
        }

    @staticmethod
    def _default_whatsapp_to(db: Session) -> str | None:
        item = MemoryService.get_by_key(db, "__whatsapp_to__")
        if item and item.value.strip():
            return item.value.strip()
        env_phone = (os.getenv("DEFAULT_WHATSAPP_TO") or "").strip()
        return env_phone or None

    @staticmethod
    def dispatch_event_notification(db: Session, event: dict) -> tuple[bool, str]:
        config = LGThinQService.alert_config(db)
        if not config.get("notify_whatsapp", True):
            return False, "La alerta se registró, pero el envío por WhatsApp está desactivado."

        target_phone = LGThinQService._default_whatsapp_to(db)
        if not target_phone:
            return False, "La alerta se registró, pero no hay número de WhatsApp configurado."

        sent, detail = WhatsAppService.send_message(str(event.get("message") or "Alerta LG ThinQ"), target_phone)
        if sent:
            return True, f"Alerta enviada por WhatsApp a +{target_phone}."
        return False, detail

    @staticmethod
    def poll_for_alerts(db: Session, preferred_name: str | None = None) -> tuple[bool, dict | None, str]:
        config = LGThinQService.alert_config(db)
        device_hint = preferred_name or str(config.get("device_hint") or "").strip() or None
        ok, device, status, detail = LGThinQService.get_device_status(db=db, preferred_name=device_hint)
        if not ok or not device:
            return False, None, detail

        device_id = str(device.get("id") or "").strip()
        previous_status = LGThinQService._load_last_status(db, device_id) if device_id else {}
        event = LGThinQService.detect_event(device, previous_status, status)
        if device_id:
            LGThinQService._store_last_status(db, device_id, status)

        if event:
            event["notification"] = LGThinQService.dispatch_event_notification(db, event)[1]
            LGThinQService._store_last_event(db, event)
        return True, event, "OK"

    @staticmethod
    def process_webhook_payload(db: Session, payload: dict) -> tuple[bool, dict | None, str]:
        if not isinstance(payload, dict):
            return False, None, "El webhook de LG ThinQ debe enviar un JSON objeto."

        device_info = payload.get("device") if isinstance(payload.get("device"), dict) else payload
        status = None
        if isinstance(payload.get("status"), dict):
            status = payload.get("status")
        else:
            extracted = LGThinQService._extract_status_map(payload)
            status = extracted if extracted else payload
        if not isinstance(status, dict):
            return False, None, "El webhook no trajo un bloque de estado legible."

        device = {
            "id": str((device_info or {}).get("deviceId") or (device_info or {}).get("id") or "").strip(),
            "name": str((device_info or {}).get("alias") or (device_info or {}).get("name") or "Dispositivo LG").strip(),
            "type": str((device_info or {}).get("type") or (device_info or {}).get("deviceType") or "").strip(),
        }
        if not device["id"]:
            device["id"] = str(payload.get("deviceId") or payload.get("id") or "webhook-device").strip()

        previous_status = LGThinQService._load_last_status(db, device["id"])
        event = LGThinQService.detect_event(device, previous_status, status)
        LGThinQService._store_last_status(db, device["id"], status)
        if event:
            event["notification"] = LGThinQService.dispatch_event_notification(db, event)[1]
            LGThinQService._store_last_event(db, event)
        return True, event, "OK"

    @staticmethod
    def _request_json(path: str, db: Session | None = None) -> tuple[bool, dict | list | None, str]:
        base_url = LGThinQService._base_url()
        pat = LGThinQService._api_pat()
        if not base_url or not pat:
            return False, None, "LG ThinQ no tiene API configurada o falta el PAT."

        url = f"{base_url}{path}"
        request = urllib.request.Request(url, method="GET")
        request.add_header("Authorization", f"Bearer {pat}")
        request.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = response.read().decode("utf-8", errors="replace")
                if not payload.strip():
                    return True, {}, "OK"
                parsed = json.loads(payload)
                return True, parsed, "OK"
        except Exception as exc:
            return False, None, f"No se pudo consultar LG ThinQ: {exc}"

    @staticmethod
    def _extract_list(payload: dict | list | None) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []

        for key in ("devices", "items", "item", "data", "result", "response", "deviceList"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = LGThinQService._extract_list(value)
                if nested:
                    return nested
        return []

    @staticmethod
    def _device_id(device: dict) -> str:
        for key in ("deviceId", "device_id", "id"):
            value = device.get(key)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _device_name(device: dict) -> str:
        for key in ("alias", "name", "deviceName", "modelName"):
            value = device.get(key)
            if value:
                return str(value)
        return "Dispositivo LG"

    @staticmethod
    def _device_type(device: dict) -> str:
        for key in ("type", "deviceType", "platformType"):
            value = device.get(key)
            if value:
                return str(value)
        return ""

    @staticmethod
    def list_devices(db: Session | None = None) -> tuple[bool, list[dict], str]:
        paths = ("/service/devices", "/devices")
        last_detail = "No se pudo obtener la lista de dispositivos de LG ThinQ."

        for path in paths:
            ok, payload, detail = LGThinQService._request_json(path, db=db)
            if not ok:
                last_detail = detail
                continue

            raw_devices = LGThinQService._extract_list(payload)
            if not raw_devices:
                continue

            devices: list[dict] = []
            for item in raw_devices:
                devices.append(
                    {
                        "id": LGThinQService._device_id(item),
                        "name": LGThinQService._device_name(item),
                        "type": LGThinQService._device_type(item),
                    }
                )
            return True, devices, "OK"

        return False, [], last_detail

    @staticmethod
    def _pick_device(devices: list[dict], preferred_name: str | None = None) -> dict | None:
        if not devices:
            return None

        normalized = (preferred_name or "").strip().lower()
        default_id = (os.getenv("LGTHINQ_DEFAULT_DEVICE_ID") or "").strip()
        if default_id:
            for device in devices:
                if str(device.get("id", "")).strip() == default_id:
                    return device

        if normalized:
            for device in devices:
                target = f"{device.get('name', '')} {device.get('type', '')}".lower()
                if normalized in target:
                    return device

        return devices[0]

    @staticmethod
    def _extract_status_map(payload: dict | list | None) -> dict:
        if isinstance(payload, dict):
            for key in ("status", "data", "result", "item", "snapshot"):
                value = payload.get(key)
                if isinstance(value, dict):
                    return value
            return payload
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    return item
        return {}

    @staticmethod
    def get_device_status(db: Session | None = None, preferred_name: str | None = None) -> tuple[bool, dict | None, dict, str]:
        ok, devices, detail = LGThinQService.list_devices(db=db)
        if not ok or not devices:
            return False, None, {}, detail

        selected = LGThinQService._pick_device(devices, preferred_name=preferred_name)
        if not selected:
            return False, None, {}, "No encontré un dispositivo LG ThinQ para consultar."

        device_id = str(selected.get("id", "")).strip()
        if not device_id:
            return False, selected, {}, "El dispositivo LG ThinQ no tiene id disponible."

        status_paths = (
            f"/service/devices/{urllib.parse.quote(device_id)}/status",
            f"/devices/{urllib.parse.quote(device_id)}/status",
            f"/service/devices/{urllib.parse.quote(device_id)}",
            f"/devices/{urllib.parse.quote(device_id)}",
        )
        last_detail = "No se pudo obtener el estado del dispositivo LG ThinQ."

        for path in status_paths:
            ok_status, payload, status_detail = LGThinQService._request_json(path, db=db)
            if not ok_status:
                last_detail = status_detail
                continue

            status_map = LGThinQService._extract_status_map(payload)
            if status_map:
                return True, selected, status_map, "OK"

        return False, selected, {}, last_detail

    @staticmethod
    def summarize_status(status: dict) -> list[str]:
        if not isinstance(status, dict):
            return []

        aliases = (
            ("operation", "Operacion"),
            ("runState", "Estado"),
            ("state", "Estado"),
            ("processState", "Proceso"),
            ("remainingTime", "Tiempo restante"),
            ("remainTimeMinute", "Minutos restantes"),
            ("course", "Programa"),
            ("waterTemp", "Temperatura"),
            ("spinSpeed", "Centrifugado"),
            ("error", "Error"),
            ("errorCode", "Codigo error"),
        )

        lines: list[str] = []
        for key, label in aliases:
            value = status.get(key)
            if value in (None, "", [], {}):
                continue
            lines.append(f"- {label}: {value}")

        if lines:
            return lines

        # Generic fallback in case provider uses custom keys.
        for key, value in status.items():
            if isinstance(value, (dict, list)):
                continue
            if value in (None, ""):
                continue
            lines.append(f"- {key}: {value}")
            if len(lines) >= 6:
                break
        return lines

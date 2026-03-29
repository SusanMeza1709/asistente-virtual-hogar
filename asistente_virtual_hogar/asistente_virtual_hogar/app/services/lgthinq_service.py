import hashlib
import json
import os
import uuid
import base64
from datetime import datetime
import urllib.parse
import urllib.error
import urllib.request

from sqlalchemy.orm import Session

from app.models.schemas import MemoryCreate
from app.services.memory_service import MemoryService
from app.services.whatsapp_service import WhatsAppService


def _deep_get(d: dict, *keys: str):
    """Safely navigate nested dicts; returns empty string if any key is missing."""
    for key in keys:
        if not isinstance(d, dict):
            return ""
        d = d.get(key) or {}
    return d if isinstance(d, str) else ""


def _load_json_memory_safe(db, key: str) -> dict:
    """Load JSON from memory without class dependency (for LGConsumerAuth)."""
    try:
        item = MemoryService.get_by_key(db, key)
        if not item or not item.value.strip():
            return {}
        parsed = json.loads(item.value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


class LGConsumerAuth:
    """LG ThinQ consumer authentication using email + password.

    Uses the same API as the official LG ThinQ app, which supports full device
    control including cycle (course) selection.

    Required env vars:
        LGTHINQ_USERNAME  – LG account email
        LGTHINQ_PASSWORD  – LG account password
    Optional:
        LGTHINQ_CONSUMER_COUNTRY – country code (defaults to LGTHINQ_COUNTRY_CODE or PE)
    """

    _GATEWAY_URL = "https://route.lgthinq.com:46030/v1/service/application/gateway-uri"
    _APP_CLIENT_ID = "LGAO221APPLICATION"
    _APP_VER = "3.6.1200"
    _TOKEN_DB_KEY = "__lgthinq_consumer_token__"

    @staticmethod
    def _username() -> str:
        return (os.getenv("LGTHINQ_USERNAME") or "").strip()

    @staticmethod
    def _password() -> str:
        return (os.getenv("LGTHINQ_PASSWORD") or "").strip()

    @staticmethod
    def _country() -> str:
        return (
            os.getenv("LGTHINQ_CONSUMER_COUNTRY")
            or os.getenv("LGTHINQ_COUNTRY_CODE")
            or "PE"
        ).strip().upper()

    @staticmethod
    def available() -> bool:
        return bool(LGConsumerAuth._username() and LGConsumerAuth._password())

    @staticmethod
    def _msg_id() -> str:
        return base64.urlsafe_b64encode(uuid.uuid4().bytes).decode("ascii").rstrip("=")

    @staticmethod
    def _base_headers(country: str) -> dict:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": (os.getenv("LGTHINQ_API_KEY") or "v6GFvkweNo7DK7yD3ylIZ9w52aKBU0eJ7wLXkSR3").strip(),
            "x-client-id": LGConsumerAuth._APP_CLIENT_ID,
            "x-country-code": country,
            "x-language-code": "es-419",
            "x-message-id": LGConsumerAuth._msg_id(),
            "x-service-code": "SVC202",
            "x-service-phase": "OP",
            "x-thinq-app-level": "PRD",
            "x-thinq-app-os": "ANDROID",
            "x-thinq-app-type": "NUTS",
            "x-thinq-app-ver": LGConsumerAuth._APP_VER,
        }

    @staticmethod
    def _do_request(
        url: str,
        *,
        method: str = "GET",
        body: dict | None = None,
        extra_headers: dict | None = None,
        country: str | None = None,
    ) -> tuple[bool, dict, str]:
        hdrs = LGConsumerAuth._base_headers(country or LGConsumerAuth._country())
        if extra_headers:
            hdrs.update(extra_headers)
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in hdrs.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = resp.read().decode("utf-8", errors="replace")
                parsed = json.loads(payload) if payload.strip() else {}
                return True, parsed if isinstance(parsed, dict) else {"raw": parsed}, "OK"
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                pass
            return False, {}, f"HTTP {exc.code}: {body_text}"
        except Exception as exc:
            return False, {}, str(exc)

    @staticmethod
    def get_gateway(country: str) -> tuple[bool, dict, str]:
        """Fetch country-specific gateway URLs from LG route service."""
        ok, data, detail = LGConsumerAuth._do_request(
            LGConsumerAuth._GATEWAY_URL, country=country
        )
        if not ok:
            return False, {}, detail
        result = data.get("result") or data
        emp_base = (result.get("empSpxUri") or result.get("empTermsUri") or "").rstrip("/")
        thinq2_uri = (result.get("thinq2Uri") or "").rstrip("/")
        if not emp_base:
            return False, {}, f"Gateway no devolvió empSpxUri: {json.dumps(result)[:300]}"
        return True, {"empBase": emp_base, "thinq2Uri": thinq2_uri, "raw": result}, "OK"

    @staticmethod
    def _login(emp_base: str, country: str, username: str, password: str) -> tuple[bool, dict, str]:
        """POST credentials to LG emp auth endpoint. Returns token dict."""
        pwd_hash = hashlib.sha512(password.encode("utf-8")).hexdigest()
        body = {
            "username": username,
            "user_auth2": pwd_hash,
            "type": "EMP",
            "country": country,
            "language": "es-419",
            "spaCode": LGConsumerAuth._APP_CLIENT_ID,
        }
        endpoints = [
            f"{emp_base}/v2.0/authorizeUser",
            f"{emp_base}/spx/login/sign_in",
        ]
        last_detail = ""
        for url in endpoints:
            ok, data, detail = LGConsumerAuth._do_request(url, method="POST", body=body, country=country)
            if ok:
                token = _deep_get(data, "result", "lgeAccessToken") or _deep_get(data, "result", "access_token") or data.get("access_token") or data.get("lgeAccessToken") or ""
                refresh = _deep_get(data, "result", "refresh_token") or data.get("refresh_token") or ""
                code = _deep_get(data, "result", "code") or data.get("code") or ""
                if token:
                    return True, {"access_token": token, "refresh_token": refresh, "raw": data}, "OK"
                if code:
                    return True, {"auth_code": code, "emp_base": emp_base, "raw": data}, "OK"
                return False, {}, f"Sin token en respuesta de {url}: {json.dumps(data)[:300]}"
            last_detail = detail
        return False, {}, f"Login fallido: {last_detail}"

    @staticmethod
    def _exchange_code(emp_base: str, country: str, code: str) -> tuple[bool, dict, str]:
        """Exchange an authorization code for OAuth tokens."""
        body = {
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": "lgaccount.lgsmartthinq:/",
        }
        token_url = f"{emp_base}/oauth/1.0/oauth2/token"
        ok, data, detail = LGConsumerAuth._do_request(token_url, method="POST", body=body, country=country)
        if ok:
            token = data.get("access_token") or _deep_get(data, "result", "access_token") or ""
            refresh = data.get("refresh_token") or _deep_get(data, "result", "refresh_token") or ""
            if token:
                return True, {"access_token": token, "refresh_token": refresh}, "OK"
            return False, {}, f"Sin access_token: {json.dumps(data)[:300]}"
        return False, {}, detail

    @staticmethod
    def authenticate(db) -> tuple[bool, str, str, str]:
        """Full auth flow: gateway → login → tokens.
        Returns (ok, access_token, thinq2_uri, detail).
        """
        import time
        if db:
            cached = _load_json_memory_safe(db, LGConsumerAuth._TOKEN_DB_KEY)
            token = cached.get("access_token", "")
            thinq2 = cached.get("thinq2Uri", "")
            expires_at = cached.get("expires_at", 0)
            if token and thinq2 and expires_at > time.time() + 60:
                return True, token, thinq2, "OK (cached)"

        country = LGConsumerAuth._country()
        username = LGConsumerAuth._username()
        password = LGConsumerAuth._password()
        if not username or not password:
            return False, "", "", "LGTHINQ_USERNAME o LGTHINQ_PASSWORD no configurados."

        ok, gw, detail = LGConsumerAuth.get_gateway(country)
        if not ok:
            return False, "", "", f"Gateway error: {detail}"
        emp_base = gw["empBase"]
        thinq2_uri = gw["thinq2Uri"]

        ok, token_data, detail = LGConsumerAuth._login(emp_base, country, username, password)
        if not ok:
            return False, "", "", f"Login error: {detail}"

        access_token = token_data.get("access_token", "")
        if not access_token and token_data.get("auth_code"):
            ok, exchanged, detail = LGConsumerAuth._exchange_code(emp_base, country, token_data["auth_code"])
            if not ok:
                return False, "", "", f"Token exchange error: {detail}"
            access_token = exchanged.get("access_token", "")

        if not access_token:
            return False, "", "", f"No access_token. Respuesta: {json.dumps(token_data)[:300]}"

        if db:
            try:
                MemoryService.save_item(db, MemoryCreate(
                    key=LGConsumerAuth._TOKEN_DB_KEY,
                    value=json.dumps({
                        "access_token": access_token,
                        "refresh_token": token_data.get("refresh_token", ""),
                        "thinq2Uri": thinq2_uri,
                        "expires_at": int(time.time()) + 3600,
                    })
                ))
            except Exception:
                pass

        return True, access_token, thinq2_uri, "OK"

    @staticmethod
    def start_cycle_consumer(
        db,
        device_id: str,
        cycle_upper: str,
        cycle_name: str,
    ) -> tuple[bool, str]:
        """Start a cycle using consumer token (full app-level API access)."""
        ok, token, thinq2_uri, detail = LGConsumerAuth.authenticate(db)
        if not ok:
            return False, f"Auth consumer falla: {detail}"

        country = LGConsumerAuth._country()
        api_key = (os.getenv("LGTHINQ_API_KEY") or "v6GFvkweNo7DK7yD3ylIZ9w52aKBU0eJ7wLXkSR3").strip()
        control_url = f"{thinq2_uri}/devices/{urllib.parse.quote(device_id)}/control"

        payloads = [
            # Format 1: location + course + operation (full consumer format)
            {
                "location": {"locationName": "MAIN"},
                "course": {"courseName": cycle_name},
                "operation": {"washerOperationMode": "START"},
            },
            # Format 2: select course only (device picks up and starts)
            {
                "location": {"locationName": "MAIN"},
                "course": {"courseName": cycle_name},
            },
            # Format 3: ctrlKey/command (older ThinQ v2 format)
            {
                "ctrlKey": "basicCtrl",
                "command": "Set",
                "dataKey": "Course",
                "dataValue": cycle_upper,
            },
            # Format 4: dataSetList (another ThinQ v2 variant)
            {
                "ctrlKey": "courseCtrl",
                "command": "Set",
                "dataSetList": {
                    "washerDryer": {
                        "courseType": cycle_upper,
                        "washerOperationMode": "START",
                    }
                },
            },
        ]

        for payload in payloads:
            req = urllib.request.Request(
                control_url,
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
            )
            req.add_header("Authorization", f"Bearer {token}")
            req.add_header("Accept", "application/json")
            req.add_header("Content-Type", "application/json")
            req.add_header("x-message-id", LGConsumerAuth._msg_id())
            req.add_header("x-country-code", country)
            req.add_header("x-client-id", LGConsumerAuth._APP_CLIENT_ID)
            req.add_header("x-api-key", api_key)
            req.add_header("x-thinq-app-ver", LGConsumerAuth._APP_VER)
            req.add_header("x-thinq-app-type", "NUTS")
            req.add_header("x-thinq-app-os", "ANDROID")
            req.add_header("x-service-phase", "OP")
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    resp.read()
                    return True, f"Ciclo {cycle_name} iniciado."
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    if db:
                        try:
                            MemoryService.save_item(db, MemoryCreate(
                                key=LGConsumerAuth._TOKEN_DB_KEY, value="{}"
                            ))
                        except Exception:
                            pass
                    return False, "Token expirado (401). Intenta de nuevo."
                continue
            except Exception:
                continue

        return False, "Consumer API: no se pudo iniciar el ciclo con ningún formato de comando."


class LGThinQService:
    """Simple client for LG ThinQ API. Uses PAT for basic ops; uses LGConsumerAuth
    (email/password) for full cycle control when LGTHINQ_USERNAME is configured."""

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
    def _country_code() -> str:
        return (os.getenv("LGTHINQ_COUNTRY_CODE") or "US").strip()

    @staticmethod
    def _client_id() -> str:
        # ThinQ requires a client identifier; stable fallback keeps troubleshooting easier.
        return (os.getenv("LGTHINQ_CLIENT_ID") or "asistente-virtual-hogar").strip()

    @staticmethod
    def _api_key() -> str:
        # Documented fixed key from LG ThinQ docs, but allow override by env.
        return (os.getenv("LGTHINQ_API_KEY") or "v6GFvkweNo7DK7yD3ylIZ9w52aKBU0eJ7wLXkSR3").strip()

    @staticmethod
    def _message_id() -> str:
        # url-safe-base64-no-padding UUIDv4, length 22.
        return base64.urlsafe_b64encode(uuid.uuid4().bytes).decode("ascii").rstrip("=")

    @staticmethod
    def _country_candidates() -> list[str]:
        primary = LGThinQService._country_code().upper()
        ordered = [primary, "PE", "US", "KR"]
        seen: set[str] = set()
        candidates: list[str] = []
        for item in ordered:
            value = (item or "").strip().upper()
            if not value or value in seen:
                continue
            seen.add(value)
            candidates.append(value)
        return candidates

    @staticmethod
    def can_query(db: Session | None = None) -> bool:
        return bool(LGThinQService._base_url() and LGThinQService._api_pat())

    @staticmethod
    def setup_instructions() -> str:
        return (
            "Para integrar LG ThinQ configura en Render: "
            "LGTHINQ_API_BASE_URL (ej: https://api-aic.lgthinq.com), "
            "LGTHINQ_API_PAT (tu Personal Access Token). "
            "Importante: LGTHINQ_COUNTRY_CODE debe coincidir con la region de tu cuenta (ej: PE, US, MX). "
            "Opcional: LGTHINQ_CLIENT_ID, LGTHINQ_API_KEY, "
            "LGTHINQ_DEFAULT_DEVICE_ID y LGTHINQ_WEBHOOK_SECRET."
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
        api_key = LGThinQService._api_key()
        if not base_url or not pat:
            return False, None, "LG ThinQ no tiene API configurada o falta el PAT."
        if not api_key:
            return False, None, "Falta LGTHINQ_API_KEY para llamar la API de LG ThinQ."

        url = f"{base_url}{path}"
        tried_countries: list[str] = []
        last_1309_detail = ""

        for country in LGThinQService._country_candidates():
            tried_countries.append(country)
            request = urllib.request.Request(url, method="GET")
            request.add_header("Authorization", f"Bearer {pat}")
            request.add_header("Accept", "application/json")
            request.add_header("Content-Type", "application/json")
            request.add_header("x-message-id", LGThinQService._message_id())
            request.add_header("x-country", country)
            request.add_header("x-client-id", LGThinQService._client_id())
            request.add_header("x-api-key", api_key)
            request.add_header("x-service-phase", "OP")

            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    payload = response.read().decode("utf-8", errors="replace")
                    if not payload.strip():
                        return True, {}, "OK"
                    parsed = json.loads(payload)
                    return True, parsed, "OK"
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")[:300]
                except Exception:
                    pass
                try:
                    parsed_error = json.loads(body) if body else {}
                except Exception:
                    parsed_error = {}
                error_code = str(((parsed_error.get("error") or {}).get("code") or "")).strip()
                if error_code == "1309":
                    last_1309_detail = body
                    continue
                return False, None, f"HTTP {exc.code} en {path}: {exc.reason}. {body}".strip()
            except Exception as exc:
                return False, None, f"No se pudo consultar LG ThinQ: {exc}"

        if last_1309_detail:
            return (
                False,
                None,
                "LG ThinQ devolvio 1309 (Not allowed api call) para todos los paises probados: "
                f"{', '.join(tried_countries)}. "
                "Tu PAT no tiene permiso para Device API o pertenece a otra cuenta/region. "
                f"Detalle: {last_1309_detail}",
            )
        return False, None, "No se pudo consultar LG ThinQ."

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

        # Fallback: walk any nested object/list keys in case provider wraps payload differently.
        for value in payload.values():
            if isinstance(value, (dict, list)):
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
        # Also check inside deviceInfo sub-object.
        info = device.get("deviceInfo") or {}
        for key in ("deviceId", "id"):
            value = info.get(key)
            if value:
                return str(value)
        return ""

    # Map LG ThinQ numeric device-type codes to Spanish labels.
    _DEVICE_TYPE_LABELS: dict[str, str] = {
        "101": "Refrigerador", "102": "Refrigerador",
        "201": "Lavadora", "202": "Secadora", "204": "Lavasecadora",
        "301": "Lavavajillas",
        "401": "Horno", "406": "Cocina",
        "501": "Aire acondicionado", "502": "Purificador de aire",
        "510": "Deshumidificador",
        "601": "TV", "602": "Monitor", "603": "Proyector",
        "701": "Robot aspirador",
        "801": "Calentador de agua",
        "WASHER": "Lavadora", "DRYER": "Secadora",
        "REFRIGERATOR": "Refrigerador", "AIR_CONDITIONER": "Aire acondicionado",
        "DISHWASHER": "Lavavajillas", "OVEN": "Horno",
        "STYLE_WASHER": "Lavadora", "TOWERTYPE_WASHER_DRYER_COMBO": "Lavasecadora",
        "DEVICE_WASHER": "Lavadora", "DEVICE_DRYER": "Secadora",
        "DEVICE_REFRIGERATOR": "Refrigerador", "DEVICE_AIR_CONDITIONER": "Aire acondicionado",
        "DEVICE_DISH_WASHER": "Lavavajillas", "DEVICE_OVEN": "Horno",
        "DEVICE_STYLER": "Styler LG", "DEVICE_TOWER_WASHER_DRYER_COMBO": "Lavasecadora",
        "DEVICE_ROBOT_CLEANER": "Robot aspirador", "DEVICE_WATER_PURIFIER": "Purificador de agua",
        "DEVICE_DEHUMIDIFIER": "Deshumidificador", "DEVICE_TV": "TV",
    }

    @staticmethod
    def _device_name(device: dict) -> str:
        # LG ThinQ nests alias/modelName inside deviceInfo.
        info = device.get("deviceInfo") or {}
        for key in ("alias", "name", "deviceAlias", "userDeviceName", "nickName",
                    "deviceNickName", "deviceName", "title", "label"):
            value = (info.get(key) or device.get(key) or "").strip()
            if value:
                return value
        # Build label from type + model.
        type_val = (info.get("deviceType") or device.get("deviceType") or device.get("type") or "").strip()
        type_label = LGThinQService._DEVICE_TYPE_LABELS.get(str(type_val).upper(),
                     LGThinQService._DEVICE_TYPE_LABELS.get(str(type_val), ""))
        model = (info.get("modelName") or device.get("modelName") or device.get("model") or "").strip()
        if type_label and model:
            return f"{type_label} LG {model}"
        if type_label:
            return f"{type_label} LG"
        if model:
            return f"LG {model}"
        return "Dispositivo LG"

    @staticmethod
    def _device_type(device: dict) -> str:
        info = device.get("deviceInfo") or {}
        for key in ("deviceType", "type", "platformType"):
            value = (info.get(key) or device.get(key) or "").strip()
            if value:
                label = LGThinQService._DEVICE_TYPE_LABELS.get(str(value).upper(),
                        LGThinQService._DEVICE_TYPE_LABELS.get(str(value), ""))
                return label or value
        return ""

    @staticmethod
    def list_devices(db: Session | None = None) -> tuple[bool, list[dict], str]:
        paths = (
            "/devices",
            "/v1/devices",
            "/thinq/v1/devices",
        )
        last_detail = "No se pudo obtener la lista de dispositivos de LG ThinQ."
        attempt_details: list[str] = []

        for path in paths:
            ok, payload, detail = LGThinQService._request_json(path, db=db)
            if not ok:
                if "1309" in detail:
                    # 1309 means permission/region mismatch; trying other paths only adds noisy 404s.
                    return False, [], detail
                last_detail = detail
                attempt_details.append(detail)
                continue

            raw_devices = LGThinQService._extract_list(payload)
            if not raw_devices:
                # If API answered OK but list is empty, treat it as a valid call, not an error.
                return True, [], "OK"

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

        if attempt_details:
            return False, [], " | ".join(attempt_details[:3])
        return False, [], last_detail

    @staticmethod
    def list_devices_raw(db: Session | None = None) -> tuple[bool, list[dict], str]:
        """Return raw device dicts from the API (for diagnostics)."""
        ok, payload, detail = LGThinQService._request_json("/devices", db=db)
        if not ok:
            return False, [], detail
        raw = LGThinQService._extract_list(payload)
        return True, raw, "OK"

    @staticmethod
    def get_device_profile(db: Session | None = None, device_id: str | None = None) -> tuple[bool, dict, str]:
        """Return the raw device profile from LG ThinQ API (for diagnostics)."""
        if not device_id:
            ok, devices, detail = LGThinQService.list_devices(db)
            if not ok or not devices:
                return False, {}, detail
            device_id = str(devices[0].get("id", "")).strip()

        profile_paths = [
            f"/devices/{urllib.parse.quote(device_id)}/profile",
            f"/v1/devices/{urllib.parse.quote(device_id)}/profile",
            f"/devices/{urllib.parse.quote(device_id)}/control-range",
        ]
        for path in profile_paths:
            ok, payload, detail = LGThinQService._request_json(path, db=db)
            if ok and payload:
                return True, payload if isinstance(payload, dict) else {"raw": payload}, "OK"
        return False, {}, "No se pudo obtener el perfil del dispositivo."

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

    # Fields that belong to the API envelope, not the device state.
    _ENVELOPE_FIELDS = frozenset({
        "messageId", "timestamp", "resultCode", "result", "code",
        "message", "traceId", "requestId",
    })
    # Fields that indicate we are looking at real device state.
    _STATE_HINT_FIELDS = frozenset({
        "runState", "state", "operation", "processState",
        "remainingTime", "remainTimeMinute", "course",
        "waterTemp", "spinSpeed", "error", "errorCode",
        "currentState", "onlineStatus", "powerState",
    })

    @staticmethod
    def _extract_status_map(payload: dict | list | None, _depth: int = 0) -> dict:
        if _depth > 6:
            return {}
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    found = LGThinQService._extract_status_map(item, _depth + 1)
                    if found:
                        return found
            return {}
        if not isinstance(payload, dict):
            return {}
        # If this dict already contains real state keys, return it directly.
        if any(k in payload for k in LGThinQService._STATE_HINT_FIELDS):
            return payload
        # Dig into known wrapper keys first (ordered by likelihood).
        for key in ("status", "body", "data", "response", "item", "snapshot", "result", "property"):
            value = payload.get(key)
            if isinstance(value, dict):
                found = LGThinQService._extract_status_map(value, _depth + 1)
                if found:
                    return found
            elif isinstance(value, list):
                found = LGThinQService._extract_status_map(value, _depth + 1)
                if found:
                    return found
        # Last resort: recurse into any non-envelope dict value.
        for key, value in payload.items():
            if key in LGThinQService._ENVELOPE_FIELDS:
                continue
            if isinstance(value, (dict, list)) and value:
                found = LGThinQService._extract_status_map(value, _depth + 1)
                if found:
                    return found
        return payload

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
            f"/devices/{urllib.parse.quote(device_id)}/state",
            f"/v1/devices/{urllib.parse.quote(device_id)}/state",
            f"/thinq/v1/devices/{urllib.parse.quote(device_id)}/state",
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

    # Map LG state codes to human-friendly Spanish labels.
    _STATE_TRANSLATIONS: dict[str, str] = {
        "INITIAL": "Reposo",
        "SLEEP": "Modo reposo",
        "TUB_CLEAN": "Limpieza de tambor",
        "DETECTING": "Detectando",
        "PREWASH": "Prelavado",
        "RINSING": "Enjuague",
        "SPINNING": "Centrifugado",
        "END": "Terminado",
        "COOLING": "Enfriamiento",
        "RESERVE": "Reserva",
        "ERROR": "Error",
        "RUN": "Ejecutando",
        "RUNNING": "En ejecución",
        "WASHING": "Lavando",
        "DRYING": "Secando",
        "PAUSE": "Pausado",
        "PAUSED": "Pausado",
        "POWER_OFF": "Apagado",
        "POWER_ON": "Encendido",
        "STANDBY": "En espera",
        "DRAINING": "Drenando",
        "OFF": "Apagado",
        "ON": "Encendido",
        "RESERVED": "Reservado",
        "COMPLETE": "Completo",
        "COMPLETED": "Completado",
    }

    # States that allow device control (START, STOP commands)
    _CONTROLLABLE_STATES = frozenset({
        "INITIAL", "STANDBY", "END", "PAUSE", "PAUSED",
        "POWER_ON", "ON", "IDLE", "READY",
    })
    # States that prevent control (device is off, sleeping, or executing)
    _UNCONTROLLABLE_STATES = frozenset({
        "SLEEP", "POWER_OFF", "OFF", "ERROR", "RUNNING",
        "WASHING", "DRYING", "EXECUTING", "RESERVED",
    })

    @staticmethod
    def _extract_scalar_value(val: any) -> str:
        """Extract readable scalar value from LG ThinQ nested structure."""
        if val is None or val == "" or val == [] or val == {}:
            return ""
        # If value is a dict with 'value' or '_value', extract it.
        if isinstance(val, dict):
            for key in ("value", "_value", "data"):
                if key in val:
                    inner = val[key]
                    if isinstance(inner, dict):
                        return LGThinQService._extract_scalar_value(inner)
                    return str(inner).strip() if inner not in (None, "", []) else ""
            # If dict has nested currentState or similar single char field, extract.
            for inner_key, inner_val in val.items():
                if inner_key not in LGThinQService._ENVELOPE_FIELDS:
                    return LGThinQService._extract_scalar_value(inner_val)
            return ""
        return str(val).strip() if val not in (None, "") else ""

    @staticmethod
    def _translate_state(code: str) -> str:
        """Translate LG state code to readable label."""
        upper_code = str(code or "").upper().strip()
        return LGThinQService._STATE_TRANSLATIONS.get(upper_code, code)

    @staticmethod
    def summarize_status(status: dict) -> list[str]:
        if not isinstance(status, dict):
            return []

        aliases = (
            ("operation", "Operación"),
            ("runState", "Estado"),
            ("state", "Estado"),
            ("currentState", "Estado actual"),
            ("processState", "Proceso"),
            ("remainingTime", "Tiempo restante"),
            ("remainTimeMinute", "Minutos restantes"),
            ("course", "Programa"),
            ("waterTemp", "Temperatura"),
            ("spinSpeed", "Centrifugado"),
            ("error", "Error"),
            ("errorCode", "Código error"),
        )

        lines: list[str] = []
        for key, label in aliases:
            raw_value = status.get(key)
            if raw_value in (None, "", [], {}):
                continue
            value = LGThinQService._extract_scalar_value(raw_value)
            if not value:
                continue
            # Translate state codes to Spanish.
            if key in ("operation", "runState", "state", "currentState", "processState"):
                value = LGThinQService._translate_state(value)
            lines.append(f"- {label}: {value}")

        if lines:
            return lines

        # Generic fallback: show any scalar field that is not envelope noise.
        for key, value in status.items():
            if key in LGThinQService._ENVELOPE_FIELDS:
                continue
            scalar = LGThinQService._extract_scalar_value(value)
            if scalar:
                lines.append(f"- {key}: {scalar}")
                if len(lines) >= 8:
                    break
        return lines

    @staticmethod
    def start_cycle(db: Session | None = None, device_id: str | None = None, cycle_type: str = "NORMAL") -> tuple[bool, str]:
        """Start a wash/dry cycle using correct LG API payload structure.
        
        Validates device state before attempting to start cycle. Rejects if device is in
        non-controllable state (SLEEP, POWER_OFF, ERROR, RUNNING, etc.).
        """
        base_url = LGThinQService._base_url()
        pat = LGThinQService._api_pat()
        if not base_url or not pat:
            return False, "LG ThinQ no está configurado."

        if not device_id:
            ok, devices, _ = LGThinQService.list_devices(db)
            if not devices:
                return False, "No encontré dispositivos LG ThinQ."
            device_id = str(devices[0].get("id", "")).strip()

        # Pre-flight check: get current device state
        ok, _, status_dict, _ = LGThinQService.get_device_status(db)
        if not ok or not status_dict:
            return False, "No pude verificar el estado de la lavadora."
        
        current_state_raw = status_dict.get("currentState", status_dict.get("runState", status_dict.get("state", "UNKNOWN")))
        current_state = LGThinQService._extract_scalar_value(current_state_raw).upper() or "UNKNOWN"
        
        # Check if device is in a controllable state
        if current_state in LGThinQService._UNCONTROLLABLE_STATES:
            translated_state = LGThinQService._translate_state(current_state)
            return False, f"La lavadora está en {translated_state} — no se puede iniciar un ciclo ahora."
        
        command_path = f"/devices/{urllib.parse.quote(device_id)}/control"
        cycle_upper = str(cycle_type or "NORMAL").upper()

        # Map internal cycle codes to the exact course names LG ThinQ API expects
        _LG_COURSE_NAMES: dict[str, str] = {
            "ALGODON": "Algodón",
            "ECO_40_60": "Eco 40-60",
            "TURBOWASH_59": "TurboWash 59",
            "MIXTOS": "Mixtos",
            "SINTETICO": "Sintético",
            "ANTIALERGICO": "Antialérgico",
            "CUIDADO_INFANTIL_CON_VAPOR": "Cuidado Infantil con Vapor",
            "DELICADO": "Delicado",
            "LAVADO_A_MANO_LANA": "Lavado a Mano/Lana",
            "RAPIDO_14": "Rápido 14",
            "SOLO_SECADO": "Sólo Secado",
            "LAVADO_SECADO": "Lavado+Secado",
            "LIMPIEZA_DE_TAMBOR": "Limpieza de Tambor",
            "DESCARGA_DE_CICLO": "Descarga de Ciclo",
            "SECADO_NORMAL": "Secado Normal",
            "SECADO_30_MIN": "Secado 30 min",
            "SECADO_60_MIN": "Secado 60 min",
            "SECADO_120_MIN": "Secado 120 min",
            "SECADO_PLANCHADO": "Secado Planchado",
            "SECADO_TEMPERATURA_BAJA": "Secado Temperatura Baja",
            "SECADO_NORMAL_ECO": "Secado Normal Eco",
            "ROPA_DE_CAMA": "Ropa de cama",
            "CENTRIFUGADO": "Centrifugado",
            "CUIDADO_DEL_BEBE": "Cuidado del Bebé",
            "DESODORIZACION": "Desodorización",
            "JEANS": "Jeans",
            "LENCERIA": "Lencería",
            "MANCHA_DE_SUDOR": "Mancha de Sudor",
            "MANCHAS_DE_COMIDA_Y_JUGO": "Manchas de Comida y Jugo",
            "TEMPORADA_DE_LLUVIAS": "Temporada de Lluvias",
            "VACIAR": "Vaciar",
        }
        cycle_name = _LG_COURSE_NAMES.get(cycle_upper, cycle_upper)

        # If consumer credentials are configured, use the full app-level API
        # which supports cycle selection (unlike the PAT B2B API).
        if LGConsumerAuth.available():
            return LGConsumerAuth.start_cycle_consumer(db, device_id, cycle_upper, cycle_name)


        # Per LG OpenAPI spec: washer command structure requires location + operation + course/cycle fields
        # All payloads include cycle_name to avoid falling back to the device default cycle.
        payloads = [
            # Washer format with location + operation + course (display name)
            {
                "location": {"locationName": "MAIN"},
                "operation": {"washerOperationMode": "START"},
                "course": {"courseName": cycle_name}
            },
            # Alternative: without location (for devices that might not need it)
            {
                "operation": {"washerOperationMode": "START"},
                "course": {"courseName": cycle_name}
            },
            # Dryer/other format with course
            {
                "location": {"locationName": "MAIN"},
                "operation": {"dryerOperationMode": "START"},
                "course": {"courseName": cycle_name}
            },
        ]

        last_error = None
        for payload in payloads:
            payload_json = json.dumps(payload)
            for country in LGThinQService._country_candidates():
                request = urllib.request.Request(
                    f"{base_url}{command_path}",
                    data=payload_json.encode("utf-8"),
                    method="POST",
                )
                request.add_header("Authorization", f"Bearer {pat}")
                request.add_header("Accept", "application/json")
                request.add_header("Content-Type", "application/json")
                request.add_header("x-message-id", LGThinQService._message_id())
                request.add_header("x-country", country)
                request.add_header("x-client-id", LGThinQService._client_id())
                request.add_header("x-api-key", LGThinQService._api_key())
                request.add_header("x-service-phase", "OP")
                try:
                    with urllib.request.urlopen(request, timeout=20) as response:
                        response.read()
                        return True, f"Ciclo {cycle_upper} iniciado en SujiLavadora."
                except urllib.error.HTTPError as exc:
                    body = ""
                    try:
                        body = exc.read().decode("utf-8", errors="replace")[:300]
                    except Exception:
                        pass
                    last_error = f"HTTP {exc.code}: {body}"
                    if exc.code == 401:
                        continue
                    break

        if last_error:
            return False, f"No se pudo iniciar el ciclo. {last_error}"
        return False, "No se pudo iniciar el ciclo (probé todos los formatos)."

    @staticmethod
    def stop_cycle(db: Session | None = None, device_id: str | None = None) -> tuple[bool, str]:
        """Stop the current wash/dry cycle using correct LG API format."""
        base_url = LGThinQService._base_url()
        pat = LGThinQService._api_pat()
        if not base_url or not pat:
            return False, "LG ThinQ no está configurado."

        if not device_id:
            ok, devices, _ = LGThinQService.list_devices(db)
            if not devices:
                return False, "No encontré dispositivos LG ThinQ."
            device_id = str(devices[0].get("id", "")).strip()

        command_path = f"/devices/{urllib.parse.quote(device_id)}/control"
        
        # Per LG OpenAPI spec: operation mode for STOP
        payloads = [
            {
                "location": {"locationName": "MAIN"},
                "operation": {"washerOperationMode": "STOP"}
            },
            {
                "operation": {"washerOperationMode": "STOP"}
            },
        ]

        last_error = None
        for payload in payloads:
            payload_json = json.dumps(payload)
            for country in LGThinQService._country_candidates():
                request = urllib.request.Request(
                    f"{base_url}{command_path}",
                    data=payload_json.encode("utf-8"),
                    method="POST",
                )
                request.add_header("Authorization", f"Bearer {pat}")
                request.add_header("Accept", "application/json")
                request.add_header("Content-Type", "application/json")
                request.add_header("x-message-id", LGThinQService._message_id())
                request.add_header("x-country", country)
                request.add_header("x-client-id", LGThinQService._client_id())
                request.add_header("x-api-key", LGThinQService._api_key())
                request.add_header("x-service-phase", "OP")
                try:
                    with urllib.request.urlopen(request, timeout=20) as response:
                        response.read()
                        return True, "Ciclo detenido."
                except urllib.error.HTTPError as exc:
                    body = ""
                    try:
                        body = exc.read().decode("utf-8", errors="replace")[:300]
                    except Exception:
                        pass
                    last_error = f"HTTP {exc.code}: {body}"
                    if exc.code == 401:
                        continue
                    break

        if last_error:
            return False, f"No se pudo detener el ciclo. {last_error}"
        return False, "No se pudo detener el ciclo (probé todos los formatos)."

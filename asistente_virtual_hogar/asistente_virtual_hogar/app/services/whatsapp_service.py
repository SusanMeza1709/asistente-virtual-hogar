import base64
import os
import urllib.parse
import urllib.request


class WhatsAppService:
    @staticmethod
    def _normalize_phone(phone: str) -> str:
        digits = "".join(ch for ch in phone if ch.isdigit())
        if not digits:
            return ""
        # Assume Peru if local mobile number is provided (9 digits)
        if len(digits) == 9 and digits.startswith("9"):
            digits = "51" + digits
        return f"whatsapp:+{digits}"

    @staticmethod
    def can_send_automatically() -> bool:
        return all(
            os.getenv(var)
            for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM")
        )

    @staticmethod
    def send_message(message: str, to_number: str) -> tuple[bool, str]:
        account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
        auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
        from_number = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()

        if not account_sid or not auth_token or not from_number:
            return (
                False,
                "Faltan credenciales de Twilio (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM). Configuralas en Render -> Service -> Environment. TWILIO_WHATSAPP_FROM debe verse como whatsapp:+14155238886.",
            )

        to_formatted = WhatsAppService._normalize_phone(to_number)
        if not to_formatted:
            return False, "No pude interpretar el número de destino para WhatsApp."

        from_formatted = from_number if from_number.startswith("whatsapp:") else f"whatsapp:{from_number}"

        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        payload = urllib.parse.urlencode(
            {
                "From": from_formatted,
                "To": to_formatted,
                "Body": message,
            }
        ).encode("utf-8")

        token = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
        request = urllib.request.Request(url, data=payload, method="POST")
        request.add_header("Authorization", f"Basic {token}")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if 200 <= response.status < 300:
                    return True, "Mensaje enviado por WhatsApp automáticamente."
                return False, f"Twilio respondió con estado {response.status}."
        except Exception as exc:
            return False, f"No se pudo enviar por WhatsApp automáticamente: {exc}"

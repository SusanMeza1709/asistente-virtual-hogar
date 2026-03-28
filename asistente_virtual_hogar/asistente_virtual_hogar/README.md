# Asistente Virtual de Hogar

Proyecto base en Python para crear un asistente virtual que controle inventario de alimentos, registre compras y consumos, genere alertas y recuerde preferencias del usuario.

## Funciones incluidas en esta versión

- Registro y consulta de productos.
- Registro de compras y aumento automático de stock.
- Registro de consumo y descuento automático de stock.
- Alertas de stock bajo.
- Alertas de productos próximos a vencer.
- Memoria básica para guardar preferencias del usuario.
- Chat inicial por reglas para ejecutar comandos simples.
- Base de datos SQLite local.

## Estructura

```text
asistente_virtual_hogar/
├── app/
│   ├── main.py
│   ├── database/
│   ├── models/
│   ├── routes/
│   ├── services/
│   └── utils/
├── tests/
├── requirements.txt
└── README.md
```

## Cómo abrirlo en Visual Studio

1. Abre Visual Studio.
2. Crea o abre una carpeta local.
3. Copia el contenido del proyecto.
4. Instala la carga de trabajo de Python si aún no la tienes.
5. Abre la terminal dentro del proyecto.

## Instalación

```bash
python -m venv .venv
```

En Windows:

```bash
.venv\Scripts\activate
pip install -r requirements.txt
```

## Ejecución

```bash
uvicorn app.main:app --reload
```

Luego abre:

- API: http://127.0.0.1:8000
- Swagger: http://127.0.0.1:8000/docs
- Voz: http://127.0.0.1:8000/voz

## Acceso desde celular/tablet sin tener la PC encendida

Para que funcione siempre desde un link, debes desplegarlo en la nube.

### Opción recomendada: Render

El proyecto ya incluye `render.yaml` para desplegar web + base de datos PostgreSQL.

1. Sube este proyecto a un repositorio de GitHub.
2. En Render, elige **New +** → **Blueprint**.
3. Conecta tu repo y confirma el despliegue.
4. Render creará automáticamente:
  - Servicio web `asistente-virtual-hogar`.
  - Base de datos `asistente-virtual-hogar-db`.
5. Cuando termine, abre la URL pública que te da Render.

Endpoints en producción:

- API raíz: `https://tu-app.onrender.com/`
- Swagger: `https://tu-app.onrender.com/docs`
- Interfaz de voz: `https://tu-app.onrender.com/voz`

### Nota importante para voz en móvil

El reconocimiento de voz del navegador requiere HTTPS en la mayoría de dispositivos móviles. Render ya entrega HTTPS, por eso `/voz` funciona mejor allí que en red local sin certificado.

## Configurar WhatsApp automatico (Twilio)

Para que el comando "enviame la lista por WhatsApp automatico" envie el mensaje de verdad (sin link manual), configura Twilio en Render.

1. En Twilio, activa WhatsApp Sandbox (o usa un numero de WhatsApp Business aprobado).
2. Copia estos datos de Twilio:
  - `TWILIO_ACCOUNT_SID`
  - `TWILIO_AUTH_TOKEN`
  - `TWILIO_WHATSAPP_FROM` (ejemplo: `whatsapp:+14155238886`)
3. En Render, abre tu servicio web y agrega esas 3 variables en **Environment**.
4. Haz redeploy del servicio.
5. Si usas Sandbox, une tu numero enviando el codigo `join ...` al numero de sandbox.

Despues de eso, en el chat:

- `mi numero de whatsapp es 926342398`
- `enviame la lista por whatsapp automatico`

## Configurar LG ThinQ con Personal Access Token (PAT)

El asistente incluye integración con LG ThinQ usando un Personal Access Token para acceso directo a dispositivos y estados.

**Variables requeridas en Render (Environment):**

- `LGTHINQ_API_BASE_URL` (ej: `https://api-aic.lgthinq.com`)
- `LGTHINQ_API_PAT` (tu Personal Access Token)

**Variables opcionales:**

- `LGTHINQ_DEFAULT_DEVICE_ID` (si tienes varios dispositivos, específica cuál usar por defecto)
- `LGTHINQ_WEBHOOK_SECRET` (para validar webhooks)

**Pasos:**

1. Obtén tu Personal Access Token (PAT) desde tu cuenta LG ThinQ
2. Configura las variables en Render:
   - `LGTHINQ_API_BASE_URL` = URL base de la API (ej: `https://api-aic.lgthinq.com`)
   -` LGTHINQ_API_PAT` = el token que obtuviste
3. Haz redeploy

**Comandos de ejemplo en el chat:**

- `lista mis dispositivos LG`
- `estado de mi lavadora LG`
- `estado de mi secadora LG`
- `avísame cuando termine la lavadora LG`
- `desactiva la alerta de la lavadora LG`
- `revisar alerta LG`

**Alertas automáticas:**

- Si tu proveedor soporta webhook, envía eventos a `POST /integrations/lgthinq/webhook`
- Si usas polling, llama a `POST /integrations/lgthinq/poll`
- El sistema detecta cambios de estado y registra eventos como `cycle_finished`
- Si WhatsApp está configurado, intentará avisarte automáticamente cuando detecte que terminó el ciclo

## Integrar Alexa sin cambiar la logica actual

La API incluye un webhook para Alexa que reutiliza exactamente el mismo motor de chat:

- `POST /alexa/webhook`

Produccion:

- `https://tu-app.onrender.com/alexa/webhook`

La Skill de Alexa solo envia texto al backend y el backend responde usando `ChatService.reply(...)`, por lo que se mantiene el mismo comportamiento que ya tienes en web/chat.

### 1) Crear Skill Custom en Alexa Developer Console

1. Crea una Skill tipo `Custom`.
2. En `Endpoint`, configura HTTPS apuntando a tu webhook.
3. Guarda y construye el modelo.

### 2) Interaction Model recomendado

Define un intent libre, por ejemplo `ComandoHogarIntent`, con slot:

- Slot name: `message`
- Slot type: `AMAZON.SearchQuery`

Utterances sugeridas:

- `{message}`
- `quiero {message}`
- `necesito {message}`

Con esto Alexa enviara frases completas como:

- `estado de mi lavadora LG`
- `inicia un ciclo delicado en la lavadora LG`
- `comprar 2 leche`

### 3) Intents nativos ya cubiertos

- `AMAZON.HelpIntent`
- `AMAZON.StopIntent`
- `AMAZON.CancelIntent`
- `AMAZON.FallbackIntent`

## Ejemplos de uso

### Crear producto

```json
POST /productos
{
  "name": "Yogurt",
  "category": "Lácteos",
  "unit": "unidad",
  "stock_current": 4,
  "stock_minimum": 2,
  "location": "Refrigerador",
  "expiration_date": "2026-03-25"
}
```

### Registrar compra

```json
POST /compras
{
  "product_name": "Yogurt",
  "quantity": 3,
  "unit_price": 1.8,
  "store": "Plaza Vea"
}
```

### Registrar consumo

```json
POST /consumos
{
  "product_name": "Yogurt",
  "quantity": 1,
  "note": "Desayuno"
}
```

### Guardar memoria

```json
POST /memoria
{
  "key": "marca favorita de leche",
  "value": "Gloria"
}
```

### Chat por reglas

```json
POST /chat
{
  "message": "agregar producto Avena, cantidad 1, unidad bolsa, categoria Cereales, ubicacion Despensa, stock_minimo 1"
}
```

También puedes usar:

- `comprar 2 Leche`
- `consumir 1 Leche`
- `ver inventario`
- `ver alertas`
- `recuerda bebida favorita: café`

## Próximas mejoras recomendadas

- Interfaz web propia.
- Reconocimiento de voz.
- Lector de tickets u OCR.
- Recomendador de recetas.
- Panel de consumo mensual.
- Login y usuarios múltiples.
- Integración con WhatsApp o Telegram.

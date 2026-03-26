from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.database.connection import Base, SessionLocal, engine
from app.routes.alerts import router as alerts_router
from app.routes.chat import router as chat_router
from app.routes.consumptions import router as consumptions_router
from app.routes.memory import router as memory_router
from app.routes.products import router as products_router
from app.routes.purchases import router as purchases_router
from app.utils.seed_data import seed_if_empty

app = FastAPI(
    title="Asistente Virtual de Hogar",
    version="1.0.0",
    description=(
        "API para controlar compras, consumo, inventario, alertas y memoria básica "
        "de un asistente virtual para el hogar."
    ),
)

Base.metadata.create_all(bind=engine)
with SessionLocal() as db:
    seed_if_empty(db)

app.include_router(products_router)
app.include_router(purchases_router)
app.include_router(consumptions_router)
app.include_router(alerts_router)
app.include_router(memory_router)
app.include_router(chat_router)

VOICE_UI_PATH = Path(__file__).parent / "static" / "voz.html"
PANEL_UI_PATH = Path(__file__).parent / "static" / "panel.html"


@app.get("/")
def root():
    return {
        "message": "Asistente Virtual de Hogar activo.",
        "docs": "/docs",
        "voz": "/voz",
        "panel": "/panel",
        "modulos": ["productos", "compras", "consumos", "alertas", "memoria", "chat"],
    }


@app.get("/voz")
def voice_ui():
    return FileResponse(VOICE_UI_PATH)


@app.get("/panel")
def dashboard_ui():
    return FileResponse(PANEL_UI_PATH)

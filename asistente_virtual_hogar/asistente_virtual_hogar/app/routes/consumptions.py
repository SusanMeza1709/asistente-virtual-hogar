from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.schemas import ConsumptionCreate, ConsumptionResponse
from app.services.consumption_service import ConsumptionService
from app.services.product_service import ProductService

router = APIRouter(prefix="/consumos", tags=["Consumos"])


@router.post("", response_model=ConsumptionResponse, status_code=201)
def register_consumption(payload: ConsumptionCreate, db: Session = Depends(get_db)):
    product = ProductService.get_by_name(db, payload.product_name)
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    try:
        return ConsumptionService.register_consumption(db, product, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

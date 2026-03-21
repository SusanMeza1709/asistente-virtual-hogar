from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.schemas import PurchaseCreate, PurchaseResponse
from app.services.product_service import ProductService
from app.services.purchase_service import PurchaseService

router = APIRouter(prefix="/compras", tags=["Compras"])


@router.post("", response_model=PurchaseResponse, status_code=201)
def register_purchase(payload: PurchaseCreate, db: Session = Depends(get_db)):
    product = ProductService.get_by_name(db, payload.product_name)
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    return PurchaseService.register_purchase(db, product, payload)

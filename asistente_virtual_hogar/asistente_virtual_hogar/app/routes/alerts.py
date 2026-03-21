from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.schemas import AlertsResponse
from app.services.alert_service import AlertService

router = APIRouter(prefix="/alertas", tags=["Alertas"])


@router.get("", response_model=AlertsResponse)
def get_alerts(db: Session = Depends(get_db)):
    return AlertService.build_alerts(db)

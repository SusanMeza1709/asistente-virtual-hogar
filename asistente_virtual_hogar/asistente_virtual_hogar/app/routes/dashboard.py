from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/pdf")
def get_dashboard_pdf(days: int = Query(default=30, ge=1, le=90), db: Session = Depends(get_db)):
    content = DashboardService.build_dashboard_pdf(db, period_days=days)
    headers = {"Content-Disposition": "inline; filename=dashboard_hogar.pdf"}
    return Response(content=content, media_type="application/pdf", headers=headers)

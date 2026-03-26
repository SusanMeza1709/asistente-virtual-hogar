from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/pdf")
def get_dashboard_pdf(db: Session = Depends(get_db)):
    content = DashboardService.build_dashboard_pdf(db)
    headers = {"Content-Disposition": "inline; filename=dashboard_hogar.pdf"}
    return Response(content=content, media_type="application/pdf", headers=headers)

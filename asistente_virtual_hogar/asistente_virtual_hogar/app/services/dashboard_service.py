from datetime import datetime
from io import BytesIO

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from app.services.alert_service import AlertService
from app.services.product_service import ProductService
from app.services.purchase_service import PurchaseService


class DashboardService:
    @staticmethod
    def build_dashboard_pdf(db: Session) -> bytes:
        alerts = AlertService.build_alerts(db)
        expenses = PurchaseService.summarize_expenses(db, days=30)
        products = ProductService.list_products(db)

        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter
        y = height - 40

        def draw_line(text: str, jump: int = 16):
            nonlocal y
            if y < 50:
                pdf.showPage()
                y = height - 40
            pdf.drawString(40, y, text)
            y -= jump

        pdf.setFont("Helvetica-Bold", 14)
        draw_line("Dashboard Hogar - Resumen", jump=22)
        pdf.setFont("Helvetica", 10)
        draw_line(f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        draw_line("")

        pdf.setFont("Helvetica-Bold", 12)
        draw_line("1) Gastos del hogar (ultimos 30 dias)", jump=18)
        pdf.setFont("Helvetica", 10)
        draw_line(f"- Gasto total: {expenses.total_amount}")
        draw_line(f"- Compras registradas: {expenses.purchases_count}")
        draw_line(f"- Compras con precio: {expenses.items_with_price}")
        draw_line("")

        pdf.setFont("Helvetica-Bold", 12)
        draw_line("2) Lista de compras", jump=18)
        pdf.setFont("Helvetica", 10)
        if alerts.shopping_list:
            for item in alerts.shopping_list:
                draw_line(f"- {item.product_name}: {item.needed_quantity} {item.unit}")
        else:
            draw_line("- Sin faltantes por ahora")
        draw_line("")

        pdf.setFont("Helvetica-Bold", 12)
        draw_line("3) Alertas", jump=18)
        pdf.setFont("Helvetica", 10)
        draw_line(f"- Vencidos: {len(alerts.expired)}")
        draw_line(f"- Por vencer: {len(alerts.expiring_soon)}")
        draw_line(f"- Stock bajo: {len(alerts.low_stock)}")
        draw_line("")

        pdf.setFont("Helvetica-Bold", 12)
        draw_line("4) Inventario actual", jump=18)
        pdf.setFont("Helvetica", 10)
        if products:
            for product in products:
                draw_line(f"- {product.name}: {product.stock_current} {product.unit} ({product.location or 'Sin ubicacion'})")
        else:
            draw_line("- Inventario vacio")

        pdf.save()
        buffer.seek(0)
        return buffer.read()

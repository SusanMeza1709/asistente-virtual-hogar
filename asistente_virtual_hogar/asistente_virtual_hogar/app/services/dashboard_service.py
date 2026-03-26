from collections import defaultdict
from datetime import datetime, timedelta
from io import BytesIO

from reportlab.graphics import renderPDF
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from app.services.alert_service import AlertService
from app.services.product_service import ProductService
from app.services.purchase_service import PurchaseService


class DashboardService:
    @staticmethod
    def _fmt_number(value: float) -> str:
        if abs(value - int(value)) < 1e-9:
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")

    @staticmethod
    def _draw_card(pdf: canvas.Canvas, x: float, y: float, w: float, h: float, title: str, value: str, subtitle: str, bg: colors.Color) -> None:
        pdf.setFillColor(bg)
        pdf.roundRect(x, y, w, h, 10, stroke=0, fill=1)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(x + 10, y + h - 16, title)
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(x + 10, y + h - 40, value)
        pdf.setFont("Helvetica", 8)
        pdf.drawString(x + 10, y + 10, subtitle)

    @staticmethod
    def _draw_spending_chart(
        pdf: canvas.Canvas,
        x: float,
        y: float,
        width: float,
        height: float,
        labels: list[str],
        values: list[float],
    ) -> None:
        drawing = Drawing(width, height)
        drawing.add(Rect(0, 0, width, height, rx=8, ry=8, fillColor=colors.HexColor("#F7FAFC"), strokeColor=colors.HexColor("#D9E2EC")))
        drawing.add(String(12, height - 18, "Gasto por producto (top)", fontName="Helvetica-Bold", fontSize=10, fillColor=colors.HexColor("#102A43")))

        if values:
            chart = VerticalBarChart()
            chart.x = 32
            chart.y = 26
            chart.height = height - 64
            chart.width = width - 50
            chart.data = [values]
            chart.valueAxis.valueMin = 0
            chart.valueAxis.labels.fontName = "Helvetica"
            chart.valueAxis.labels.fontSize = 7
            chart.categoryAxis.labels.boxAnchor = "n"
            chart.categoryAxis.labels.fontName = "Helvetica"
            chart.categoryAxis.labels.fontSize = 7
            chart.categoryAxis.categoryNames = labels
            chart.bars[0].fillColor = colors.HexColor("#2F80ED")
            chart.barSpacing = 4
            chart.groupSpacing = 8
            drawing.add(chart)
        else:
            drawing.add(String(12, height / 2, "Sin compras con precio en el periodo", fontName="Helvetica", fontSize=9, fillColor=colors.HexColor("#486581")))

        renderPDF.draw(drawing, pdf, x, y)

    @staticmethod
    def _draw_alert_pie(
        pdf: canvas.Canvas,
        x: float,
        y: float,
        width: float,
        height: float,
        low_stock: int,
        expiring: int,
        expired: int,
    ) -> None:
        drawing = Drawing(width, height)
        drawing.add(Rect(0, 0, width, height, rx=8, ry=8, fillColor=colors.HexColor("#FFF8F2"), strokeColor=colors.HexColor("#F0D9C7")))
        drawing.add(String(12, height - 18, "Distribucion de alertas", fontName="Helvetica-Bold", fontSize=10, fillColor=colors.HexColor("#7C2D12")))

        total = low_stock + expiring + expired
        if total > 0:
            pie = Pie()
            pie.x = 8
            pie.y = 8
            pie.width = 120
            pie.height = 120
            pie.data = [low_stock, expiring, expired]
            pie.labels = ["Stock bajo", "Por vencer", "Vencidos"]
            pie.slices.strokeWidth = 0.5
            pie.slices[0].fillColor = colors.HexColor("#F59E0B")
            pie.slices[1].fillColor = colors.HexColor("#60A5FA")
            pie.slices[2].fillColor = colors.HexColor("#EF4444")
            pie.slices[0].popout = 2
            drawing.add(pie)
        else:
            drawing.add(String(12, 56, "Sin alertas activas", fontName="Helvetica", fontSize=9, fillColor=colors.HexColor("#486581")))

        renderPDF.draw(drawing, pdf, x, y)

    @staticmethod
    def build_dashboard_pdf(db: Session, period_days: int = 30) -> bytes:
        period_days = max(1, min(period_days, 90))
        alerts = AlertService.build_alerts(db)
        expenses = PurchaseService.summarize_expenses(db, days=period_days)
        products = ProductService.list_products(db)
        purchases = PurchaseService.list_purchases(db)
        cutoff = datetime.utcnow() - timedelta(days=period_days)
        period_purchases = [purchase for purchase in purchases if purchase.purchased_at >= cutoff]

        spend_by_product: dict[str, float] = defaultdict(float)
        for purchase in period_purchases:
            if purchase.unit_price is None:
                continue
            product_name = purchase.product.name if purchase.product else "Producto"
            spend_by_product[product_name] += float(purchase.unit_price * purchase.quantity)

        top_spend = sorted(spend_by_product.items(), key=lambda row: row[1], reverse=True)[:6]
        chart_labels = [name[:10] + ("..." if len(name) > 10 else "") for name, _ in top_spend]
        chart_values = [round(amount, 2) for _, amount in top_spend]

        inventory_lines = [
            f"{product.name}: {DashboardService._fmt_number(product.stock_current)} {product.unit}"
            for product in sorted(products, key=lambda item: (item.stock_current <= item.stock_minimum, item.name))[:10]
        ]

        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter

        pdf.setFillColor(colors.HexColor("#F4F7FB"))
        pdf.rect(0, 0, width, height, stroke=0, fill=1)

        pdf.setFillColor(colors.HexColor("#102A43"))
        pdf.setFont("Helvetica-Bold", 20)
        pdf.drawString(36, height - 44, "Dashboard mensual del hogar")
        pdf.setFillColor(colors.HexColor("#486581"))
        pdf.setFont("Helvetica", 9)
        pdf.drawString(
            36,
            height - 60,
            f"Periodo: ultimos {period_days} dias | Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        )

        card_w, card_h, gutter = 126, 66, 12
        card_y = height - 145
        cards = [
            ("Gasto total", f"S/ {DashboardService._fmt_number(expenses.total_amount)}", "Compras con precio", f"{expenses.items_with_price}/{expenses.purchases_count}", colors.HexColor("#0F766E")),
            ("Inventario", str(len(products)), "Productos activos", "En casa", colors.HexColor("#1D4ED8")),
            ("Alertas", str(len(alerts.low_stock) + len(alerts.expiring_soon) + len(alerts.expired)), "Total de alertas", "Atencion diaria", colors.HexColor("#B45309")),
            ("Vencidos", str(len(alerts.expired)), "Productos vencidos", "Revisar y descartar", colors.HexColor("#B91C1C")),
        ]

        for idx, card in enumerate(cards):
            x = 36 + (card_w + gutter) * idx
            DashboardService._draw_card(pdf, x, card_y, card_w, card_h, card[0], card[1], f"{card[2]}: {card[3]}", card[4])

        DashboardService._draw_spending_chart(
            pdf,
            x=36,
            y=height - 410,
            width=360,
            height=220,
            labels=chart_labels,
            values=chart_values,
        )
        DashboardService._draw_alert_pie(
            pdf,
            x=408,
            y=height - 410,
            width=168,
            height=220,
            low_stock=len(alerts.low_stock),
            expiring=len(alerts.expiring_soon),
            expired=len(alerts.expired),
        )

        pdf.setFillColor(colors.HexColor("#102A43"))
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(36, height - 430, "Inventario clave")
        pdf.setFont("Helvetica", 9)
        pdf.setFillColor(colors.HexColor("#334E68"))

        if not inventory_lines:
            pdf.drawString(36, height - 445, "No hay productos registrados todavia.")
        else:
            y = height - 445
            for line in inventory_lines:
                if y < 58:
                    break
                pdf.drawString(36, y, f"- {line}")
                y -= 14

        pdf.setFillColor(colors.HexColor("#829AB1"))
        pdf.setFont("Helvetica", 8)
        pdf.drawString(36, 26, "Nota: La lista de compras se comparte como mensaje aparte para mantener este PDF enfocado en gastos y estado general.")

        pdf.save()
        buffer.seek(0)
        return buffer.read()

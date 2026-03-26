from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    category: Optional[str] = Field(default=None, max_length=80)
    unit: str = Field(default="unidad", max_length=20)
    stock_current: float = Field(default=0, ge=0)
    stock_minimum: float = Field(default=1, ge=0)
    location: Optional[str] = Field(default=None, max_length=50)
    expiration_date: Optional[date] = None


class ProductUpdate(BaseModel):
    category: Optional[str] = Field(default=None, max_length=80)
    unit: Optional[str] = Field(default=None, max_length=20)
    stock_current: Optional[float] = Field(default=None, ge=0)
    stock_minimum: Optional[float] = Field(default=None, ge=0)
    location: Optional[str] = Field(default=None, max_length=50)
    expiration_date: Optional[date] = None
    status: Optional[str] = Field(default=None, max_length=20)


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: Optional[str]
    unit: str
    stock_current: float
    stock_minimum: float
    location: Optional[str]
    expiration_date: Optional[date]
    status: str
    created_at: datetime
    updated_at: datetime


class PurchaseCreate(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=120)
    quantity: float = Field(..., gt=0)
    unit_price: Optional[float] = Field(default=None, ge=0)
    store: Optional[str] = Field(default=None, max_length=120)
    purchased_at: Optional[datetime] = None


class PurchaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    quantity: float
    unit_price: Optional[float]
    store: Optional[str]
    purchased_at: datetime


class ConsumptionCreate(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=120)
    quantity: float = Field(..., gt=0)
    consumed_at: Optional[datetime] = None
    note: Optional[str] = Field(default=None, max_length=255)


class ConsumptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    quantity: float
    consumed_at: datetime
    note: Optional[str]


class MemoryCreate(BaseModel):
    key: str = Field(..., min_length=1, max_length=120)
    value: str = Field(..., min_length=1)


class MemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    value: str
    created_at: datetime
    updated_at: datetime


class ChatMessage(BaseModel):
    message: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    reply: str


class AlertItem(BaseModel):
    product_name: str
    current_stock: float
    minimum_stock: float
    unit: str
    reason: str
    expiration_date: Optional[date] = None
    days_until_expiration: Optional[int] = None
    suggested_action: Optional[str] = None


class ShoppingListItem(BaseModel):
    product_name: str
    needed_quantity: float
    unit: str
    reason: str


class ExpenseSummary(BaseModel):
    total_amount: float
    purchases_count: int
    items_with_price: int
    period_days: int


class AlertsResponse(BaseModel):
    low_stock: list[AlertItem]
    expiring_soon: list[AlertItem]
    expired: list[AlertItem]
    consume_first: list[AlertItem]
    shopping_list: list[ShoppingListItem]

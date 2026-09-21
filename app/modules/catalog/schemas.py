"""Catalog request and response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateProductRequest(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    description: str = Field(min_length=5)
    price_egp: float = Field(ge=0, description="Price in EGP")
    category: str = Field(default="medical", max_length=64)
    product_type: str = Field(default="memo", max_length=32)
    medical_year: int = Field(default=1, ge=1, le=6)
    folder_id: uuid.UUID | None = None
    preview_data: str | None = None


class PriceRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    min_quantity: int
    discount_percent: int


class BundleItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_product_id: uuid.UUID
    order_index: int


class BundleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    items: list[BundleItemOut] = []


class ProductPreviewResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    product_type: str
    medical_year: int
    price_egp: float
    preview_excerpt: str
    sample_pages: list[str] = []
    page_count: int = 1


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str
    price_piastres: int
    price_egp: float
    currency: str
    category: str
    product_type: str = "memo"
    medical_year: int = 1
    folder_id: uuid.UUID | None = None
    preview_data: str | None = None
    is_active: bool
    created_at: datetime
    price_rules: list[PriceRuleOut] = []
    bundle: BundleOut | None = None


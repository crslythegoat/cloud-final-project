"""Business validation shared by HTTP commands and event projection."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

Text = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^\x00]+$")]
Code = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Z0-9][A-Z0-9._-]*$")]
Money = Annotated[int, Field(strict=True, ge=0, le=9223372036854775807)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Category(Input):
    code: Code
    name: Text


class Product(Input):
    sku: Code
    name: Text
    description: str | None = Field(default=None, max_length=2000)
    category_id: UUID
    unit_price_minor: Money
    currency: Literal["MXN"] = "MXN"


class Customer(Input):
    name: Text
    email: EmailStr


class OrderItem(Input):
    product_id: UUID
    quantity: int = Field(strict=True, ge=1, le=2147483647)
    unit_price_minor: Money


class Order(Input):
    order_reference: Code
    customer_id: UUID
    status: Literal["draft", "confirmed", "completed", "cancelled"] = "draft"
    currency: Literal["MXN"] = "MXN"
    items: list[OrderItem] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid_items(self):
        if len({i.product_id for i in self.items}) != len(self.items):
            raise ValueError("Duplicate product in order")
        if self.status != "draft" and not self.items:
            raise ValueError("Non-draft orders require items")
        return self


MODELS = {"category": Category, "product": Product, "customer": Customer, "order": Order}
TABLES = {"category": "categories", "product": "products", "customer": "customers", "order": "orders"}


def business_keys(kind, data):
    if kind == "category":
        return {"code": data["code"], "normalized_name": " ".join(data["name"].casefold().split())}
    if kind == "customer":
        return {"normalized_email": data["email"].casefold()}
    key = "sku" if kind == "product" else "order_reference"
    return {key: data[key]}

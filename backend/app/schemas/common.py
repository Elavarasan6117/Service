from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

T = TypeVar("T")


class CamelModel(BaseModel):
    """Base schema.

    The API speaks camelCase (matching the response examples in the brief) while
    Python stays snake_case. ``populate_by_name`` means either spelling is
    accepted on input, which keeps integration scripts forgiving.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        str_strip_whitespace=True,
    )


class PageMeta(CamelModel):
    total: int
    page: int
    page_size: int
    total_pages: int


class Page(CamelModel, Generic[T]):
    items: list[T]
    meta: PageMeta


class PaginationParams(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(50, ge=1, le=500)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class MessageResponse(CamelModel):
    message: str


class Coordinates(CamelModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)

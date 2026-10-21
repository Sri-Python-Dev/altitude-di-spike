from __future__ import annotations
from typing import ClassVar, Generic, TypeVar
from pydantic import BaseModel, Field

T = TypeVar('T')
class Evidenced(BaseModel, Generic[T]):
    value: T | None = None
    page: int | None = Field(default=None, ge=0)  # 1-based PDF page; 0 = email body
    # (x0, y0, x1, y1) in PDF points, top-left origin. Set only by the Ground step.
    bbox: tuple[float, float, float, float] | None = None
    verbatim_span: str | None = None
    raw_confidence: float | None = None  # model self-report: stored, never trusted
    grounded: bool = False
    ungrounded_span: str | None = None
    ungrounded_reason: str | None = None  # fixed code from extract.ground, never content

class QuoteLineItem(BaseModel):
    part_number_quoted: Evidenced[str] = Field(default_factory=Evidenced[str])
    quantity: Evidenced[float] = Field(default_factory=Evidenced[float])
    unit_price: Evidenced[float] = Field(default_factory=Evidenced[float])
    extended_price: Evidenced[float] = Field(default_factory=Evidenced[float])
    lead_time_days: Evidenced[int] = Field(default_factory=Evidenced[int])
    moq: Evidenced[float] = Field(default_factory=Evidenced[float])

class SupplierQuote(BaseModel):
    HEADER_FIELDS: ClassVar[tuple[str, ...]] = (
        'supplier_name', 'quote_reference', 'quote_date', 'valid_until', 'currency', 'incoterms', 'payment_terms', 'stated_total')

    supplier_name: Evidenced[str] = Field(default_factory=Evidenced[str])
    quote_reference: Evidenced[str] = Field(default_factory=Evidenced[str])
    quote_date: Evidenced[str] = Field(default_factory=Evidenced[str])
    valid_until: Evidenced[str] = Field(default_factory=Evidenced[str])
    currency: Evidenced[str] = Field(default_factory=Evidenced[str])
    incoterms: Evidenced[str] = Field(default_factory=Evidenced[str])
    payment_terms: Evidenced[str] = Field(default_factory=Evidenced[str])
    stated_total: Evidenced[float] = Field(default_factory=Evidenced[float])  # the quote's total as printed
    line_items: list[QuoteLineItem] = Field(default_factory=list)

    def fields(self):
        for name in self.HEADER_FIELDS:
            yield name, getattr(self, name)
        for i, item in enumerate(self.line_items):
            for name in QuoteLineItem.model_fields:
                yield f'line_items[{i}].{name}', getattr(item, name)

    def field(self, name: str) -> Evidenced:
        """Look up a field by its golden name, e.g. 'line_items[0].unit_price'."""
        return dict(self.fields())[name]

    def safe_for_storage(self):
        clone = self.model_copy(deep=True)
        for _, field in clone.fields():
            if not field.grounded:
                field.value = None
        return clone

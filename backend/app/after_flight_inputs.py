"""Explicit operator bounds for a planned flight-to-airport pickup connection."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .live_data import identifier
from .models import Model
from .pricing import aware_timestamp


class AfterFlightTransferInput(Model):
    group_id: str = Field(min_length=1, max_length=80)
    via_airport: Literal["OTP"]
    destination_airport: Literal["CRA"]
    pickup_window_start: datetime
    pickup_window_end: datetime
    airport_processing_minutes: int = Field(ge=1, le=720, strict=True)
    suitcases: int = Field(ge=0, le=7, strict=True)
    handbags: int = Field(ge=0, le=7, strict=True)
    max_inbound_options: int = Field(default=2, ge=1, le=2, strict=True)

    @field_validator("group_id")
    @classmethod
    def exact_group(cls, value):
        if identifier(value) != value:
            raise ValueError("Use the exact selected passenger group identifier")
        return value

    @field_validator("pickup_window_start", "pickup_window_end", mode="before")
    @classmethod
    def explicit_instant(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("Supply a pickup window with explicit UTC offsets")
        value = aware_timestamp(value.isoformat() if isinstance(value, datetime) else value)
        if value.second or value.microsecond or value.astimezone(timezone.utc).second:
            raise ValueError("Supply whole-minute pickup window boundaries")
        return value

    @model_validator(mode="after")
    def ordered_window(self):
        if self.pickup_window_end < self.pickup_window_start:
            raise ValueError("The pickup window must end at or after its start")
        return self

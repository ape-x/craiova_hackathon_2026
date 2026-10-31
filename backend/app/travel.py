"""Dated travel-search input; quotes do not create reservations."""
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .airport_catalog import lookup_airport


class TravelSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    kind: Literal["flight", "bus"]
    origin_code: str = Field(min_length=3, max_length=4)
    destination_code: str = Field(min_length=3, max_length=4)
    departure_date: date
    adults: int = Field(default=1, ge=1, le=30, strict=True)
    passenger_ages: list[int] | None = Field(default=None, min_length=1, max_length=30)

    @field_validator("origin_code", "destination_code")
    @classmethod
    def known_airport(cls, value: str) -> str:
        airport = lookup_airport(value)
        if airport is None:
            raise ValueError("Select a known IATA or ICAO airport code")
        return airport["code"]

    @field_validator("passenger_ages", mode="before")
    @classmethod
    def strict_ages(cls, value):
        if value is not None and (not isinstance(value, list) or any(type(age) is not int or not 1 <= age <= 120 for age in value)):
            raise ValueError("Passenger ages must be integers between 1 and 120")
        return value

    @model_validator(mode="after")
    def valid_journey(self):
        today = datetime.now(ZoneInfo("Europe/Bucharest")).date()
        if self.origin_code == self.destination_code:
            raise ValueError("Origin and destination must differ")
        if not today <= self.departure_date <= today + timedelta(days=330):
            raise ValueError("Departure must be today or within the next 330 days")
        if self.passenger_ages is not None and len(self.passenger_ages) != self.adults:
            raise ValueError("Provide one age per requested passenger")
        return self

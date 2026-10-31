"""Dated travel-search input; quotes do not create reservations."""
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .airport_catalog import lookup_airport
from .hotel_location import coordinate_timezone, property_zone


class TravelSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    kind: Literal["flight", "bus"]
    origin_code: str = Field(min_length=3, max_length=4)
    destination_code: str = Field(min_length=3, max_length=4)
    departure_date: date
    adults: int = Field(default=1, ge=1, le=30, strict=True)
    passenger_ages: list[int] | None = Field(default=None, min_length=1, max_length=30)
    # Internal eligibility bounds; adapters do not add these to supplier bodies.
    earliest_departure_at: datetime | None = None
    latest_arrival_at: datetime | None = None

    @field_validator("earliest_departure_at", "latest_arrival_at", mode="before")
    @classmethod
    def aware_bound(cls, value):
        if value is None:
            return None
        if not isinstance(value, (str, datetime)):
            raise ValueError("Search time bounds require explicit timezone-aware instants")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Search time bounds require an explicit UTC offset")
        return parsed.astimezone(timezone.utc)

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
        airport = lookup_airport(self.origin_code)
        today = datetime.now(property_zone(coordinate_timezone(airport["lat"],airport["lon"]))).date()
        if self.origin_code == self.destination_code:
            raise ValueError("Origin and destination must differ")
        if not today <= self.departure_date <= today + timedelta(days=330):
            raise ValueError("Departure must be today or within the next 330 days")
        if self.passenger_ages is not None and len(self.passenger_ages) != self.adults:
            raise ValueError("Provide one age per requested passenger")
        if self.earliest_departure_at is not None and self.latest_arrival_at is not None and self.earliest_departure_at > self.latest_arrival_at:
            raise ValueError("The earliest departure exceeds the latest arrival")
        return self

from datetime import datetime, timezone

import pytest

from app.hotel_location import property_location, property_zone


def test_property_timezone_tracks_dst_and_actual_source_coordinates():
    node, context = property_location({"id": "property-1", "name": "Source property", "latitude": 51.47, "longitude": -0.45})
    assert node.kind == "hotel" and not node.diversion_eligible
    assert context["timezone"] == "Europe/London"
    zone = property_zone(context["timezone"])
    assert datetime(2027, 1, 15, tzinfo=timezone.utc).astimezone(zone).utcoffset().total_seconds() == 0
    assert datetime(2027, 7, 15, tzinfo=timezone.utc).astimezone(zone).utcoffset().total_seconds() == 3600
    assert context["timezone_dataset"] == "timezonefinder-data 4.2026.5"


@pytest.mark.parametrize("coordinates", [{}, {"latitude": True, "longitude": 23.8},
    {"latitude": float("nan"), "longitude": 23.8}, {"latitude": 91, "longitude": 0},
    {"latitude": 0, "longitude": -140}])
def test_invalid_or_ocean_property_coordinates_never_get_airport_fallback(coordinates):
    with pytest.raises(ValueError):
        property_location({"id": "property-1", "name": "Source property", **coordinates})

import pytest

from app.airport_catalog import nearby_airports


def test_lisbon_alternates_are_nearby_major_commercial_airports():
    result = nearby_airports("LPPT")
    assert {"FAO", "OPO"} <= {airport["id"] for airport in result}
    assert "LIS" not in {airport["id"] for airport in result}
    assert [a["distance_km"] for a in result] == sorted(a["distance_km"] for a in result)
    assert all(a["scheduled_service"] and a["type"] == "large_airport" for a in result)
    assert all("available" not in a for a in result)
    assert next(a["distance_km"] for a in result if a["id"] == "FAO") == pytest.approx(220, abs=10)


def test_nearby_retrieval_is_bounded_and_unknowns_do_not_get_geography():
    assert len(nearby_airports("LIS", limit=100)) <= 6
    assert nearby_airports("LIS", radius_km=10) == []
    assert nearby_airports("LIS", radius_km=float("nan")) == []
    assert nearby_airports("LIS", limit=0) == []
    assert nearby_airports("UNKNOWN") == []
    modified = nearby_airports("LIS")
    modified[0]["name"] = "changed"
    assert nearby_airports("LIS")[0]["name"] != "changed"

"""Replace these adapters with real provider/notifier implementations for a pilot."""
from typing import Protocol


class ProviderGateway(Protocol):
    def record_response(self, resource_id: str, response: str) -> dict: ...


class NotificationGateway(Protocol):
    def passenger_link(self, token: str) -> dict: ...


class SimulatedProviderGateway:
    def record_response(self, resource_id: str, response: str) -> dict:
        return {"resource_id": resource_id, "response": response, "simulation": True}


class SimulatedNotificationGateway:
    def passenger_link(self, token: str) -> dict:
        return {"url": f"/p/{token}", "delivered": False, "channel": "simulare"}

from fastapi.testclient import TestClient

from app.main import create_app
from app.recovery_jobs import RecoveryCancellation
from test_recovery_jobs import setup, request


def test_recovery_routes_are_authenticated_and_queue_is_idempotent(setup):
    settings,store,_,_,_=setup
    settings.operator_token="fixture-operator-secret"
    app=create_app(settings)
    # No lifespan: assert durable queue boundaries without dispatching a worker.
    client=TestClient(app)
    body=request(store).model_dump(mode="json")
    assert client.post("/api/incidents/CASE/recovery-jobs",json=body).status_code==401
    headers={"Authorization":"Bearer fixture-operator-secret"}
    started=client.post("/api/incidents/CASE/recovery-jobs",json=body,headers=headers)
    assert started.status_code==202
    job=started.json()["job"]
    assert job["status"]=="queued" and job["steps"]==job["gaps"]==[]
    replay=client.post("/api/incidents/CASE/recovery-jobs",json=body,headers=headers)
    assert replay.json()["replayed"] and replay.json()["job"]["id"]==job["id"]
    assert client.get(f"/api/recovery-jobs/{job['id']}").status_code==401
    assert client.get("/api/incidents/CASE/recovery-jobs",headers=headers).json()["jobs"][0]["id"]==job["id"]
    cancelled=client.post(f"/api/recovery-jobs/{job['id']}/cancel",json={"idempotency_key":"api-cancel-fixture"},headers=headers)
    assert cancelled.status_code==200 and cancelled.json()["job"]["status"]=="cancelled"
    assert cancelled.json()["job"]["calls_dispatched"]==0
    client.close()

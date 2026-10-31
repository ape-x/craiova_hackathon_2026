from datetime import datetime,timedelta,timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_live_api_generates_only_explicit_test_people_and_blocks_synthetic_resources(tmp_path):
    app=create_app(Settings(database_path=str(tmp_path/'live-api.sqlite3'),operation_mode='live',_env_file=None))
    with TestClient(app) as client:
        world=client.get('/api/world').json()
        assert world['mode']=='live' and not world['resources'] and not world['groups']
        now=datetime.now(timezone.utc)
        body={'expected_version':world['version'],'idempotency_key':'generate-live-people',
              'passengers':4,'group_size':2,'origin':'CRA','destination':'OTP',
              'original_flight_id':'TEST-REFERENCE','ready_at':(now+timedelta(minutes=5)).isoformat(),
              'deadline':(now+timedelta(days=1)).isoformat(),'group_budget_eur':'500.00',
              'accessible':False,'priority':1,'is_adult':True}
        generated=client.post('/api/live/generate-passengers',json=body)
        assert generated.status_code==200,generated.text
        world=generated.json()['world']
        assert sum(g['size'] for g in world['groups'])==4 and not world['resources']
        assert all(g['source']=='synthetic' and g['adult_count']==2 for g in world['groups'])
        assert client.post('/api/live/generate-passengers',json=body).json()['replayed'] is True
        fake_resource=client.post('/api/events',json={'expected_version':world['version'],'idempotency_key':'no-fake-resource',
            'type':'resource_capacity','target_id':'B12','value':40})
        assert fake_resource.status_code==409
        search=client.post('/api/live/travel/search',json={'group_id':world['groups'][0]['id'],
            'kind':'flight','origin_code':'CRA','destination_code':'OTP','departure_date':(now+timedelta(days=1)).date().isoformat()})
        assert search.status_code==200 and search.json()['state']=='not_configured'
        after=client.get('/api/world').json()
        assert after['version']==world['version'] and after['resources']==[]
        assert client.post('/api/reset',json={'expected_version':world['version'],'idempotency_key':'no-live-demo-reset'}).status_code==409


def test_live_imports_are_operator_protected(tmp_path):
    app=create_app(Settings(database_path=str(tmp_path/'auth.sqlite3'),operation_mode='live',operator_token='x'*40,_env_file=None))
    with TestClient(app) as client:
        query={'group_id':'NO-GROUP','kind':'flight','origin_code':'CRA','destination_code':'OTP','departure_date':datetime.now(timezone.utc).date().isoformat()}
        assert client.post('/api/live/travel/search',json=query).status_code==401
        config=client.get('/api/config').json()
        assert config['operation_mode']=='live' and config['simulation_only'] is False
        assert config['travel_sources']['coaches']['configured'] is False

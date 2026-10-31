"""Atomic supersession/dispatch races; isolated SQLite, no external calls."""
import pytest

from app.recovery_jobs import RecoveryCancellation
from app.recovery_ledger import RecoveryLedger
from app.store import DomainError
from test_recovery_jobs import setup, request


def successor(jobs, store, key, group_ids=None):
    req = request(store, idempotency_key=key, group_ids=group_ids or ['G1'])
    with store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        world = store._load(conn)
        return jobs.start_in_transaction(conn, world, 'CASE',
            req, supersede_active=True)['job']


def old_running(jobs, store):
    jobs.start('CASE', request(store))
    old, owner = jobs.ledger.claim_next()
    step = jobs.ledger.dispatch(old['id'], owner, 'searching_flights', 'Fixture', {'query': 1}, ['G1'])
    return old, owner, step


def settle(jobs, old, owner, step, *, unknown=False):
    jobs.ledger.outcome(old['id'], owner, step, 'unknown' if unknown else 'succeeded', 'Fixture outcome')
    jobs.ledger.finish(old['id'], owner, 'unknown_outcome' if unknown else 'cancelled', 'Fixture drain ended')


def test_replaces_queued_attempt_without_touching_independent_groups_or_resetting_limits(setup):
    _, store, jobs, _, _ = setup
    first = jobs.start('CASE', request(store))['job']
    independent = jobs.start('CASE', request(store, idempotency_key='independent-job', group_ids=['G2']))['job']
    before = store.read().model_dump(exclude={'sim_time'})
    new = successor(jobs, store, 'new-report-request')
    assert new['supersedes_job_ids'] == [first['id']]
    assert new['calls_dispatched'] == 0 and new['max_provider_calls'] == first['max_provider_calls']
    prior = jobs.get(first['id'])['job']
    assert prior['status'] == 'cancelled' and prior['superseded_by_job_id'] == new['id']
    assert jobs.get(independent['id'])['job'] == independent
    assert store.read().model_dump(exclude={'sim_time'}) == before
    assert successor(jobs, store, 'new-report-request') == new
    assert len(jobs.list('CASE')['jobs']) == 3
    with pytest.raises(DomainError) as conflict:
        jobs.start('CASE', request(store, idempotency_key='ordinary-conflicting-job'))
    assert conflict.value.details['code'] == 'recovery_group_active'
    assert jobs.ledger.claim_next()[0]['id'] == independent['id']


def test_running_predecessor_drains_without_losing_ownership_and_next_dispatch_is_refused(setup):
    _, store, jobs, _, _ = setup
    old, owner, step = old_running(jobs, store)
    new = successor(jobs, store, 'new-report-request')
    assert new['status'] == 'queued' and new['stage'] == 'waiting_for_previous'
    assert jobs.get(old['id'])['job']['status'] == 'running'
    assert jobs.ledger.claim_next() is None
    with pytest.raises(DomainError, match='cancellation'):
        jobs.ledger.dispatch(old['id'], owner, 'deciding', 'Fixture', {'query': 2}, ['G1'])
    settle(jobs, old, owner, step)
    claimed, _ = jobs.ledger.claim_next()
    assert claimed['id'] == new['id'] and claimed['calls_dispatched'] == 0
    assert jobs.get(old['id'])['job']['calls_dispatched'] == 1


@pytest.mark.parametrize('terminal', ['unknown_outcome', 'cancelled', 'completed', 'needs_review'])
def test_unknown_step_requires_review_even_when_predecessor_terminal_status_is_otherwise_safe(setup, terminal):
    _, store, jobs, _, _ = setup
    old, owner, step = old_running(jobs, store)
    new = successor(jobs, store, 'new-report-request')
    jobs.ledger.outcome(old['id'], owner, step, 'unknown', 'Fixture optional-source uncertainty')
    jobs.ledger.update(old['id'], owner, status=terminal, stage='finished')
    assert jobs.ledger.claim_next() is None
    result = jobs.get(new['id'])['job']
    assert result['status'] == 'needs_review' and result['calls_dispatched'] == 0
    assert result['steps'] == [] and result['blocking_job_ids'] == [old['id']]


def test_rapid_reports_carry_unknown_ancestor_through_cancelled_intermediate(setup):
    _, store, jobs, _, _ = setup
    old, owner, step = old_running(jobs, store)
    middle = successor(jobs, store, 'middle-report-request')
    settle(jobs, old, owner, step, unknown=True)
    latest = successor(jobs, store, 'latest-report-request')
    assert latest['supersedes_job_ids'] == [middle['id']]
    assert jobs.get(middle['id'])['job']['status'] == 'cancelled'
    assert old['id'] in jobs.ledger.get(latest['id'], private=True)['_handoff_dependencies']
    assert jobs.ledger.claim_next() is None
    assert jobs.get(latest['id'])['job']['status'] == 'needs_review'
    assert jobs.get(latest['id'])['job']['blocking_job_ids'] == [old['id']]


def test_many_known_cancelled_intermediates_do_not_grow_unresolved_ancestry(setup):
    _, store, jobs, _, _ = setup
    old, owner, step = old_running(jobs, store)
    for index in range(70):
        new = successor(jobs, store, f'rapid-report-{index}')
        assert len(jobs.ledger.get(new['id'], private=True)['_handoff_dependencies']) <= 2
    settle(jobs, old, owner, step)
    assert jobs.ledger.claim_next()[0]['id'] == new['id']


def test_missing_predecessor_history_and_lease_loss_block_successor_after_restart(setup):
    _, store, jobs, _, _ = setup
    old, _, _ = old_running(jobs, store)
    new = successor(jobs, store, 'new-report-request')
    with store.connect() as conn:
        conn.execute('UPDATE recovery_jobs SET lease_until=0 WHERE id=?', (old['id'],))
    restarted = RecoveryLedger(store, 90)
    assert restarted.claim_next() is None
    assert restarted.get(old['id'])['status'] == 'unknown_outcome'
    assert restarted.get(new['id'])['status'] == 'needs_review'
    with store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        record = restarted._load(conn, new['id'])
        record.update(status='queued', _handoff_dependencies=['JOB-MISSING'])
        restarted._save(conn, record)
    assert restarted.claim_next() is None
    assert restarted.get(new['id'])['calls_dispatched'] == 0


def test_cancelling_successor_does_not_revive_prior_request(setup):
    _, store, jobs, _, _ = setup
    old, owner, step = old_running(jobs, store)
    new = successor(jobs, store, 'new-report-request')
    jobs.cancel(new['id'], RecoveryCancellation(idempotency_key='cancel-new-request'))
    settle(jobs, old, owner, step)
    assert jobs.ledger.claim_next() is None
    assert jobs.get(new['id'])['job']['status'] == 'cancelled'
    assert jobs.get(old['id'])['job']['cancel_requested']


def test_creation_failure_rolls_back_predecessor_cancellations(setup):
    _, store, jobs, _, _ = setup
    old, _, _ = old_running(jobs, store)
    before = jobs.get(old['id'])['job']
    req = request(store, idempotency_key='rollback-report')
    with pytest.raises(RuntimeError):
        with store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            jobs.start_in_transaction(conn, store._load(conn), 'CASE', req, supersede_active=True)
            raise RuntimeError('Fixture command receipt failure')
    assert jobs.get(old['id'])['job'] == before
    assert len(jobs.list('CASE')['jobs']) == 1


def test_dispatch_rechecks_world_inside_its_transaction_before_recording_any_call(setup):
    _, store, jobs, _, _ = setup
    jobs.start('CASE', request(store))
    old, owner = jobs.ledger.claim_next()
    version = store.read().version
    store.mutate('fixture-new-report', {}, version, lambda world: setattr(world.groups[0], 'ready_at', world.groups[0].ready_at + 1))
    with pytest.raises(DomainError, match='Operation changed before dispatch'):
        jobs.ledger.dispatch(old['id'], owner, 'searching_flights', 'Fixture', {}, ['G1'], expected_version=version)
    recorded = jobs.get(old['id'])['job']
    assert recorded['calls_dispatched'] == 0 and recorded['steps'] == []


@pytest.mark.asyncio
async def test_report_between_scope_read_and_dispatch_cannot_issue_a_provider_call(setup, monkeypatch):
    _, store, jobs, _, _ = setup
    jobs.start('CASE', request(store))
    old, owner = jobs.ledger.claim_next()
    old['_planning_version'] = store.read().version
    original = jobs.ledger.dispatch
    def raced_dispatch(*args, **kwargs):
        store.mutate('fixture-racing-report', {}, old['_planning_version'],
            lambda world: setattr(world.groups[0], 'ready_at', world.groups[0].ready_at + 1))
        return original(*args, **kwargs)
    monkeypatch.setattr(jobs.ledger, 'dispatch', raced_dispatch)
    calls = []
    async def source():
        calls.append(True)
        return {}
    with pytest.raises(DomainError, match='Operation changed before dispatch'):
        await jobs._call(old, owner, 'searching_flights', 'Fixture', {}, ['G1'], source)
    assert calls == [] and jobs.get(old['id'])['job']['steps'] == []


def test_queue_limit_counts_surviving_jobs_and_does_not_cancel_when_full(setup):
    _, store, jobs, _, _ = setup
    def expand(world):
        template = world.groups[0]
        for index in range(3, 21):
            group_id = f'G{index}'
            world.groups.append(template.model_copy(deep=True, update={'id': group_id, 'token': template.token + group_id}))
            world.incidents[0].group_ids.append(group_id)
    store.mutate('fixture-twenty-groups', {}, store.read().version, expand)
    old, owner, step = old_running(jobs, store)
    others = [jobs.start('CASE', request(store, idempotency_key=f'full-queue-{index}', group_ids=[f'G{index}']))['job'] for index in range(2, 21)]
    before = jobs.get(old['id'])['job']
    with pytest.raises(DomainError) as full:
        successor(jobs, store, 'new-report-no-free-slot')
    assert full.value.details['code'] == 'recovery_queue_full'
    assert jobs.get(old['id'])['job'] == before
    new = successor(jobs, store, 'new-report-replaces-queued-slot', ['G2'])
    assert new['supersedes_job_ids'] == [others[0]['id']]
    assert new['stage'] == 'queued'
    assert jobs.get(others[0]['id'])['job']['status'] == 'cancelled'
    assert sum(j['status'] in ('queued', 'running') for j in jobs.list('CASE')['jobs']) == 20


def test_ancestry_limit_fails_before_cancelling_or_inserting(setup):
    _, store, jobs, _, _ = setup
    old, _, _ = old_running(jobs, store)
    with store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        record = jobs.ledger._load(conn, old['id'])
        record['_handoff_dependencies'] = [f'JOB-MISSING-{i}' for i in range(65)]
        jobs.ledger._save(conn, record)
    before = jobs.get(old['id'])['job']
    with pytest.raises(DomainError) as excessive:
        successor(jobs, store, 'new-report-excessive-ancestry')
    assert excessive.value.details['code'] == 'recovery_handoff_limit'
    assert jobs.get(old['id'])['job'] == before and len(jobs.list('CASE')['jobs']) == 1


def test_backup_preserves_dependency_history_without_resuming_historical_requests(setup, tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    from app.store import Store
    _, store, jobs, _, _ = setup
    old, _, _ = old_running(jobs, store)
    new = successor(jobs, store, 'new-report-before-backup')
    target = tmp_path / 'backup.sqlite3'
    subprocess.run([sys.executable, str(Path(__file__).resolve().parents[2] / 'scripts/backup.py'), store.path, str(target)], check=True, capture_output=True)
    restored = RecoveryLedger(Store(str(target)), 90)
    assert restored.claim_next() is None
    assert restored.get(new['id'])['status'] == 'needs_review'
    assert restored.get(new['id'], private=True)['_handoff_dependencies'] == [old['id']]
    assert restored.get(old['id'])['status'] == 'unknown_outcome'
    assert restored.get(old['id'])['superseded_by_job_id'] == new['id']
    assert jobs.get(old['id'])['job']['status'] == 'running'

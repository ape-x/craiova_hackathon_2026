import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, request, TransportError } from './api';
import type { World } from './types';

afterEach(() => vi.unstubAllGlobals());
describe('mutation transport boundary', () => {
  it('retries a live import with its original quote and distinguishes a definitive rejection', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response('upstream interrupted', { status: 502 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            detail: 'Quote expired; search again',
            applied: false,
          }),
          { status: 409 },
        ),
      );
    vi.stubGlobal('fetch', fetch);
    const input = { search_id: 'SEARCH-1', offer_id: 'QUOTE-1', group_id: 'FAMILY-1' };
    let failure: unknown;
    try {
      await api.liveInput({ version: 9 } as World, 'travel/import', input);
    } catch (cause) {
      failure = cause;
    }
    expect(failure).toBeInstanceOf(TransportError);
    input.offer_id = 'QUOTE-2';
    const retry = (failure as TransportError).retry();
    await expect(retry).rejects.toBeInstanceOf(ApiError);
    await expect(retry).rejects.toMatchObject({
      status: 409,
      message: 'Quote expired; search again',
    });
    expect(fetch.mock.calls[1][0]).toBe('/api/live/travel/import');
    expect(fetch.mock.calls[1][1].body).toBe(fetch.mock.calls[0][1].body);
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toMatchObject({
      expected_version: 9,
      offer_id: 'QUOTE-1',
      group_id: 'FAMILY-1',
    });
  });
  it('sends a clarification reply separately from prior commands and verified results', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ applied: false })));
    vi.stubGlobal('fetch', fetch);
    const context = 'Cerere anterioară: adaugă un autocar. Backend: câte locuri?';
    await api.command({ version: 7 } as World, '52 de locuri, pleacă la 18:00.', context);
    const body = JSON.parse(fetch.mock.calls[0][1].body);
    expect(body.text).toBe('52 de locuri, pleacă la 18:00.');
    expect(body.context).toBe(context);
    expect(body.expected_version).toBe(7);
    expect(body.idempotency_key).toEqual(expect.any(String));
  });
  it('recovers a truncated successful response without changing the original payload', async () => {
    const payload = { expected_version: 7, idempotency_key: 'request-truncated-123', value: 13 };
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response('{"world":', { status: 200 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ replayed: true, world: { version: 8 } }), { status: 200 }),
      );
    vi.stubGlobal('fetch', fetch);
    let failure: unknown;
    try {
      await request('/events', payload);
    } catch (error) {
      failure = error;
    }
    expect(failure).toBeInstanceOf(TransportError);
    payload.value = 99;
    await (failure as TransportError).retry();
    expect(fetch.mock.calls[0][1].body).toBe(fetch.mock.calls[1][1].body);
    expect(JSON.parse(fetch.mock.calls[1][1].body).value).toBe(13);
  });
  it('preserves recoverability when a proxy fails without confirming a mutation outcome', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('temporarily unavailable', {
          status: 503,
          statusText: 'Service Unavailable',
        }),
      ),
    );
    await expect(
      request('/replan', { idempotency_key: 'proxy-rejected-123' }),
    ).rejects.toBeInstanceOf(TransportError);
  });
  it('recovers an uncertain mutation with the original version and idempotency key', async () => {
    const payload = {
      expected_version: 14,
      idempotency_key: 'request-stable-123',
      type: 'resource_unavailable',
      target_id: 'B12',
      value: null,
    };
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('connection lost'))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ replayed: true, world: { version: 15 } }), { status: 200 }),
      );
    vi.stubGlobal('fetch', fetch);
    let failure: unknown;
    try {
      await request('/events', payload);
    } catch (error) {
      failure = error;
    }
    expect(failure).toBeInstanceOf(TransportError);
    const recovered = await (failure as TransportError).retry<{
      replayed: boolean;
      world: { version: number };
    }>();
    expect(recovered).toEqual({ replayed: true, world: { version: 15 } });
    expect(fetch.mock.calls[0][1].body).toBe(fetch.mock.calls[1][1].body);
    expect(JSON.parse(fetch.mock.calls[1][1].body).idempotency_key).toBe(payload.idempotency_key);
  });
  it('keeps a server rejection distinct from an uncertain network outcome', async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'World changed; refresh before planning' }), {
        status: 409,
      }),
    );
    vi.stubGlobal('fetch', fetch);
    const outcome = request('/replan', {
      mode: 'ai',
      expected_version: 1,
      idempotency_key: 'request-stale-123',
    });
    await expect(outcome).rejects.toMatchObject({
      name: 'ApiError',
      status: 409,
      message: 'World changed; refresh before planning',
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('recovers an in-progress AI request with its original key instead of issuing a new decision', async () => {
    const payload = { expected_version: 7, idempotency_key: 'decision-in-progress', mode: 'ai' };
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            detail:
              'This request is still processing. Retry the same request ID after it finishes.',
            applied: null,
            details: { in_progress: true, retry_after_seconds: 2 },
          }),
          { status: 503, headers: { 'Retry-After': '2' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ replayed: true, world: { version: 8 } })),
      );
    vi.stubGlobal('fetch', fetch);
    let pending: unknown;
    try {
      await request('/replan', payload);
    } catch (error) {
      pending = error;
    }
    expect(pending).toBeInstanceOf(TransportError);
    expect(fetch).toHaveBeenCalledTimes(1);
    await expect((pending as TransportError).retry()).resolves.toMatchObject({ replayed: true });
    expect(fetch.mock.calls[1][1].body).toBe(fetch.mock.calls[0][1].body);
  });
  it('does not retry a read intentionally cancelled by its owner', async () => {
    const controller = new AbortController();
    controller.abort();
    const aborted = new DOMException('Aborted', 'AbortError');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(aborted));
    await expect(request('/world', undefined, controller.signal)).rejects.toBe(aborted);
  });
  it('surfaces missing provider access rather than returning a synthetic success', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ detail: 'OpenAI API key is not configured', applied: false }),
            { status: 503 },
          ),
        ),
    );
    await expect(
      request('/commands', {
        text: 'Change the budget to 3200',
        expected_version: 1,
        idempotency_key: 'no-key-1234',
      }),
    ).rejects.toBeInstanceOf(ApiError);
  });
});

import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Login from './Login';
import { api } from './api';

afterEach(() => vi.unstubAllGlobals());

describe('operator sign in', () => {
  it('renders labelled username and password fields with password manager support', () => {
    const html = renderToStaticMarkup(<Login onSuccess={() => undefined} />);
    expect(html).toContain('Username');
    expect(html).toContain('name="username"');
    expect(html).toContain('autoComplete="username"');
    expect(html).toContain('type="password"');
    expect(html).toContain('autoComplete="current-password"');
    expect(html).not.toContain('Operator access token');
  });

  it('uses credentials only in the POST body and keeps the session in cookies', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ authenticated: true })));
    vi.stubGlobal('fetch', fetch);
    await api.login('operator', 'test-only-password');
    expect(fetch).toHaveBeenCalledWith(
      '/api/auth/login',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        body: JSON.stringify({ username: 'operator', password: 'test-only-password' }),
      }),
    );
  });

  it('surfaces invalid credentials without retrying silently', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ detail: 'Invalid username or password' }), { status: 401 }),
      );
    vi.stubGlobal('fetch', fetch);
    await expect(api.login('operator', 'incorrect')).rejects.toMatchObject({
      status: 401,
      message: 'Invalid username or password',
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('keeps existing token-only deployments accessible', () => {
    const html = renderToStaticMarkup(<Login method="token" onSuccess={() => undefined} />);
    expect(html).toContain('Operator access token');
    expect(html).not.toContain('name="username"');
  });
});

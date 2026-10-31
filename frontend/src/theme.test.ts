/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { describe, expect, it, vi } from 'vitest';
import { createThemeStore, THEME_KEY } from './theme';

function browser(saved: string | null = null, dark = false, storageBlocked = false) {
  const systemListeners = new Set<() => void>();
  const storageListeners = new Set<(event: StorageEvent) => void>();
  const media = {
    matches: dark,
    addEventListener: (_: string, fn: () => void) => systemListeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => systemListeners.delete(fn),
  };
  const localStorage = {
    getItem: vi.fn(() => {
      if (storageBlocked) throw new Error('Storage unavailable');
      return saved;
    }),
    setItem: vi.fn((_key: string, value: string) => {
      if (storageBlocked) throw new Error('Storage unavailable');
      saved = value;
    }),
  };
  const meta = { content: '', setAttribute: vi.fn() };
  const document = {
    documentElement: { dataset: {} as Record<string, string> },
    querySelector: () => meta,
  };
  const window = {
    localStorage,
    document,
    matchMedia: () => media,
    addEventListener: (_: string, fn: (event: StorageEvent) => void) => storageListeners.add(fn),
    removeEventListener: (_: string, fn: (event: StorageEvent) => void) =>
      storageListeners.delete(fn),
  };
  return {
    window: window as unknown as Window,
    document,
    media,
    localStorage,
    systemListeners,
    storageListeners,
    meta,
  };
}

describe('persistent theme integration', () => {
  it('follows the OS until an explicit choice is saved and synchronizes other tabs', () => {
    const env = browser();
    const store = createThemeStore(env.window);
    expect(store.getSnapshot()).toBe('light');
    const changed = vi.fn();
    const unsubscribe = store.subscribe(changed);
    env.media.matches = true;
    env.systemListeners.forEach((listener) => listener());
    expect(store.getSnapshot()).toBe('dark');
    store.toggle();
    expect(env.localStorage.setItem).toHaveBeenCalledWith(THEME_KEY, 'light');
    env.systemListeners.forEach((listener) => listener());
    expect(store.getSnapshot()).toBe('light');
    env.storageListeners.forEach((listener) =>
      listener({ key: THEME_KEY, newValue: 'dark' } as StorageEvent),
    );
    expect(store.getSnapshot()).toBe('dark');
    expect(env.document.documentElement.dataset.theme).toBe('dark');
    unsubscribe();
    expect(env.systemListeners.size).toBe(0);
    expect(env.storageListeners.size).toBe(0);
  });
  it('retains saved preferences and works when storage is blocked', () => {
    expect(createThemeStore(browser('dark', false).window).getSnapshot()).toBe('dark');
    const store = createThemeStore(browser(null, false, true).window);
    store.toggle();
    expect(store.getSnapshot()).toBe('dark');
  });
  it('applies the same first-paint preference using a self-hosted script allowed by the CSP', () => {
    const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
    const script = readFileSync(new URL('../public/theme-init.js', import.meta.url), 'utf8');
    expect(html).toContain('<script src="/theme-init.js"></script>');
    expect(html).not.toMatch(/<script>\s*\S/);
    for (const [saved, dark, blocked, expected] of [
      ['dark', false, false, 'dark'],
      ['light', true, false, 'light'],
      [null, true, true, 'dark'],
      [null, false, true, 'light'],
    ] as const) {
      const env = browser(saved, dark, blocked);
      runInNewContext(script, {
        localStorage: env.localStorage,
        document: env.document,
        matchMedia: () => env.media,
      });
      expect(env.document.documentElement.dataset.theme).toBe(expected);
      expect(env.meta.content).toBe(expected === 'dark' ? '#171717' : '#f8f8f8');
      expect(createThemeStore(env.window).getSnapshot()).toBe(expected);
    }
  });
});

import { useSyncExternalStore } from 'react';

export type Theme = 'light' | 'dark';
export const THEME_KEY = 'recovery-theme';
const isTheme = (value: string | null): value is Theme => value === 'light' || value === 'dark';

/** A single preference shared by the workspace, boarding pages and Three canvas. */
export function createThemeStore(browser: Window) {
  const system = browser.matchMedia('(prefers-color-scheme: dark)');
  let preference: Theme | null = null;
  try {
    const saved = browser.localStorage.getItem(THEME_KEY);
    if (isTheme(saved)) preference = saved;
  } catch {
    // Private browsing can disable storage. The toggle still works for this visit.
  }
  let current: Theme = preference ?? (system.matches ? 'dark' : 'light');
  const listeners = new Set<() => void>();
  const apply = () => {
    current = preference ?? (system.matches ? 'dark' : 'light');
    browser.document.documentElement.dataset.theme = current;
    browser.document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute('content', current === 'dark' ? '#171717' : '#f8f8f8');
    listeners.forEach((listener) => listener());
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key !== THEME_KEY && event.key !== null) return;
    preference = isTheme(event.newValue) ? event.newValue : null;
    apply();
  };
  apply();
  return {
    getSnapshot: () => current,
    subscribe(listener: () => void) {
      if (listeners.size === 0) {
        system.addEventListener('change', apply);
        browser.addEventListener('storage', onStorage);
        // Catch an OS preference change while no components were subscribed.
        apply();
      }
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
        if (!listeners.size) {
          system.removeEventListener('change', apply);
          browser.removeEventListener('storage', onStorage);
        }
      };
    },
    toggle() {
      preference = current === 'dark' ? 'light' : 'dark';
      try {
        browser.localStorage.setItem(THEME_KEY, preference);
      } catch {
        // Keep the in-memory preference when persistence is unavailable.
      }
      apply();
    },
  };
}

const store = typeof window === 'undefined' ? null : createThemeStore(window);
export function useTheme() {
  return useSyncExternalStore(
    store?.subscribe ?? (() => () => {}),
    store?.getSnapshot ?? (() => 'light' as const),
    () => 'light' as const,
  );
}
export const toggleTheme = () => store?.toggle();

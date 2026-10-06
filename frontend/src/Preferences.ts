export type Theme = 'auto' | 'light' | 'dark';
export type InventoryView = 'gallery' | 'list' | 'tags';

export function storedChoice<T extends string>(key: string, choices: readonly T[], fallback: T): T {
  try { const value = localStorage.getItem(key); return choices.includes(value as T) ? value as T : fallback; }
  catch { return fallback; }
}
export function storedFlag(key: string, fallback = false): boolean {
  try { const value = localStorage.getItem(key); return value === null ? fallback : value === 'true'; }
  catch { return fallback; }
}
export function systemIsDark(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-color-scheme: dark)').matches;
}
export function motionIsReduced(): boolean {
  return document.documentElement.dataset.motion === 'reduced'
    || typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

import { useEffect, useRef } from 'react';
import type { RefObject } from 'react';
import type { Filter } from './api';
import type { WorkDetailHandle } from './components';

export type Page = 'inventory' | 'detail' | 'issues' | 'jobs' | 'settings' | 'tags' | 'calendar' | 'profile';
export interface Route { page: Page; filter: Filter; workId: number | null }
export interface InventoryPosition {
  filter: Filter; query: string; search: string; number: number; issuesOnly: boolean;
  tagId: number | null; untaggedOnly: boolean; view: 'gallery' | 'list' | 'tags';
  scrollY: number; focusWorkId?: number;
  tagIds?: number[]; sortPlatform?: 'es' | 'patreon'; sortDirection?: 'asc' | 'desc';
}
export interface NavigationEntry {
  index: number; inventory?: InventoryPosition; returnInventory?: InventoryPosition;
  returnHash?: string; fromIndex?: number;
}
const KEY = 'workbenchNavigation';
const filters: Filter[] = ['all', 'pending', 'published', 'to_make', 'es_published', 'patreon_published'];

export function routeFromHash(hash: string): Route {
  const path = hash.replace(/^#\/?/, '');
  const work = /^works\/([1-9]\d*)$/.exec(path);
  if (work && Number.isSafeInteger(Number(work[1]))) return { page: 'detail', filter: 'all', workId: Number(work[1]) };
  if (filters.includes(path as Filter) && path !== 'all') return { page: 'inventory', filter: path as Filter, workId: null };
  if (['issues', 'jobs', 'settings', 'tags', 'calendar', 'profile'].includes(path)) return { page: path as Page, filter: 'all', workId: null };
  return { page: 'inventory', filter: 'all', workId: null };
}

function position(value: unknown): InventoryPosition | undefined {
  if (!value || typeof value !== 'object') return;
  const item = value as InventoryPosition;
  if (!filters.includes(item.filter) || typeof item.query !== 'string' || typeof item.search !== 'string'
      || !Number.isSafeInteger(item.number) || item.number < 1 || typeof item.issuesOnly !== 'boolean'
      || !(item.tagId === null || Number.isSafeInteger(item.tagId) && item.tagId > 0)
      || typeof item.untaggedOnly !== 'boolean' || !['gallery', 'list', 'tags'].includes(item.view)
      || !Number.isFinite(item.scrollY) || item.scrollY < 0) return;
  return { filter: item.filter, query: item.query, search: item.search, number: item.number,
    issuesOnly: item.issuesOnly, tagId: item.tagId, untaggedOnly: item.untaggedOnly, view: item.view,
    scrollY: item.scrollY,
    ...(Array.isArray(item.tagIds) ? { tagIds: item.tagIds.filter(id => Number.isSafeInteger(id) && id > 0).slice(0, 100) } : {}),
    ...(['es', 'patreon'].includes(item.sortPlatform || '') ? { sortPlatform: item.sortPlatform } : {}),
    ...(['asc', 'desc'].includes(item.sortDirection || '') ? { sortDirection: item.sortDirection } : {}),
    ...(Number.isSafeInteger(item.focusWorkId) && item.focusWorkId! > 0 ? { focusWorkId: item.focusWorkId } : {}) };
}

function entry(): NavigationEntry | undefined {
  const saved = window.history.state?.[KEY];
  if (!saved || !Number.isSafeInteger(saved.index)) return;
  return { index: saved.index, inventory: position(saved.inventory), returnInventory: position(saved.returnInventory),
    ...(typeof saved.returnHash === 'string' && /^#\/?(?:inventory|pending|published|to_make|es_published|patreon_published|calendar|profile|jobs|issues|tags)$/.test(saved.returnHash)
      ? { returnHash: saved.returnHash } : {}),
    ...(Number.isSafeInteger(saved.fromIndex) ? { fromIndex: saved.fromIndex } : {}) };
}

export function initialNavigation() {
  const route = routeFromHash(window.location.hash);
  const saved = entry();
  return { route, inventory: route.page === 'detail' ? saved?.returnInventory : saved?.inventory };
}

/** Hash history keeps list context in this tab's history, never in the workbench database. */
export function useWorkbenchNavigation({ getInventory, onRoute, detailRef }: {
  getInventory: () => InventoryPosition;
  onRoute: (route: Route, saved: NavigationEntry) => void;
  detailRef: RefObject<WorkDetailHandle | null>;
}) {
  const callbacks = useRef({ getInventory, onRoute });
  callbacks.current = { getInventory, onRoute };
  const current = useRef({ hash: window.location.hash || '#/inventory', saved: entry() || { index: 0 } });
  const restoring = useRef<{ hash: string; saved: NavigationEntry; delta: number } | null>(null);
  const allowedPop = useRef<{ hash: string; saved: NavigationEntry } | null>(null);

  function replace(hash: string, saved: NavigationEntry) {
    window.history.replaceState({ ...window.history.state, [KEY]: saved }, '', hash);
  }
  function apply(hash: string, saved: NavigationEntry) {
    current.current = { hash, saved };
    callbacks.current.onRoute(routeFromHash(hash), saved);
    if (!saved.inventory || routeFromHash(hash).page !== 'inventory') window.scrollTo({ top: 0, behavior: 'auto' });
  }
  function leave(next: () => void) {
    if (routeFromHash(current.current.hash).page === 'detail' && detailRef.current) detailRef.current.requestLeave(next);
    else next();
  }
  function saveInventory(focusWorkId?: number) {
    const saved = current.current.saved;
    if (routeFromHash(current.current.hash).page !== 'inventory') return;
    const inventory = { ...callbacks.current.getInventory(), ...(focusWorkId ? { focusWorkId } : {}) };
    current.current.saved = { ...saved, inventory };
    replace(current.current.hash, current.current.saved);
    return inventory;
  }
  function push(hash: string, extras: Omit<NavigationEntry, 'index'> = {}) {
    restoring.current = null; allowedPop.current = null;
    const saved = { ...extras, index: current.current.saved.index + (hash === current.current.hash ? 0 : 1) };
    if (hash === current.current.hash) replace(hash, saved);
    else window.history.pushState({ [KEY]: saved }, '', hash);
    apply(hash, saved);
  }

  useEffect(() => {
    replace(current.current.hash, current.current.saved);
    const previousRestoration = window.history.scrollRestoration;
    window.history.scrollRestoration = 'manual';
    const read = () => {
      const hash = window.location.hash || '#/inventory';
      const raw = entry();
      const saved = raw || { index: current.current.saved.index + 1 };
      if (restoring.current) {
        if (hash === current.current.hash && saved.index === current.current.saved.index) {
          const target = restoring.current; restoring.current = null;
          // Restore the detail URL before asking, so cancelling leaves both URL and draft intact.
          leave(() => { allowedPop.current = target; window.history.go(target.delta); });
        }
        return;
      }
      if (allowedPop.current?.hash === hash && (!raw || allowedPop.current.saved.index === saved.index)) {
        const target = allowedPop.current; allowedPop.current = null;
        if (!raw) replace(hash, target.saved);
        apply(hash, target.saved); return;
      }
      if (hash === current.current.hash && saved.index === current.current.saved.index) return;
      if (routeFromHash(current.current.hash).page === 'detail') {
        const delta = saved.index - current.current.saved.index;
        if (delta) { restoring.current = { hash, saved, delta }; window.history.go(-delta); return; }
        replace(current.current.hash, current.current.saved);
        leave(() => { replace(hash, saved); apply(hash, saved); });
        return;
      }
      if (!raw) replace(hash, saved);
      apply(hash, saved);
    };
    window.addEventListener('popstate', read); window.addEventListener('hashchange', read);
    return () => {
      window.removeEventListener('popstate', read); window.removeEventListener('hashchange', read);
      window.history.scrollRestoration = previousRestoration;
    };
  }, []);

  return {
    returnPage: routeFromHash(current.current.saved.returnHash || '#/inventory').page,
    navigate(page: Exclude<Page, 'detail'>, filter: Filter = 'all') {
      leave(() => { saveInventory(); push(`#/${page === 'inventory' ? filter === 'all' ? 'inventory' : filter : page}`); });
    },
    openWork(workId: number) {
      const inventory = saveInventory(workId);
      push(`#/works/${workId}`, { returnInventory: inventory,
        returnHash: current.current.hash, fromIndex: current.current.saved.index });
    },
    backToInventory() {
      // WorkDetail's onClose is already approved by its own requestLeave guard.
      const saved = current.current.saved;
      if (saved.returnHash && saved.fromIndex === saved.index - 1) {
        allowedPop.current = { hash: saved.returnHash,
          saved: { index: saved.fromIndex, ...(saved.returnInventory ? { inventory: saved.returnInventory } : {}) } };
        window.history.back();
      } else push('#/inventory');
    },
  };
}

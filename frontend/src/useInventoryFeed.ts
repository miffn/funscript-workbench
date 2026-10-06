import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { SetStateAction } from 'react';
import { ApiError, errorMessage, request } from './api';
import type { Inventory } from './api';

type Snapshot = Inventory & { snapshot_id?: string; inventory_revision?: number };
type Entry = { data: Snapshot; pages: number; localRevision: number; stale?: boolean };
const SIZE = 24;

/** One list stays in App while detail routes are open; append reads the same server snapshot. */
export function useInventoryFeed(query: string, wantedPages: number, revision: number, active: boolean, initialSnapshotId?: string, scanActive = false) {
  const cache = useRef(new Map<string, Entry>());
  const seed = useRef({ query, id: initialSnapshotId });
  const [inventory, setData] = useState<Snapshot | null>(null);
  const [loadedKey, setLoadedKey] = useState('');
  const [loadedPages, setLoadedPages] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [appendError, setAppendError] = useState('');
  const [retry, setRetry] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const operation = useRef(false);
  const knownVersion = useRef<number | null>(null);
  const dataRef = useRef(inventory); dataRef.current = inventory;
  const lastRefresh = useRef(refresh);
  const anchor = useRef<{ query: string; id: string; top: number; scrollY: number } | null>(null);
  const effectivePages = active ? wantedPages : 1;
  useLayoutEffect(() => {
    if (!active) { anchor.current = null; return; }
    if (!anchor.current || loadingMore || loading || loadedKey !== anchor.current.query) return;
    const saved = anchor.current; anchor.current = null;
    const element = document.querySelector<HTMLElement>(`[data-inventory-work="${saved.id}"]`);
    window.scrollTo({ top: element ? window.scrollY + element.getBoundingClientRect().top - saved.top : saved.scrollY, behavior: 'auto' });
  }, [inventory, loadingMore, loading, loadedKey, active]);

  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    const existing = cache.current.get(query);
    const invalid = refresh !== lastRefresh.current || !!existing && (existing.stale || existing.localRevision !== revision || knownVersion.current !== null && existing.data.inventory_revision !== undefined && existing.data.inventory_revision !== knownVersion.current);
    if (existing && invalid) existing.stale = true;
    const mustRefresh = !!invalid && !scanActive;
    lastRefresh.current = refresh;
    const old = existing?.data;
    const rememberAnchor = () => {
      if (!active || loadedKey !== query || anchor.current) return;
      const element = [...document.querySelectorAll<HTMLElement>('[data-inventory-work]')].find(item => item.getBoundingClientRect().bottom > 0);
      if (element) anchor.current = { query, id: element.dataset.inventoryWork!, top: element.getBoundingClientRect().top, scrollY: window.scrollY };
    };
    if (mustRefresh) rememberAnchor();
    const show = (entry: Entry) => {
      const pages = Math.min(effectivePages, entry.pages, Math.max(1, Math.ceil(entry.data.total / SIZE)));
      setData({ ...entry.data, items: entry.data.items.slice(0, pages * SIZE), page: pages });
      setLoadedPages(pages); setLoadedKey(query); setLoading(false); setLoadingMore(false);
    };
    if (existing && !mustRefresh) {
      setError(''); setAppendError('');
      show(existing);
      if (existing.pages >= effectivePages || existing.data.items.length >= existing.data.total) return;
    }
    if (!active && existing && mustRefresh) return;
    const preserve = !!old || loadedKey === query && !!dataRef.current;
    if (!preserve) { setData(null); setLoadedKey(''); setLoadedPages(0); }
    setLoading(!preserve); setLoadingMore(preserve); setError(''); setAppendError('');
    operation.current = true;
    const load = async () => {
      let entry = !mustRefresh ? existing : undefined;
      const target = Math.max(effectivePages, mustRefresh ? existing?.pages || 1 : 1);
      let token = entry?.data.snapshot_id || (!existing && seed.current.query === query ? seed.current.id : undefined);
      let page = entry ? entry.pages + 1 : 1;
      let recovered = false;
      try {
        while (page <= target) {
          const params = new URLSearchParams(query);
          params.set('page', String(page)); params.set('page_size', String(SIZE));
          if (token) params.set('snapshot_id', token);
          let batch: Snapshot;
          try { batch = await request<Snapshot>(`/api/works?${params}`, { signal: controller.signal }); }
          catch (reason) {
            if (reason instanceof ApiError && reason.status === 410 && !recovered) {
              rememberAnchor();
              recovered = true; token = undefined; entry = undefined; page = 1; continue;
            }
            throw reason;
          }
          if (!alive) return;
          const items = new Map((entry?.data.items || []).map(work => [work.id, work]));
          for (const work of batch.items) items.set(work.id, work);
          entry = { data: { ...batch, items: [...items.values()] }, pages: page, localRevision: revision };
          token = batch.snapshot_id;
          knownVersion.current = batch.inventory_revision ?? knownVersion.current;
          if (!batch.items.length || page * SIZE >= batch.total) break;
          page += 1;
        }
        if (!alive || !entry) return;
        cache.current.delete(query); cache.current.set(query, entry);
        if (cache.current.size > 16) cache.current.delete(cache.current.keys().next().value!);
        show(entry);
      } catch (reason) {
        if (!alive || controller.signal.aborted) return;
        if (preserve) setAppendError(errorMessage(reason)); else setError(errorMessage(reason));
      } finally {
        if (alive) { operation.current = false; setLoading(false); setLoadingMore(false); }
      }
    };
    void load();
    return () => { alive = false; controller.abort(); operation.current = false; };
  }, [query, effectivePages, revision, retry, refresh, active, scanActive]);

  useEffect(() => {
    if (!active) return;
    let alive = true, checking = false;
    const controller = new AbortController();
    const check = async () => {
      if (checking || operation.current) return;
      checking = true;
      try {
        const state = await request<{ inventory_revision: number; scan_active: boolean }>('/api/inventory-revision', { signal: controller.signal });
        if (!alive || state.scan_active || !Number.isInteger(state.inventory_revision)) return;
        if (knownVersion.current !== null && knownVersion.current !== state.inventory_revision) {
          knownVersion.current = state.inventory_revision;
          setRefresh(value => value + 1);
        }
      } catch { /* Existing items remain usable while the connection is recovering. */ }
      finally { checking = false; }
    };
    void check(); const timer = setInterval(() => void check(), 10000);
    return () => { alive = false; controller.abort(); clearInterval(timer); };
  }, [active]);

  const setInventory = (update: SetStateAction<Inventory | null>) => {
    setData(previous => {
      const next = typeof update === 'function' ? update(previous) : update;
      if (next) {
        const entry = cache.current.get(query);
        if (entry) {
          const patches = new Map(next.items.map(work => [work.id, work]));
          entry.data = { ...entry.data, ...next, items: entry.data.items.map(work => patches.get(work.id) || work) };
        }
      }
      return next;
    });
  };
  return { inventory, setInventory, loadedKey, loadedPages, loading, loadingMore, error, appendError,
    hasMore: !!inventory && loadedPages * SIZE < inventory.total,
    retry: () => setRetry(value => value + 1), refresh: () => setRefresh(value => value + 1) };
}

// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Inventory, Work } from './api';
import { useInventoryFeed } from './useInventoryFeed';

type Pending = { path: string; signal?: AbortSignal | null; resolve: (response: Response) => void };
const rows = (start: number, count = 24): Work[] => Array.from({ length: count }, (_, index) => ({
  id: start + index, script_id: `S${start + index}`, title: `Work ${start + index}`, status: 'pending',
  video_count: 1, script_count: 1, directories: [], issues: [], cover_url: null, updated_at: '', tags: [],
}));
const batch = (start: number, page = 1, token = 'snapshot-a', version = 1, total = 72): Inventory => ({
  items: rows(start), total, page, page_size: 24, snapshot_id: token, inventory_revision: version,
  stats: { total, pending: total, published: 0, issues: 0 }, last_scan: null,
});
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
let pending: Pending[];
let revisionState: { inventory_revision: number; scan_active: boolean };
let fetchMock: ReturnType<typeof vi.fn<(path: string, options?: RequestInit) => Promise<Response>>>;
const listCalls = () => fetchMock.mock.calls.filter(([path]) => path.startsWith('/api/works?'));
const params = (index: number) => new URL(listCalls()[index][0], 'http://localhost').searchParams;
async function finish(index: number, body: unknown, status = 200) {
  await act(async () => { pending[index].resolve(response(body, status)); });
}
beforeEach(() => {
  pending = []; revisionState = { inventory_revision: 1, scan_active: false };
  fetchMock = vi.fn((path: string, options?: RequestInit) => path === '/api/inventory-revision'
    ? Promise.resolve(response(revisionState))
    : new Promise<Response>(resolve => pending.push({ path, signal: options?.signal, resolve })));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

describe('snapshot inventory feed', () => {
  it('preserves the visible work anchor when refreshed ordering changes', async () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, 'scrollY')!;
    Object.defineProperty(window, 'scrollY', { configurable: true, value: 200 });
    const scroll = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function(this: HTMLElement) {
      const index = [...document.querySelectorAll('[data-inventory-work]')].indexOf(this);
      return new DOMRect(0, index * 100 - 200, 200, 100);
    });
    function List() {
      const feed = useInventoryFeed('status=all', 1, 0, true);
      return <><button onClick={feed.refresh}>Refresh list</button>{feed.inventory?.items.map(work => <div key={work.id} data-inventory-work={work.id}>{work.title}</div>)}</>;
    }
    try {
      render(<List />); await finish(0, batch(1, 1, 'initial', 1, 24));
      fireEvent.click(screen.getByRole('button', { name: 'Refresh list' }));
      await finish(1, { ...batch(1, 1, 'updated', 2, 24), items: rows(1).reverse() });
      expect(scroll).toHaveBeenLastCalledWith({ top: 2100, behavior: 'auto' });
    } finally { Object.defineProperty(window, 'scrollY', descriptor); }
  });
  it('loads 24 and appends 24 with the same snapshot without duplicate batch requests', async () => {
    const { result, rerender } = renderHook(({ pages }) => useInventoryFeed('status=all', pages, 0, true), { initialProps: { pages: 1 } });
    expect(params(0).get('page')).toBe('1'); expect(params(0).get('page_size')).toBe('24');
    await finish(0, batch(1));
    expect(result.current.inventory?.items).toHaveLength(24);
    rerender({ pages: 2 }); rerender({ pages: 2 });
    expect(listCalls()).toHaveLength(2); expect(params(1).get('page')).toBe('2');
    expect(params(1).get('snapshot_id')).toBe('snapshot-a');
    expect(result.current.inventory?.items).toHaveLength(24);
    await finish(1, batch(25, 2));
    expect(result.current.inventory?.items.map(work => work.id)).toEqual(Array.from({ length: 48 }, (_, index) => index + 1));
    expect(result.current.loadedPages).toBe(2); expect(result.current.hasMore).toBe(true);
  });

  it('aborts the previous query and never mixes a late response into the new query', async () => {
    const { result, rerender } = renderHook(({ query }) => useInventoryFeed(query, 1, 0, true), { initialProps: { query: 'q=old' } });
    rerender({ query: 'q=new' });
    expect(pending[0].signal?.aborted).toBe(true);
    await finish(0, batch(1));
    expect(result.current.inventory).toBeNull();
    await finish(1, batch(101, 1, 'snapshot-new'));
    expect(result.current.loadedKey).toBe('q=new');
    expect(result.current.inventory?.items.map(work => work.id)).toEqual(rows(101).map(work => work.id));
    expect(listCalls()).toHaveLength(2);
  });

  it('retains loaded rows after append failure and retries the same snapshot page', async () => {
    const { result, rerender } = renderHook(({ pages }) => useInventoryFeed('status=all', pages, 0, true), { initialProps: { pages: 1 } });
    await finish(0, batch(1)); rerender({ pages: 2 });
    await finish(1, { detail: '第二批暂时无法读取' }, 503);
    expect(result.current.inventory?.items).toHaveLength(24);
    expect(result.current.loadedPages).toBe(1); expect(result.current.error).toBe('');
    expect(result.current.appendError).toBe('第二批暂时无法读取');
    act(() => result.current.retry());
    expect(params(2).get('page')).toBe('2'); expect(params(2).get('snapshot_id')).toBe('snapshot-a');
    await finish(2, batch(25, 2));
    expect(result.current.inventory?.items).toHaveLength(48); expect(result.current.appendError).toBe('');
  });

  it('does not carry a previous query failure into a different cached query', async () => {
    const { result, rerender } = renderHook(({ query, pages }) => useInventoryFeed(query, pages, 0, true), { initialProps: { query: 'q=a', pages: 1 } });
    await finish(0, batch(1));
    rerender({ query: 'q=b', pages: 1 }); await finish(1, batch(101, 1, 'snapshot-b'));
    rerender({ query: 'q=a', pages: 2 }); await finish(2, { detail: 'A 列表追加失败' }, 503);
    expect(result.current.appendError).toBe('A 列表追加失败');
    rerender({ query: 'q=b', pages: 1 });
    expect(result.current.inventory?.items[0].id).toBe(101);
    expect(result.current.appendError).toBe(''); expect(result.current.error).toBe('');
    expect(listCalls()).toHaveLength(3);
  });

  it('recovers a 410 by rebuilding the full requested range without mixing old and new snapshots', async () => {
    const { result, rerender } = renderHook(({ pages }) => useInventoryFeed('status=all', pages, 0, true), { initialProps: { pages: 1 } });
    await finish(0, batch(1)); rerender({ pages: 2 }); await finish(1, batch(25, 2));
    rerender({ pages: 3 }); await finish(2, { detail: '库存快照已过期' }, 410);
    expect(result.current.inventory?.items).toHaveLength(48);
    expect(params(3).get('page')).toBe('1'); expect(params(3).get('snapshot_id')).toBeNull();
    await finish(3, batch(101, 1, 'snapshot-b', 2));
    expect(params(4).get('page')).toBe('2'); expect(params(4).get('snapshot_id')).toBe('snapshot-b');
    await finish(4, batch(125, 2, 'snapshot-b', 2));
    await finish(5, batch(149, 3, 'snapshot-b', 2));
    expect(result.current.inventory?.items.map(work => work.id)).toEqual(Array.from({ length: 72 }, (_, index) => 101 + index));
    expect(result.current.inventory?.snapshot_id).toBe('snapshot-b');
    expect(result.current.loadedPages).toBe(3); expect(result.current.hasMore).toBe(false);
  });

  it('restores a saved snapshot range after refresh and rebuilds an expired saved token', async () => {
    const { result } = renderHook(() => useInventoryFeed('status=pending', 2, 0, true, 'saved-token'));
    expect(params(0).get('snapshot_id')).toBe('saved-token');
    await finish(0, { detail: '过期' }, 410);
    expect(params(1).get('snapshot_id')).toBeNull();
    await finish(1, batch(1, 1, 'restored-token'));
    await finish(2, batch(25, 2, 'restored-token'));
    expect(result.current.inventory?.items).toHaveLength(48);
    expect(params(2).get('snapshot_id')).toBe('restored-token');
  });

  it('returns from an inactive detail route using cached batches without repeating list requests', async () => {
    const { result, rerender } = renderHook(({ active }) => useInventoryFeed('status=all', 2, 0, active), { initialProps: { active: true } });
    await finish(0, batch(1)); await finish(1, batch(25, 2));
    rerender({ active: false }); rerender({ active: true });
    await waitFor(() => expect(result.current.inventory?.items).toHaveLength(48));
    expect(listCalls()).toHaveLength(2);
  });

  it('polls only the lightweight revision every 10 seconds and waits for active scans to finish', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useInventoryFeed('status=all', 1, 0, true));
    await finish(0, batch(1));
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(listCalls()).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([path]) => path === '/api/inventory-revision')).toHaveLength(1);
    revisionState = { inventory_revision: 2, scan_active: true };
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(listCalls()).toHaveLength(1);
    expect(result.current.inventory?.inventory_revision).toBe(1);
    revisionState.scan_active = false;
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(listCalls()).toHaveLength(2); expect(params(1).get('snapshot_id')).toBeNull();
    await finish(1, batch(101, 1, 'updated-token', 2));
    expect(result.current.inventory?.inventory_revision).toBe(2);
  });

  it('defers local revision refresh while scanActive is true, then refreshes once after completion', async () => {
    const { result, rerender } = renderHook(({ revision, scanning }) => useInventoryFeed('status=all', 1, revision, true, undefined, scanning), { initialProps: { revision: 0, scanning: false } });
    await finish(0, batch(1));
    rerender({ revision: 1, scanning: true });
    expect(listCalls()).toHaveLength(1); expect(result.current.inventory?.items).toHaveLength(24);
    rerender({ revision: 1, scanning: false });
    expect(listCalls()).toHaveLength(2);
    await finish(1, batch(101, 1, 'after-scan', 2));
    expect(result.current.inventory?.snapshot_id).toBe('after-scan');
  });
});

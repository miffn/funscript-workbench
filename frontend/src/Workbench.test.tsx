// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { setLanguage } from './i18n';
import type { Work } from './api';

const work: Work = { id: 1, script_id: 'S001', title: '作品', status: 'pending', cover_url: null,
  video_count: 1, script_count: 1, directories: [], issues: [], updated_at: '', tags: [] };
const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  localStorage.clear(); window.history.replaceState(null, '', '/#/inventory');
  setLanguage({ language: 'zh-CN', revision: 0 });
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value() { this.open = true; } });
  fetchMock = vi.fn(async (path: string) => {
    if (path.startsWith('/api/works?')) return response({ items: [work], total: 1, page: 1, page_size: 24,
      stats: { total: 1, pending: 1, to_make: 0, published: 0, es_published: 0, patreon_published: 0, issues: 0 }, last_scan: null });
    if (path === '/api/works/1') return response(work);
    if (path === '/api/works/1/preview-matching') return response({ work_id: 1, video_asset_id: null, mode: 'auto', revision: 0,
      script_asset_ids: {}, videos: [], scripts: [], issues: [], job: null, source_changed: false });
    if (path === '/api/works/1/preview') return response({ job: null, files: [], output_dir: '', windows_path: '' });
    if (path === '/api/profile') return response({ name: 'Miffn', bio: '个人简介', avatar: null, revision: 1 });
    if (path === '/api/capabilities') return response({ can_open_folder: false, reason: '' });
    if (path === '/api/settings') return response({ roots: [], scan_roots_revision: 0, scan_interval_seconds: 0 });
    if (path === '/api/mcp-auth') return response({ enabled: false, can_manage: false, revision: 0 });
    return response({ items: [] });
  });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.clear(); });
const inventoryParams = () => new URL(String(fetchMock.mock.calls.filter(([path]) => String(path).startsWith('/api/works?')).at(-1)?.[0]), 'http://localhost').searchParams;

it('sorts on the server before pagination and persists the selected platform and direction', async () => {
  render(<App />); await screen.findByRole('button', { name: '查看 S001 作品' });
  expect(inventoryParams().get('sort_platform')).toBe('es'); expect(inventoryParams().get('sort_direction')).toBe('desc');
  fireEvent.click(within(screen.getByLabelText('按平台发布日期排序')).getByRole('button', { name: 'Patreon' }));
  await waitFor(() => expect(inventoryParams().get('sort_platform')).toBe('patreon'));
  fireEvent.click(screen.getByRole('button', { name: '当前从新到旧，点击切换从旧到新' }));
  await waitFor(() => expect(inventoryParams().get('sort_direction')).toBe('asc'));
  expect(localStorage.getItem('workbench-sort-platform')).toBe('patreon');
});

it('appends exactly one snapshot batch when the bottom observer fires twice', async () => {
  let bottomCallback: IntersectionObserverCallback | undefined;
  let bottomObserver: IntersectionObserver | undefined;
  vi.stubGlobal('IntersectionObserver', class {
    constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
      if (options?.rootMargin === '0px 0px 160px 0px') { bottomCallback = callback; bottomObserver = this as unknown as IntersectionObserver; }
    }
    observe() {}
    unobserve() {}
    disconnect() {}
  });
  const first = Array.from({ length: 24 }, (_, index) => ({ ...work, id: index + 1, script_id: `S${String(index + 1).padStart(3, '0')}` }));
  const next = first.map(item => ({ ...item, id: item.id + 24, script_id: `S${String(item.id + 24).padStart(3, '0')}` }));
  const envelope = { total: 72, page_size: 24, snapshot_id: 'shared-snapshot', inventory_revision: 1,
    stats: { total: 72, pending: 72, to_make: 0, published: 0, issues: 0 }, last_scan: null };
  const originalFetch = fetchMock.getMockImplementation() as (path: string, options?: RequestInit) => Promise<Response>;
  let resolveNext: (response: Response) => void = () => {};
  fetchMock.mockImplementation((path: string, options?: RequestInit) => {
    if (path.startsWith('/api/works?')) {
      const page = new URL(path, 'http://localhost').searchParams.get('page');
      return page === '1' ? Promise.resolve(response({ ...envelope, items: first, page: 1 })) : new Promise<Response>(resolve => { resolveNext = resolve; });
    }
    if (path === '/api/inventory-revision') return Promise.resolve(response({ inventory_revision: 1, scan_active: false }));
    return originalFetch(path, options);
  });
  render(<App />);
  await screen.findByText('已加载 24 / 72 个库存');
  expect(bottomCallback).toBeDefined();
  act(() => {
    bottomCallback!([{ isIntersecting: true }] as IntersectionObserverEntry[], bottomObserver!);
    bottomCallback!([{ isIntersecting: true }] as IntersectionObserverEntry[], bottomObserver!);
  });
  await waitFor(() => expect(fetchMock.mock.calls.filter(([path]) => String(path).startsWith('/api/works?'))).toHaveLength(2));
  expect(inventoryParams().get('page')).toBe('2'); expect(inventoryParams().get('snapshot_id')).toBe('shared-snapshot');
  expect(screen.getByText('已加载 24 / 72 个库存')).toBeTruthy();
  await act(async () => resolveNext(response({ ...envelope, items: next, page: 2 })));
  await screen.findByText('已加载 48 / 72 个库存');
  expect(document.querySelectorAll('.work-card')).toHaveLength(48);
  expect(fetchMock.mock.calls.filter(([path]) => String(path).startsWith('/api/works?'))).toHaveLength(2);
});

it('keeps navigation accessible when collapsed and places the expand control below the theme control', async () => {
  const { unmount } = render(<App />); await screen.findByRole('button', { name: '查看 S001 作品' });
  fireEvent.click(screen.getByRole('button', { name: '收起侧栏' }));
  const sidebar = screen.getByRole('complementary', { name: '工作台导航' });
  expect(document.querySelector('.app-shell')?.classList.contains('sidebar-collapsed')).toBe(true);
  expect(within(sidebar).getByRole('button', { name: /^标签管理/ })).toBeTruthy();
  const footer = sidebar.querySelector('.sidebar-footer')!;
  expect([...footer.querySelectorAll('button')].map(button => button.getAttribute('aria-label'))).toEqual(['切换深色主题', '展开侧栏']);
  fireEvent.click(screen.getByRole('button', { name: '切换深色主题' }));
  expect(document.documentElement.dataset.theme).toBe('dark');
  unmount(); render(<App />); await screen.findByRole('button', { name: '展开侧栏' });
  expect(document.documentElement.dataset.theme).toBe('dark');
});

it('opens the real profile from the avatar and cancels its draft without a write', async () => {
  render(<App />); await screen.findByText('Miffn', { selector: '.brand strong' });
  fireEvent.click(screen.getByRole('link', { name: '个人中心' }));
  await screen.findByRole('heading', { name: '个人中心', level: 1 });
  expect(window.location.hash).toBe('#/profile');
  fireEvent.click(screen.getByRole('button', { name: '编辑资料' }));
  fireEvent.change(screen.getByLabelText('姓名'), { target: { value: '未保存姓名' } });
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  fireEvent.click(screen.getByRole('button', { name: '编辑资料' }));
  expect((screen.getByLabelText('姓名') as HTMLInputElement).value).toBe('Miffn');
  expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'PUT')).toBe(false);
});

it('retains an unsaved directory input while switching settings categories', async () => {
  render(<App />); await screen.findByRole('button', { name: '查看 S001 作品' });
  fireEvent.click(within(screen.getByRole('complementary', { name: '工作台导航' })).getByRole('button', { name: '设置' }));
  const path = await screen.findByLabelText('目录路径');
  fireEvent.change(path, { target: { value: 'D:\\DraftLibrary' } });
  fireEvent.click(screen.getByRole('button', { name: '扫描与处理' }));
  expect(screen.queryByRole('textbox', { name: '目录路径' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '本地资料库' }));
  expect((screen.getByLabelText('目录路径') as HTMLInputElement).value).toBe('D:\\DraftLibrary');
  expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'PUT')).toBe(false);
});

it('returns a work page to its profile origin with a matching back label', async () => {
  render(<App />); await screen.findByRole('button', { name: '查看 S001 作品' });
  fireEvent.click(screen.getByRole('link', { name: '个人中心' }));
  fireEvent.click(await screen.findByRole('button', { name: '查看 S001 作品' }));
  await screen.findByRole('button', { name: '返回个人中心' });
  expect(window.location.hash).toBe('#/works/1');
  fireEvent.click(screen.getByRole('button', { name: '返回个人中心' }));
  await screen.findByRole('heading', { name: '个人中心', level: 1 });
  expect(window.location.hash).toBe('#/profile');
});

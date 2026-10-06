// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Mock } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import App from './App';
import type { Inventory, Tag, Work } from './api';
import { setLanguage } from './i18n';

const author: Tag = { id: 1, category: 'author', name: '作者 A', revision: 1, usage_count: 2, support_url: null, support_status: 'unknown' };
const emptyLinks = { es: '', patreon: '', video: '', script: '' };
const work = (id: number): Work => ({
  id, script_id: `S${String(id).padStart(3, '0')}`, title: `作品 ${id}`, notes: '已有备注', status: 'pending',
  cover_url: null, video_count: 1, script_count: 1, issues: [], updated_at: '2026-10-06T00:00:00Z',
  es_published: false, patreon_published: false, es_published_date: null, patreon_published_date: null,
  tags: [author], tags_revision: 0, links: emptyLinks, links_revision: 0, production_required: false,
  directories: [{ id: id + 10, path: `/library/S${id}`, windows_path: `D:\\library\\S${id}`, available: true }],
  assets: [{ id: id + 100, directory_id: id + 10, kind: 'video', name: '视频.mp4', relative_path: '视频.mp4', size: 100 }],
});
const response = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
const originalScrollY = Object.getOwnPropertyDescriptor(window, 'scrollY');
let fetchMock: Mock<(path: string, init?: RequestInit) => Promise<Response>>;
let showModal: ReturnType<typeof vi.fn>;
let scrollTo: ReturnType<typeof vi.spyOn>;
let scrollPosition: number;
let workError: { id: number; status: number } | null;
let records: Map<number, Work>;

it('opens and returns from an unnumbered folder work through its stable numeric route', async () => {
  const existing = records.get(7)!;
  records.set(7, { ...existing, script_id: null, title: '普通文件夹' });
  render(<App />);
  const cardButton = await screen.findByRole('button', { name: '查看 普通文件夹' });
  const information = cardButton.closest('article')!.querySelector('.work-info')!;
  expect(within(information as HTMLElement).getAllByText('普通文件夹')).toHaveLength(1);
  expect(information.querySelector('h2')).toBeNull();
  fireEvent.click(cardButton);
  await screen.findByRole('heading', { name: '普通文件夹', level: 1 });
  expect(window.location.hash).toBe('#/works/7');
  expect(document.title).toBe('普通文件夹 · Funscript 工作台');
  expect(screen.queryByText(/^null$/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '返回库存' }));
  await screen.findByRole('button', { name: '查看 普通文件夹' });
  expect(window.location.hash).toBe('#/inventory');
});

beforeEach(() => {
  setLanguage({ language: 'zh-CN', revision: 0 });
  localStorage.clear();
  window.history.pushState(null, '', '/#/inventory');
  document.title = '脚本工作台'; document.body.style.overflow = 'auto';
  scrollPosition = 0; workError = null; records = new Map([7, 8].map(id => [id, work(id)]));
  Object.defineProperty(window, 'scrollY', { configurable: true, get: () => scrollPosition });
  scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation((...args: unknown[]) => {
    const options = args[0];
    scrollPosition = typeof options === 'object' && options !== null ? (options as ScrollToOptions).top ?? scrollPosition : Number(args[1] || 0);
  });
  showModal = vi.fn(function(this: HTMLDialogElement) { this.open = true; });
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: showModal });
  fetchMock = vi.fn((path: string, init?: RequestInit) => {
    const url = new URL(path, 'http://localhost');
    if (url.pathname === '/api/works') {
      const page = Number(url.searchParams.get('page') || '1');
      const result: Inventory = { items: [records.get(page === 1 ? 7 : 8)!], total: 72, page, page_size: 24,
        stats: { total: 72, pending: 72, published: 0, es_published: 0, patreon_published: 0, issues: 1 }, last_scan: null };
      return response(result);
    }
    if (path === '/api/profile') return response({ name: '测试工作台', bio: '测试简介', avatar: null, revision: 0 });
    if (path === '/api/language') return response({ language: 'zh-CN', revision: 0 });
    if (path === '/api/capabilities') return response({ can_open_folder: false, reason: '仅素材主机可用' });
    if (path === '/api/jobs' || path === '/api/issues') return response({ items: [], total: 0 });
    if (path === '/api/tags') return response({ items: [author], categories: [], import_report: null });
    if (path === '/api/settings') return response({ roots: [], scan_interval_seconds: 0, scan_roots_revision: 0 });
    if (path === '/api/mcp-auth') return response({ enabled: false, can_manage: false, revision: 0, updated_at: null });
    const detail = url.pathname.match(/^\/api\/works\/(\d+)(?:\/(.+))?$/);
    if (detail) {
      const id = Number(detail[1]); const resource = detail[2]; const current = records.get(id);
      if (resource === 'preview-matching') return response({ work_id: id, video_asset_id: null, mode: 'auto', revision: 0, script_asset_ids: {}, videos: [], scripts: [], issues: [], job: null, source_changed: false });
      if (resource === 'preview') return response({ job: null, files: [], output_dir: '', windows_path: '' });
      if (resource === 'tags') return response({ work_id: id, tags: current?.tags || [], tags_revision: 0 });
      if (resource === 'links') return response({ work_id: id, links: { ...emptyLinks }, links_revision: 0, es_published_date: null, patreon_published_date: null });
      if (!resource) {
        if (!current || workError?.id === id) return response({ detail: current ? '服务暂时不可用' : '库存编号不存在' }, current ? workError!.status : 404);
        if (init?.method === 'PATCH') { const next = { ...current, ...JSON.parse(String(init.body)) }; records.set(id, next); return response(next); }
        return response(current);
      }
    }
    return response({ detail: `Unexpected test endpoint: ${path}` }, 404);
  });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(async () => {
  cleanup();
  // jsdom history traversal is asynchronous; drain it after removing App listeners.
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
  vi.unstubAllGlobals(); vi.restoreAllMocks();
  if (originalScrollY) Object.defineProperty(window, 'scrollY', originalScrollY);
  localStorage.clear(); window.history.replaceState(null, '', '/#/inventory'); document.body.style.overflow = '';
  setLanguage({ language: 'zh-CN', revision: 0 });
});

const card = (id = 7) => screen.findByRole('button', { name: `查看 S${String(id).padStart(3, '0')} 作品 ${id}` });
const detailHeading = (id = 7) => screen.findByRole('heading', { level: 1, name: `S${String(id).padStart(3, '0')}` });
function latestInventoryParams() {
  const call = fetchMock.mock.calls.filter(([path]) => typeof path === 'string' && path.startsWith('/api/works?')).at(-1);
  return new URL(String(call?.[0]), 'http://localhost').searchParams;
}
function scrollWasRestored(position: number) {
  return scrollTo.mock.calls.some((args: unknown[]) => typeof args[0] === 'object' && args[0] !== null
    ? (args[0] as ScrollToOptions).top === position : args[1] === position);
}
async function traverse(direction: 'back' | 'forward') {
  await act(async () => { window.history[direction](); await new Promise(resolve => setTimeout(resolve, 30)); });
}
async function filteredGallery() {
  render(<App />); await card();
  const navigation = screen.getByRole('complementary', { name: '工作台导航' });
  fireEvent.click(within(navigation).getByRole('button', { name: /^待发布/ }));
  await waitFor(() => expect(latestInventoryParams().get('status')).toBe('pending'));
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索编号、标题或标签' }), { target: { value: 'needle' } });
  fireEvent.click(screen.getByRole('button', { name: '组合筛选' }));
  fireEvent.click(screen.getByRole('button', { name: '作者 A' }));
  fireEvent.click(screen.getByLabelText('仅看异常'));
  fireEvent.click(screen.getByRole('button', { name: '完成' }));
  await waitFor(() => {
    const params = latestInventoryParams();
    expect(params.get('q')).toBe('needle'); expect(params.get('status')).toBe('pending');
    expect(params.get('tag_ids')).toBe('1'); expect(params.get('issues_only')).toBe('true');
  });
  await waitFor(() => expect((screen.getByRole('button', { name: '下一页' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await card(8); await waitFor(() => expect(latestInventoryParams().get('page')).toBe('2'));
  scrollPosition = 612; document.documentElement.scrollTop = 612; scrollTo.mockClear();
}
async function assertFilteredGallery() {
  await card(8);
  expect((screen.getByRole('searchbox', { name: '搜索编号、标题或标签' }) as HTMLInputElement).value).toBe('needle');
  expect(document.querySelector('.inventory-filter-summary')?.textContent).toContain('作者 A');
  expect(document.querySelector('.inventory-filter-summary')?.textContent).toContain('仅看异常');
  expect(within(screen.getByRole('complementary', { name: '工作台导航' })).getByRole('button', { name: /^待发布/ }).getAttribute('aria-current')).toBe('page');
  expect(screen.getByText('第 2 / 3 页 · 每页 24 个')).toBeTruthy();
  await waitFor(() => {
    const params = latestInventoryParams();
    expect(params.get('q')).toBe('needle'); expect(params.get('status')).toBe('pending');
    expect(params.get('tag_ids')).toBe('1'); expect(params.get('issues_only')).toBe('true'); expect(params.get('page')).toBe('2');
    expect(scrollWasRestored(612)).toBe(true);
  });
}

it('opens a gallery work as a routed page with one work heading and updated document title', async () => {
  render(<App />); fireEvent.click(await card());
  const heading = await detailHeading();
  expect(window.location.hash).toBe('#/works/7');
  expect(heading.closest('article')?.classList.contains('detail-page')).toBe(true);
  expect(screen.queryByRole('dialog')).toBeNull(); expect(showModal).not.toHaveBeenCalled();
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  expect(screen.queryByRole('heading', { level: 1, name: '脚本库存' })).toBeNull();
  await waitFor(() => expect(document.title).toContain('S007'));
  expect(document.body.style.overflow).toBe('auto');
});

it('keeps compact-list detail editing in a dialog without entering the gallery route', async () => {
  localStorage.setItem('workbench-view', 'list'); render(<App />); fireEvent.click(await card());
  await screen.findByLabelText('标题');
  expect(screen.getByRole('dialog')).toBeTruthy(); expect(showModal).toHaveBeenCalledOnce();
  expect(window.location.hash).toBe('#/inventory'); expect(document.querySelector('article.detail-page')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '关闭作品详情' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect((await card()).closest('article')?.classList.contains('work-card')).toBe(true);
});

it('loads a direct work URL and remains a page after a simulated refresh, regardless of the saved list view', async () => {
  localStorage.setItem('workbench-view', 'list'); window.history.replaceState(null, '', '/#/works/7');
  const first = render(<App />); await detailHeading();
  expect(fetchMock.mock.calls.some(([path]) => path === '/api/works/7')).toBe(true);
  expect(screen.queryByRole('dialog')).toBeNull(); first.unmount();
  render(<App />); await detailHeading();
  expect(window.location.hash).toBe('#/works/7'); expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '返回库存' })); await card();
  expect(window.location.hash).not.toContain('/works/');
});

it('restores search, publication filter, tag, page and scroll through the detail Back button', async () => {
  await filteredGallery(); fireEvent.click(await card(8)); await detailHeading(8);
  fireEvent.click(screen.getByRole('button', { name: '返回库存' }));
  await assertFilteredGallery(); expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.title).not.toContain('S008');
});

it('preserves the gallery return context when the work detail page is refreshed', async () => {
  await filteredGallery(); fireEvent.click(await card(8)); await detailHeading(8);
  cleanup();
  render(<App />); await detailHeading(8);
  expect(window.location.hash).toBe('#/works/8'); expect(screen.queryByRole('dialog')).toBeNull();
  scrollTo.mockClear(); fireEvent.click(screen.getByRole('button', { name: '返回库存' }));
  await assertFilteredGallery(); expect(window.location.hash).toBe('#/pending');
});
it('restores the gallery through real browser Back and reopens the same detail through Forward', async () => {
  await filteredGallery(); fireEvent.click(await card(8)); await detailHeading(8);
  await traverse('back'); await assertFilteredGallery();
  await traverse('forward'); await detailHeading(8);
  expect(window.location.hash).toBe('#/works/8'); expect(screen.queryByRole('dialog')).toBeNull();
  scrollTo.mockClear(); await traverse('back'); await assertFilteredGallery();
});

describe('unsaved work-page navigation', () => {
  it.each(['sidebar', 'return', 'browser'] as const)('guards %s navigation, keeps drafts on Continue and leaves only after discard', async destination => {
    render(<App />); fireEvent.click(await card()); await detailHeading();
    const title = await screen.findByLabelText('标题');
    fireEvent.change(title, { target: { value: '未保存标题' } });
    fireEvent.change(screen.getByLabelText('备注'), { target: { value: '未保存备注' } });
    fireEvent.change(screen.getByLabelText('ES 发布日期'), { target: { value: '2026-10-06' } });
    const leave = async () => {
      if (destination === 'browser') await traverse('back');
      else fireEvent.click(destination === 'sidebar'
        ? within(screen.getByRole('complementary', { name: '工作台导航' })).getByRole('button', { name: '设置' })
        : screen.getByRole('button', { name: '返回库存' }));
      await screen.findByRole('button', { name: '继续编辑' });
      await waitFor(() => expect(window.location.hash).toBe('#/works/7'));
    };
    await leave(); fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
    expect((title as HTMLInputElement).value).toBe('未保存标题');
    expect((screen.getByLabelText('备注') as HTMLTextAreaElement).value).toBe('未保存备注');
    expect((screen.getByLabelText('ES 发布日期') as HTMLInputElement).value).toBe('2026-10-06');
    expect(window.location.hash).toBe('#/works/7'); expect(screen.queryByRole('dialog')).toBeNull();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false);
    await leave(); fireEvent.click(screen.getByRole('button', { name: '放弃更改并离开' }));
    if (destination === 'sidebar') await screen.findByRole('heading', { level: 1, name: '工作台设置' });
    else await card();
    expect(window.location.hash).toBe(destination === 'sidebar' ? '#/settings' : '#/inventory');
    expect(screen.queryByLabelText('标题')).toBeNull(); expect(screen.queryByText('有尚未保存的修改')).toBeNull();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false);
  });
});

it('opens publication link icons as their existing popup without routing to the work page', async () => {
  render(<App />); await card();
  fireEvent.click(screen.getByRole('button', { name: '填写 ES 帖子链接 S007' }));
  await screen.findByLabelText('ES 帖子链接');
  const dialog = screen.getByRole('dialog'); expect(within(dialog).getByRole('heading', { name: 'S007 · 发布链接' })).toBeTruthy();
  expect(window.location.hash).toBe('#/inventory'); expect(document.querySelector('article.detail-page')).toBeNull();
  expect(screen.getByRole('heading', { level: 1, name: '脚本库存' })).toBeTruthy();
});

it('keeps a nonexistent deep link visible with retry and a working inventory return', async () => {
  window.history.replaceState(null, '', '/#/works/999'); render(<App />);
  const alert = await screen.findByRole('alert'); expect(alert.textContent).toContain('库存编号不存在');
  expect(window.location.hash).toBe('#/works/999'); expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  await waitFor(() => expect(fetchMock.mock.calls.filter(([path]) => path === '/api/works/999')).toHaveLength(2));
  expect((await screen.findByRole('alert')).textContent).toContain('库存编号不存在');
  fireEvent.click(screen.getByRole('button', { name: '返回库存' })); await card();
  expect(window.location.hash).not.toContain('/works/');
});

it('recovers a transient detail fetch error in place after Retry', async () => {
  workError = { id: 7, status: 503 }; window.history.replaceState(null, '', '/#/works/7'); render(<App />);
  expect((await screen.findByRole('alert')).textContent).toContain('服务暂时不可用');
  workError = null; fireEvent.click(screen.getByRole('button', { name: '重试' }));
  await detailHeading(); await screen.findByLabelText('标题');
  expect(window.location.hash).toBe('#/works/7'); expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByRole('dialog')).toBeNull();
});


it('skips to main content within a dirty detail page without changing its route or asking to discard', async () => {
  const scrollIntoView = vi.fn();
  const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollIntoView');
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scrollIntoView });
  try {
    render(<App />); fireEvent.click(await card()); await detailHeading();
    const title = await screen.findByLabelText('标题');
    fireEvent.change(title, { target: { value: '未保存标题' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('link', { name: '跳到主要内容' }));
      await new Promise(resolve => setTimeout(resolve, 30));
    });
    expect(window.location.hash).toBe('#/works/7');
    expect(document.activeElement).toBe(screen.getByRole('main'));
    expect(scrollIntoView).toHaveBeenCalledOnce();
    expect((title as HTMLInputElement).value).toBe('未保存标题');
    expect(screen.queryByRole('button', { name: '继续编辑' })).toBeNull();
    expect(screen.queryByText('有尚未保存的修改')).toBeNull();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false);
  } finally {
    if (original) Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', original);
    else delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  }
});

describe('gallery position restoration after an inventory refresh error', () => {
  it.each(['success', 'failure'] as const)('survives a cancelled animation frame and restores focus/scroll after the new request ends in %s', async outcome => {
    render(<App />); await card();
    scrollPosition = 412; fireEvent.click(await card()); await detailHeading();
    const originalFetch = fetchMock.getMockImplementation()!;
    let requestMode: 'error' | 'deferred' = 'error';
    let inventoryRequests = 0;
    let resolveInventory!: (value: Response) => void;
    const pendingInventory = new Promise<Response>(resolve => { resolveInventory = resolve; });
    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path.startsWith('/api/works?')) {
        inventoryRequests++;
        return requestMode === 'error' ? response({ detail: '库存刷新失败' }, 503) : pendingInventory;
      }
      return originalFetch(path, init);
    });
    // A save refreshes inventory in the background while its previous cards stay cached.
    fireEvent.change(screen.getByLabelText('备注'), { target: { value: '已保存的备注' } });
    fireEvent.click(screen.getByRole('button', { name: '保存信息' }));
    await screen.findByText('S007 的作品信息已保存');
    await waitFor(() => expect(inventoryRequests).toBe(1));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });

    const frames = new Map<number, FrameRequestCallback>(); let nextFrame = 0;
    const schedule = vi.fn((callback: FrameRequestCallback) => { frames.set(++nextFrame, callback); return nextFrame; });
    const cancel = vi.fn((id: number) => { frames.delete(id); });
    vi.stubGlobal('requestAnimationFrame', schedule); vi.stubGlobal('cancelAnimationFrame', cancel);
    requestMode = 'deferred'; scrollTo.mockClear();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '返回库存' }));
      await new Promise(resolve => setTimeout(resolve, 30));
    });
    await waitFor(() => expect(inventoryRequests).toBe(2));
    expect(window.location.hash).toBe('#/inventory');
    expect(cancel).toHaveBeenCalled();
    expect(frames.size).toBe(0);
    expect(scrollWasRestored(412)).toBe(false);

    await act(async () => {
      resolveInventory(outcome === 'success'
        ? await originalFetch('/api/works?page=1')
        : await response({ detail: '库存仍暂时无法读取' }, 503));
    });
    const target = await card();
    if (outcome === 'failure') expect((await screen.findByRole('alert')).textContent).toContain('库存仍暂时无法读取');
    await waitFor(() => expect(frames.size).toBe(1));
    // Execute the pending callback, not merely assert that a frame was scheduled.
    await act(async () => {
      const pending = [...frames.values()]; frames.clear();
      pending.forEach(callback => callback(performance.now()));
    });
    expect(scrollWasRestored(412)).toBe(true);
    expect(document.activeElement).toBe(target);
    expect(scrollPosition).toBe(412);
  });
});
it('abandons a pending gallery restoration when navigation changes to All inventory before its refresh finishes', async () => {
  await filteredGallery(); fireEvent.click(await card(8)); await detailHeading(8);
  const originalFetch = fetchMock.getMockImplementation()!;
  let pendingPath = ''; let resolvePending!: (value: Response) => void;
  const pendingRefresh = new Promise<Response>(resolve => { resolvePending = resolve; });
  fetchMock.mockImplementation((path: string, init?: RequestInit) => {
    if (path.startsWith('/api/works?') && new URL(path, 'http://localhost').searchParams.get('status') === 'pending') {
      pendingPath = path; return pendingRefresh;
    }
    return originalFetch(path, init);
  });
  const frames = new Map<number, FrameRequestCallback>(); let nextFrame = 0;
  vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => { frames.set(++nextFrame, callback); return nextFrame; }));
  vi.stubGlobal('cancelAnimationFrame', vi.fn((id: number) => { frames.delete(id); }));
  scrollTo.mockClear();
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '返回库存' }));
    await new Promise(resolve => setTimeout(resolve, 30));
  });
  await waitFor(() => expect(pendingPath).not.toBe(''));
  expect(window.location.hash).toBe('#/pending');
  const navigation = screen.getByRole('complementary', { name: '工作台导航' });
  fireEvent.click(within(navigation).getByRole('button', { name: /^全部库存/ }));
  const allCard = await card();
  await waitFor(() => {
    expect(latestInventoryParams().get('status')).toBe('all');
    expect(latestInventoryParams().get('page')).toBe('1');
  });
  // The obsolete pending response still resolves despite cancellation; App must ignore it.
  await act(async () => { resolvePending(await originalFetch(pendingPath)); });
  await act(async () => {
    const callbacks = [...frames.values()]; frames.clear();
    callbacks.forEach(callback => callback(performance.now()));
  });
  expect(window.location.hash).toBe('#/inventory');
  expect(within(navigation).getByRole('button', { name: /^全部库存/ }).getAttribute('aria-current')).toBe('page');
  expect(screen.queryByRole('button', { name: '查看 S008 作品 8' })).toBeNull();
  expect(scrollWasRestored(612)).toBe(false);
  expect(scrollPosition).toBe(0);
  expect(document.activeElement).not.toBe(allCard);
});

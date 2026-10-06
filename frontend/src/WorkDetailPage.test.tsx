// @vitest-environment jsdom
import { createRef } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { WorkDetail } from './components';
import type { WorkDetailHandle } from './components';
import type { Work } from './api';
import { setLanguage } from './i18n';
import { LanguageBootstrap } from './LanguageSettings';

const fixture: Work = {
  id: 7, script_id: 'S025_001', title: '已有标题', notes: '已有备注', status: 'pending',
  video_count: 1, script_count: 1, cover_url: null, issues: [], updated_at: '',
  es_published: false, patreon_published: false, links: { es: '', patreon: '', video: '', script: '' }, links_revision: 0,
  directories: [{ id: 12, path: '/library/S025_001', windows_path: 'D:\\library\\S025_001', available: true }],
  assets: [{ id: 9, name: 'main.mp4', relative_path: 'main.mp4', kind: 'video', size: 100, directory_id: 12 }],
};
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
let current: Work;
let fetchMock: ReturnType<typeof vi.fn>;
let pendingPath: string;
let complete: ((response: Response) => void) | undefined;
let showModal: ReturnType<typeof vi.fn>;
const close = vi.fn();
const saved = vi.fn();
const notify = vi.fn();
beforeEach(() => {
  setLanguage({ language: 'zh-CN', revision: 0 });
  current = structuredClone(fixture); close.mockClear(); saved.mockClear(); notify.mockClear();
  pendingPath = ''; complete = undefined;
  document.body.style.overflow = 'auto';
  showModal = vi.fn(function(this: HTMLDialogElement) { this.open = true; });
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: showModal });
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (pendingPath && url === pendingPath && init?.method) return new Promise<Response>(resolve => { complete = resolve; });
    if (url.endsWith('/links')) { if (init?.method === 'PATCH') current = { ...current, ...JSON.parse(String(init.body)) }; return response({ ...current, work_id: 7, publication_revision: 'revision' }); }
    if (url === '/api/jobs') return response({ items: [] });
    if (url.includes('/preview-matching')) return response({ work_id: 7, video_asset_id: 9, mode: 'auto', revision: 0, script_asset_ids: {}, issues: [], videos: fixture.assets, scripts: [{ id: 22, name: 'pitch.funscript', relative_path: 'pitch.funscript', kind: 'script', axis: 'pitch', size: 200, directory_id: 12 }], job: null, source_changed: false });
    if (url.endsWith('/preview')) return response({ job: null, files: [], output_dir: '/output', windows_path: 'D:\\previews' });
    if (url === '/api/tags') return response({ items: [] });
    if (url.endsWith('/tags')) return response({ work_id: 7, tags: [], tags_revision: 0 });
    if (init?.method === 'PATCH') { current = { ...current, ...JSON.parse(String(init.body)) }; return response(current); }
    return response(current);
  });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); document.body.style.overflow = ''; act(() => setLanguage({ language: 'zh-CN', revision: 0 })); });
function page() {
  const ref = createRef<WorkDetailHandle>();
  const view = render(<WorkDetail ref={ref} id={7} presentation="page" capabilities={{ can_open_folder: true, reason: '' }} onClose={close} onSaved={saved} notify={notify} />);
  return { ...view, ref };
}

it('renders a full page with a title hero and complete-ID identity, document scrolling and guarded inventory return', async () => {
  const { unmount } = page();
  const heading = await screen.findByRole('heading', { name: '已有标题', level: 1 });
  expect(heading.closest('article')?.className).toBe('detail-page');
  expect(screen.queryByRole('dialog')).toBeNull(); expect(showModal).not.toHaveBeenCalled();
  expect(document.body.style.overflow).toBe('auto');
  await waitFor(() => expect(document.activeElement).toBe(heading));
  fireEvent.click(screen.getByRole('button', { name: '返回库存' }));
  expect(close).toHaveBeenCalledOnce();
  unmount(); expect(document.body.style.overflow).toBe('auto');
});

it('preserves title, notes and dates when continuing, then runs the requested navigation on discard', async () => {
  const { ref } = page(); const title = await screen.findByLabelText('标题');
  fireEvent.change(title, { target: { value: '未保存标题' } });
  fireEvent.change(screen.getByLabelText('备注'), { target: { value: '未保存备注' } });
  fireEvent.click(screen.getByRole('tab', { name: '发布信息' }));
  fireEvent.click(await screen.findByRole('button', { name: '编辑 ES 发布日期' }));
  fireEvent.change(screen.getByLabelText('ES 发布日期'), { target: { value: '2026-10-06' } });
  const first = vi.fn(); act(() => ref.current!.requestLeave(first));
  expect(first).not.toHaveBeenCalled();
  expect(document.activeElement).toBe(screen.getByRole('button', { name: '继续编辑' }));
  fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
  expect((title as HTMLInputElement).value).toBe('未保存标题');
  expect((screen.getByLabelText('备注') as HTMLTextAreaElement).value).toBe('未保存备注');
  expect((screen.getByLabelText('ES 发布日期') as HTMLInputElement).value).toBe('2026-10-06');
  const second = vi.fn(); act(() => ref.current!.requestLeave(second));
  fireEvent.click(screen.getByRole('button', { name: '放弃更改并离开' }));
  expect(second).toHaveBeenCalledOnce(); expect(first).not.toHaveBeenCalled(); expect(close).not.toHaveBeenCalled();
});

it('clears a cancelled pending destination and allows clean navigation after saving', async () => {
  const { ref } = page(); const title = await screen.findByLabelText('标题');
  fireEvent.change(title, { target: { value: '保存后的标题' } });
  const cancelled = vi.fn(); act(() => ref.current!.requestLeave(cancelled));
  fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
  fireEvent.click(screen.getByRole('button', { name: '保存信息' }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  const next = vi.fn(); act(() => ref.current!.requestLeave(next));
  expect(next).toHaveBeenCalledOnce(); expect(cancelled).not.toHaveBeenCalled();
});

it('uses the same unsaved matching guard for page navigation as for the default dialog', async () => {
  const { ref } = page(); await screen.findByLabelText('标题'); fireEvent.click(screen.getByRole('tab', { name: '素材' })); const pitch = await screen.findByLabelText('Pitch · 俯仰');
  fireEvent.change(pitch, { target: { value: '22' } });
  await screen.findByRole('button', { name: '放弃调整' });
  const next = vi.fn(); act(() => ref.current!.requestLeave(next));
  expect(screen.getByText('有尚未保存的修改')).toBeTruthy(); expect(next).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
  expect((pitch as HTMLSelectElement).value).toBe('22');
  fireEvent.click(screen.getByRole('button', { name: '返回库存' }));
  fireEvent.click(screen.getByRole('button', { name: '放弃更改并离开' }));
  expect(close).toHaveBeenCalledOnce(); expect(next).not.toHaveBeenCalled();
});

it.each(['save', 'status', 'folder', 'production'] as const)('blocks leave requests during a %s write', async operation => {
  if (operation === 'production') current = { ...current, production_required: true, production_revision: 3 };
  const { ref } = page(); await screen.findByLabelText('标题');
  pendingPath = operation === 'status' ? '/api/works/7/links' : operation === 'folder' ? '/api/works/7/open-folder' : operation === 'production' ? '/api/works/7/production/confirm' : '/api/works/7';
  if (operation === 'save') { fireEvent.change(screen.getByLabelText('备注'), { target: { value: '保存备注' } }); fireEvent.click(screen.getByRole('button', { name: '保存信息' })); }
  if (operation === 'status') { fireEvent.click(screen.getByRole('tab', { name: '发布信息' })); fireEvent.click(await screen.findByRole('button', { name: '切换 ES 发布状态' })); fireEvent.click(screen.getByRole('button', { name: '保存链接' })); }
  if (operation === 'folder') fireEvent.click(screen.getByRole('button', { name: '打开文件夹' }));
  if (operation === 'production') { await waitFor(() => expect((screen.getByRole('button', { name: '确认制作完成' }) as HTMLButtonElement).disabled).toBe(false)); fireEvent.click(screen.getByRole('button', { name: '确认制作完成' })); }
  expect((screen.getByRole('button', { name: '返回库存' }) as HTMLButtonElement).disabled).toBe(true);
  const next = vi.fn(); act(() => ref.current!.requestLeave(next));
  expect(next).not.toHaveBeenCalled(); expect(screen.queryByText('有尚未保存的修改')).toBeNull();
  await act(async () => complete!(response(operation === 'folder' ? { message: '已打开' } : { ...current, notes: operation === 'save' ? '保存备注' : current.notes, production_required: false })));
  await waitFor(() => expect((screen.getByRole('button', { name: '返回库存' }) as HTMLButtonElement).disabled).toBe(false));
});

it('blocks parent navigation while the nested tag editor is open and releases it after closing', async () => {
  const { ref } = page(); await screen.findByLabelText('标题');
  fireEvent.click(screen.getByRole('button', { name: '编辑标签' }));
  await screen.findByRole('button', { name: '关闭标签编辑' });
  const next = vi.fn(); act(() => ref.current!.requestLeave(next));
  expect(next).not.toHaveBeenCalled(); expect((screen.getByRole('button', { name: '返回库存' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '关闭标签编辑' }));
  act(() => ref.current!.requestLeave(next)); expect(next).toHaveBeenCalledOnce();
});

it('renders loading and request failures in the page and allows returning without a work', async () => {
  let resolve: ((value: Response) => void) | undefined;
  fetchMock.mockImplementation(() => new Promise<Response>(finish => { resolve = finish; }));
  page(); expect(screen.getByText('正在读取作品详情')).toBeTruthy();
  await act(async () => resolve!(response({ detail: '作品不存在' }, 404)));
  expect(screen.getByRole('alert').textContent).toContain('作品不存在');
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '返回库存' })); expect(close).toHaveBeenCalledOnce();
});

it('translates page navigation and its unsaved confirmation into English without translating user data', async () => {
  setLanguage({ language: 'en', revision: 1 }); const { ref } = page();
  const title = await screen.findByLabelText('Title');
  expect(screen.getByRole('button', { name: 'Back to inventory' })).toBeTruthy();
  fireEvent.change(title, { target: { value: '用户标题' } }); act(() => ref.current!.requestLeave(vi.fn()));
  expect(screen.getByRole('button', { name: 'Discard changes and leave' })).toBeTruthy();
  expect(screen.getByText('Leaving discards unsaved titles, notes, publication dates or script mappings.')).toBeTruthy();
  expect((title as HTMLInputElement).value).toBe('用户标题');
});

it('protects only unsaved page text from cross-document navigation and removes the guard after saving', async () => {
  const { unmount } = page(); await screen.findByLabelText('备注');
  const clean = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(clean);
  expect(clean.defaultPrevented).toBe(false);
  fireEvent.change(screen.getByLabelText('备注'), { target: { value: '待保存备注' } });
  const dirty = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(dirty);
  expect(dirty.defaultPrevented).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '保存信息' }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  const afterSave = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(afterSave);
  expect(afterSave.defaultPrevented).toBe(false);
  fireEvent.change(screen.getByLabelText('备注'), { target: { value: '再次修改' } }); unmount();
  const afterUnmount = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(afterUnmount);
  expect(afterUnmount.defaultPrevented).toBe(false);
});

it('protects unsaved script mappings from refresh and clears the native guard when mappings are discarded', async () => {
  page(); await screen.findByLabelText('标题'); fireEvent.click(screen.getByRole('tab', { name: '素材' })); const pitch = await screen.findByLabelText('Pitch · 俯仰');
  fireEvent.change(pitch, { target: { value: '22' } }); await screen.findByRole('button', { name: '放弃调整' });
  const dirty = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(dirty); expect(dirty.defaultPrevented).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '放弃调整' }));
  const clean = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(clean); expect(clean.defaultPrevented).toBe(false);
});

it('updates the page tab title for the full ID and language, then restores the current localized base title', async () => {
  const view = render(<><LanguageBootstrap /><WorkDetail id={7} presentation="page" capabilities={{ can_open_folder: false, reason: '' }} onClose={close} onSaved={saved} notify={notify} /></>);
  await screen.findByRole('heading', { name: '已有标题', level: 1 });
  await waitFor(() => expect(document.title).toBe('S025_001 · Funscript 工作台'));
  act(() => setLanguage({ language: 'en', revision: 2 }));
  expect(document.title).toBe('S025_001 · Funscript Workbench');
  view.rerender(<LanguageBootstrap />);
  expect(document.title).toBe('Funscript Workbench');
});

it('uses a localized work-details tab title for page loading and failure', async () => {
  let resolve: ((value: Response) => void) | undefined;
  fetchMock.mockImplementation(() => new Promise<Response>(finish => { resolve = finish; }));
  page(); expect(document.title).toBe('作品详情 · Funscript 工作台');
  await act(async () => resolve!(response({ detail: '作品不存在' }, 404)));
  expect(document.title).toBe('作品详情 · Funscript 工作台');
  act(() => setLanguage({ language: 'en', revision: 3 }));
  expect(document.title).toBe('Work details · Funscript Workbench');
});

it('does not change default dialog title or its original beforeunload behavior', async () => {
  document.title = '原有页面标题';
  render(<WorkDetail id={7} capabilities={{ can_open_folder: false, reason: '' }} onClose={close} onSaved={saved} notify={notify} />);
  const notes = await screen.findByLabelText('备注'); fireEvent.change(notes, { target: { value: '抽屉草稿' } });
  expect(document.title).toBe('原有页面标题');
  const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(false);
});

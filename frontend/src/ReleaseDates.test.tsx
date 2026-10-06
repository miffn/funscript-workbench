// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import App from './App';
import { WorkDetail } from './components';
import { ReleaseDates, workDisplayTitle } from './ReleaseDates';
import type { Work } from './api';

const work: Work = { id: 7, script_id: 'S069', title: 'S069', status: 'pending', es_published: false, patreon_published: false, es_published_date: null, patreon_published_date: '2026-09-29', notes: '', video_count: 1, script_count: 4, cover_url: null, directories: [], issues: [], updated_at: '' };
const response = (value: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }));
let fetchMock: ReturnType<typeof vi.fn>;
let patchWork: Work;
const saved = vi.fn();
const close = vi.fn();
beforeEach(() => {
  window.location.hash = '#/inventory';
  localStorage.clear();
  saved.mockClear(); close.mockClear(); patchWork = work;
  fetchMock = vi.fn((url: string, init?: RequestInit) => url.endsWith('/links') ? response({ ...patchWork, work_id: 7, links: patchWork.links || { es: '', patreon: '', video: '', script: '' }, links_revision: 1, publication_revision: 'revision' }) : response(init?.method === 'PATCH' ? patchWork : url.startsWith('/api/works?') ? { items: [work], total: 1, page: 1, page_size: 24, stats: { total: 1, pending: 1, published: 0, issues: 0 }, last_scan: null } : url === '/api/works/7' ? patchWork : url.endsWith('/preview-matching') ? { work_id: 7, video_asset_id: null, mode: 'auto', revision: 0, script_asset_ids: {}, issues: [], videos: [], scripts: [], job: null, source_changed: false } : url.endsWith('/preview') ? { job: null, files: [], output_dir: '', windows_path: '' } : url === '/api/capabilities' ? { can_open_folder: false, reason: '' } : { items: [] }));
  vi.stubGlobal('fetch', fetchMock);
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function(this: HTMLDialogElement) { this.open = true; } });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); window.location.hash = ''; });

it('shows calendar-only dates and leaves missing historical dates unrecorded', () => {
  render(<ReleaseDates work={work} />);
  expect(screen.getByText('2026-09-29').getAttribute('datetime')).toBe('2026-09-29');
  expect(screen.getByText('未记录')).toBeTruthy();
  expect(workDisplayTitle(work)).toBeNull();
  expect(workDisplayTitle({ ...work, title: ' S069 ' })).toBeNull();
  expect(workDisplayTitle({ ...work, title: '实际标题' })).toBe('实际标题');
  expect(workDisplayTitle({ ...work, script_id: null, title: '普通文件夹' })).toBeNull();
  expect(workDisplayTitle({ ...work, script_id: '', title: '普通文件夹' })).toBeNull();
});

it('displays both dates in every inventory mode without duplicating the ID heading', async () => {
  render(<App />);
  await screen.findByLabelText('Patreon · 待发布 · 2026-09-29');
  for (const mode of ['封面画廊', '紧凑目录', '标签列表']) {
    fireEvent.click(screen.getByRole('button', { name: mode }));
    const patreon = screen.getByLabelText('Patreon · 待发布 · 2026-09-29');
    expect(within(patreon).getByText('09/29').getAttribute('datetime')).toBe('2026-09-29');
    const es = screen.getByLabelText('ES · 待发布');
    expect(es.textContent).toBe('ES');
    expect(es.querySelector('time')).toBeNull();
    expect(screen.queryByLabelText('S069 发布日期')).toBeNull();
    expect(screen.queryByRole('heading', { name: 'S069' })).toBeNull();
  }
});

it('persists edited dates and allows clearing one without affecting the other', async () => {
  render(<WorkDetail id={7} capabilities={{ can_open_folder: false, reason: '' }} onClose={close} onSaved={saved} notify={vi.fn()} />);
  await screen.findByLabelText('标题');
  fireEvent.click(screen.getByRole('tab', { name: '发布信息' }));
  fireEvent.click(await screen.findByRole('button', { name: '编辑 ES 发布日期' }));
  const es = screen.getByLabelText('ES 发布日期');
  fireEvent.click(screen.getByRole('button', { name: '编辑 Patreon 发布日期' }));
  const patreon = screen.getByLabelText('Patreon 发布日期');
  expect((es as HTMLInputElement).value).toBe('');
  expect((patreon as HTMLInputElement).value).toBe('2026-09-29');
  fireEvent.change(es, { target: { value: '2026-09-30' } });
  fireEvent.change(patreon, { target: { value: '' } });
  patchWork = { ...work, es_published_date: '2026-09-30', patreon_published_date: null };
  fireEvent.click(screen.getByRole('button', { name: '保存链接' }));
  await screen.findByLabelText('ES · 待发布 · 2026-09-30');
  expect(screen.getByLabelText('ES · 待发布 · 2026-09-30').querySelector('time')?.getAttribute('datetime')).toBe('2026-09-30');
  expect(screen.getByLabelText('Patreon · 待发布').querySelector('time')).toBeNull();
  expect(JSON.parse(fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')![1].body)).toEqual({ links: {}, expected_revision: 1, expected_publication_revision: 'revision', es_published_date: '2026-09-30', patreon_published_date: null });
  expect(saved).toHaveBeenCalledOnce();
  expect((screen.getByRole('button', { name: '保存链接' }) as HTMLButtonElement).disabled).toBe(true);
});

it('preserves unsaved dates during publication toggles and confirms before discarding', async () => {
  render(<WorkDetail id={7} capabilities={{ can_open_folder: false, reason: '' }} onClose={close} onSaved={saved} notify={vi.fn()} />);
  await screen.findByLabelText('标题');
  fireEvent.click(screen.getByRole('tab', { name: '发布信息' }));
  fireEvent.click(await screen.findByRole('button', { name: '编辑 ES 发布日期' }));
  const es = screen.getByLabelText('ES 发布日期');
  fireEvent.change(es, { target: { value: '2026-09-28' } });
  patchWork = { ...work, es_published: true };
  fireEvent.click(screen.getByRole('button', { name: '切换 ES 发布状态' }));
  expect(screen.getByRole('button', { name: '切换 ES 发布状态' }).getAttribute('aria-pressed')).toBe('true');
  expect((es as HTMLInputElement).value).toBe('2026-09-28');
  fireEvent.click(screen.getByRole('button', { name: '关闭作品详情' }));
  expect(screen.getByText('有尚未保存的修改')).toBeTruthy();
  expect(close).not.toHaveBeenCalled();
});

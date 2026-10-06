// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Mock } from 'vitest';
import type { Notice } from './App';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Cover, PublicationBadges, WorkDetail } from './components';
import { historyEntries, safeLink } from './api';
import type { Work } from './api';

const fixture: Work = {
  id: 7, script_id: 'S025_001', title: '已有标题', status: 'pending', notes: '已有备注', video_count: 1,
  script_count: 2, cover_url: '/api/covers/7', issues: [], updated_at: '2026-09-30T04:00:00Z',
  directories: [{ id: 12, path: '/mnt/d/library/S025_001', windows_path: 'D:\\library\\S025_001', available: true }],
  assets: [{ id: 9, name: 'main.mp4', relative_path: 'main.mp4', kind: 'video', size: 1024, directory_id: 12 }],
};
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
let fetchMock: ReturnType<typeof vi.fn>;
let saved: Mock<() => void>;
let notify: Mock<(notice: Notice) => void>;
let onClose: Mock<() => void>;
const local = { can_open_folder: true, reason: '' };
const remote = { can_open_folder: false, reason: '不支持打开，仅素材所在主机可用' };
function detail(capabilities = local) { return render(<WorkDetail id={7} capabilities={capabilities} onClose={onClose} onSaved={saved} notify={notify} />); }

beforeEach(() => {
  fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve(response(url.includes('/preview-matching') ? { work_id: 7, video_asset_id: 9, mode: 'auto', revision: 0, script_asset_ids: {}, issues: [], videos: fixture.assets, scripts: [], job: null, source_changed: false } : url.endsWith('/preview') ? { job: null, files: [], output_dir: '/output/S025_001', windows_path: 'D:\\previews\\S025_001' } : fixture)));
  vi.stubGlobal('fetch', fetchMock);
  saved = vi.fn(); notify = vi.fn(); onClose = vi.fn();
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function(this: HTMLDialogElement) { this.open = true; } });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('persistent work editing', () => {
  it('keeps unsaved notes when a status change succeeds', async () => {
    detail();
    const notes = await screen.findByLabelText('备注');
    fireEvent.change(notes, { target: { value: '正在输入的备注' } });
    fetchMock.mockResolvedValueOnce(response({ ...fixture, es_published: true, patreon_published: false }));
    fireEvent.click(screen.getByRole('button', { name: '标记 ES 已发布' }));
    await screen.findByRole('button', { name: '将 ES 改为未发布' });
    expect((notes as HTMLTextAreaElement).value).toBe('正在输入的备注');
    expect(saved).toHaveBeenCalledOnce();
    expect(JSON.parse(fetchMock.mock.calls.find(([url, init]) => url === '/api/works/7' && init?.method === 'PATCH')![1].body)).toEqual({ es_published: true });
    expect((screen.getByRole('button', { name: '保存信息' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('updates Patreon separately, then retracts ES without resetting either draft', async () => {
    fetchMock.mockResolvedValueOnce(response({ ...fixture, es_published: true, patreon_published: false }));
    detail();
    const title = await screen.findByLabelText('标题');
    const notes = screen.getByLabelText('备注');
    fireEvent.change(title, { target: { value: '尚未保存的标题' } });
    fireEvent.change(notes, { target: { value: '尚未保存的备注' } });
    fetchMock.mockResolvedValueOnce(response({ ...fixture, status: 'published', es_published: true, patreon_published: true }));
    fireEvent.click(screen.getByRole('button', { name: '标记 Patreon 已发布' }));
    await screen.findByRole('button', { name: '将 Patreon 改为未发布' });
    fetchMock.mockResolvedValueOnce(response({ ...fixture, es_published: false, patreon_published: true }));
    fireEvent.click(screen.getByRole('button', { name: '将 ES 改为未发布' }));
    await screen.findByRole('button', { name: '标记 ES 已发布' });
    expect(screen.getByRole('button', { name: '将 Patreon 改为未发布' })).toBeTruthy();
    expect((title as HTMLInputElement).value).toBe('尚未保存的标题');
    expect((notes as HTMLTextAreaElement).value).toBe('尚未保存的备注');
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH').map(([, init]) => JSON.parse(init.body))).toEqual([{ patreon_published: true }, { es_published: false }]);
  });

  it('keeps both current publication states if updating a platform fails', async () => {
    fetchMock.mockResolvedValueOnce(response({ ...fixture, es_published: true, patreon_published: false }));
    detail();
    await screen.findByLabelText('备注');
    fetchMock.mockResolvedValueOnce(response({ detail: '写入失败' }, 503));
    fireEvent.click(screen.getByRole('button', { name: '标记 Patreon 已发布' }));
    await screen.findByText('发布状态未更新：写入失败');
    expect(screen.getByLabelText('ES · 已发布')).toBeTruthy();
    expect(screen.getByLabelText('Patreon · 待发布')).toBeTruthy();
    expect(saved).not.toHaveBeenCalled();
  });

  it('retains unsaved axis matching when publication changes and blocks duplicate writes', async () => {
    const current = { ...fixture, es_published: false, patreon_published: false };
    const script = { id: 22, name: 'pitch.funscript', relative_path: 'pitch.funscript', kind: 'script', axis: 'pitch', size: 200, directory_id: 12 };
    let complete: ((value: Response) => void) | undefined;
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') return new Promise<Response>(resolve => { complete = resolve; });
      return Promise.resolve(response(url.includes('/preview-matching') ? { work_id: 7, video_asset_id: 9, mode: 'auto', revision: 0, script_asset_ids: {}, issues: [], videos: fixture.assets, scripts: [script], job: null, source_changed: false } : url.endsWith('/preview') ? { job: null, files: [], output_dir: '/output/S025_001', windows_path: 'D:\\previews\\S025_001' } : current));
    });
    detail();
    const pitch = await screen.findByLabelText('Pitch · 俯仰');
    fireEvent.change(pitch, { target: { value: '22' } });
    const es = screen.getByRole('button', { name: '标记 ES 已发布' });
    fireEvent.click(es);
    expect((es as HTMLButtonElement).disabled).toBe(true);
    const patreon = screen.getByRole('button', { name: '标记 Patreon 已发布' });
    expect((patreon as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(es); fireEvent.click(patreon);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1);
    complete!(response({ ...current, es_published: true }));
    await screen.findByRole('button', { name: '将 ES 改为未发布' });
    expect((pitch as HTMLSelectElement).value).toBe('22');
    expect(screen.getByRole('button', { name: '放弃调整' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '关闭作品详情' }));
    expect(screen.getByText('有尚未保存的修改')).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('preserves failed edits and only reports success after the retry writes to the server', async () => {
    detail();
    const title = await screen.findByLabelText('标题');
    fireEvent.change(title, { target: { value: '新的标题' } });
    fetchMock.mockResolvedValueOnce(response({ detail: '数据库暂时不可用' }, 503));
    fireEvent.click(screen.getByRole('button', { name: '保存信息' }));
    await screen.findByText('修改未保存：数据库暂时不可用');
    expect((title as HTMLInputElement).value).toBe('新的标题');
    expect(saved).not.toHaveBeenCalled();
    expect(notify).not.toHaveBeenCalled();
    fetchMock.mockResolvedValueOnce(response({ ...fixture, title: '新的标题' }));
    fireEvent.click(screen.getByRole('button', { name: '保存信息' }));
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    expect((screen.getByRole('button', { name: '保存信息' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('uses an accessible in-page discard confirmation and keeps edits when cancelled', async () => {
    detail();
    const notes = await screen.findByLabelText('备注');
    fireEvent.change(notes, { target: { value: '尚未保存' } });
    fireEvent.click(screen.getByRole('button', { name: '关闭作品详情' }));
    expect(onClose).not.toHaveBeenCalled();
    const continueButton = screen.getByRole('button', { name: '继续编辑' });
    expect(document.activeElement).toBe(continueButton);
    fireEvent.click(continueButton);
    expect(screen.queryByText('有尚未保存的修改')).toBeNull();
    expect((notes as HTMLTextAreaElement).value).toBe('尚未保存');
    // Escape has the same guard as the close button.
    fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }));
    fireEvent.click(screen.getByRole('button', { name: '放弃更改并关闭' }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/works/7')).toHaveLength(1);
  });
});

describe('host folder capability', () => {
  it('never submits an open-folder request from a remote client', async () => {
    detail(remote);
    await screen.findByLabelText('备注');
    const button = screen.getByRole('button', { name: '打开文件夹' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(remote.reason)).toBeTruthy();
    fireEvent.click(button);
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/works/7')).toHaveLength(1);
  });

  it('blocks folder opening for conflicting paths without offering multiple bindings', async () => {
    const duplicate = { ...fixture, directories: [], issues: [{ type: 'duplicate_identifier', message: '同一编号对应多个文件夹，请处理编号冲突' }] };
    fetchMock.mockResolvedValueOnce(response(duplicate));
    detail();
    await screen.findByLabelText('备注');
    const button = screen.getByRole('button', { name: '打开文件夹' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole('radio')).toBeNull();
    expect(screen.getByText('编号存在目录冲突，请处理冲突后重新匹配文件。')).toBeTruthy();
    fireEvent.click(button);
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/works/7/open-folder')).toBe(false);
  });
});

describe('cover and historical data', () => {
  it('displays both platform states and supports migrated legacy data', () => {
    const view = render(<PublicationBadges work={{ ...fixture, es_published: false, patreon_published: true }} />);
    expect(screen.getByLabelText('ES · 待发布')).toBeTruthy();
    expect(screen.getByLabelText('Patreon · 已发布')).toBeTruthy();
    view.rerender(<PublicationBadges work={{ ...fixture, status: 'published' }} />);
    expect(screen.getByLabelText('ES · 已发布')).toBeTruthy();
    expect(screen.getByLabelText('Patreon · 已发布')).toBeTruthy();
  });
  it('keeps a cover placeholder if an image fails to load', () => {
    render(<Cover work={fixture} />);
    fireEvent.error(screen.getByRole('img'));
    expect(screen.getByText('封面暂不可用')).toBeTruthy();
    expect(screen.queryByRole('img')).toBeNull();
  });
  it('keeps sub-ID historical source links without showing stale production status or duplicating fields', () => {
    expect(historyEntries({ 'ES Link': ' https://example.com/S025_001 ', 'ES Post URL': '', es_url: 'https://example.com/S025_001', 'Stauts': 'Ready', source_rows: { old: {} }, 'Axis Type': 'Multi-axis' })).toEqual([['轴类型', 'Multi-axis'], ['EroScripts', 'https://example.com/S025_001']]);
    expect(safeLink('javascript:alert(1)')).toBeNull();
    expect(safeLink('file:///D:/library')).toBeNull();
    expect(safeLink('https://example.com/S025_001')).toBe('https://example.com/S025_001');
  });
});

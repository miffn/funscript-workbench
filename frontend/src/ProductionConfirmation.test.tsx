// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Work } from './api';
import type { Notice } from './App';
import type { Mock } from 'vitest';
import { WorkDetail } from './components';

vi.mock('./PreviewSection', () => ({
  PreviewSection: ({ onSourcesChanged, onDirtyChange }: { onSourcesChanged: () => Promise<void>; onDirtyChange: (dirty: boolean) => void }) => <div>
    <button onClick={() => void onSourcesChanged()}>测试刷新素材</button>
    <button onClick={() => onDirtyChange(true)}>测试修改对应关系</button>
  </div>,
}));

const fixture: Work = {
  id: 7, script_id: 'S070', title: '原有标题', status: 'pending', notes: '原有备注',
  video_count: 1, script_count: 2, cover_url: null, issues: [], updated_at: '2026-10-05T06:00:00Z',
  es_published_date: '2026-10-03', patreon_published_date: '2026-10-04',
  directories: [{ id: 12, path: '/mnt/d/library/S070', windows_path: 'D:\\library\\S070', available: true }],
  production_required: true, production_confirmed_at: null, production_revision: 3,
};
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
let server: Work;
let tasks: { type: string; status: string }[];
let fetchMock: ReturnType<typeof vi.fn>;
let saved: Mock<() => void>;
let notify: Mock<(notice: Notice) => void>;
const confirmRequests = () => fetchMock.mock.calls.filter(([url]) => url === '/api/works/7/production/confirm');
const detail = () => render(<WorkDetail id={7} capabilities={{ can_open_folder: false, reason: '' }} onClose={() => {}} onSaved={saved} notify={notify} />);
const confirmButton = () => screen.getByRole('button', { name: '确认制作完成' }) as HTMLButtonElement;

beforeEach(() => {
  server = structuredClone(fixture); tasks = []; saved = vi.fn(); notify = vi.fn();
  fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve(response(url === '/api/jobs' ? { items: tasks } : server)));
  vi.stubGlobal('fetch', fetchMock);
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function(this: HTMLDialogElement) { this.open = true; } });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('manual production completion', () => {
  it('keeps scriptless works waiting and explains how to add source files', async () => {
    server = { ...server, script_count: 0 };
    detail();
    await screen.findByRole('heading', { name: '制作确认' });
    expect(screen.getByText('待确认制作完成')).toBeTruthy();
    expect(screen.getByText('尚无可用脚本，请添加脚本后扫描或重新匹配文件。')).toBeTruthy();
    expect(confirmButton().disabled).toBe(true);
    fireEvent.click(confirmButton());
    expect(confirmRequests()).toHaveLength(0);
  });

  it('requires a click even with scripts, guards duplicate requests and retains all local drafts', async () => {
    let complete: ((value: Response) => void) | undefined;
    fetchMock.mockImplementation((url: string) => url.endsWith('/production/confirm') ? new Promise<Response>(resolve => { complete = resolve; }) : Promise.resolve(response(url === '/api/jobs' ? { items: [] } : server)));
    detail();
    await screen.findByLabelText('标题');
    await waitFor(() => expect(confirmButton().disabled).toBe(false));
    expect(confirmRequests()).toHaveLength(0);
    fireEvent.change(screen.getByLabelText('标题'), { target: { value: '未保存的新标题' } });
    fireEvent.change(screen.getByLabelText('备注'), { target: { value: '未保存的新备注' } });
    fireEvent.change(screen.getByLabelText('ES 发布日期'), { target: { value: '2026-09-29' } });
    fireEvent.change(screen.getByLabelText('Patreon 发布日期'), { target: { value: '2026-09-30' } });
    fireEvent.click(confirmButton());
    const busy = screen.getByRole('button', { name: '正在确认制作完成' }) as HTMLButtonElement;
    expect(busy.disabled).toBe(true); fireEvent.click(busy);
    expect((screen.getByRole('button', { name: '保存信息' }) as HTMLButtonElement).disabled).toBe(true);
    expect(confirmRequests()).toHaveLength(1);
    expect(JSON.parse(confirmRequests()[0][1].body)).toEqual({ expected_revision: 3 });
    await act(async () => { complete!(response({ ...server, production_required: false, production_confirmed_at: '2026-10-05T06:10:00Z', production_revision: 4 })); });
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    expect(screen.queryByRole('heading', { name: '制作确认' })).toBeNull();
    expect((screen.getByLabelText('标题') as HTMLInputElement).value).toBe('未保存的新标题');
    expect((screen.getByLabelText('备注') as HTMLTextAreaElement).value).toBe('未保存的新备注');
    expect((screen.getByLabelText('ES 发布日期') as HTMLInputElement).value).toBe('2026-09-29');
    expect((screen.getByLabelText('Patreon 发布日期') as HTMLInputElement).value).toBe('2026-09-30');
    expect(notify).toHaveBeenCalledWith({ kind: 'success', message: '已确认 S070 制作完成' });
  });

  it('blocks confirmation for unavailable source directories', async () => {
    server = { ...server, directories: server.directories.map(directory => ({ ...directory, available: false })) };
    detail();
    await screen.findByText('当前作品目录不可用，请恢复目录后扫描或重新匹配文件。');
    expect(confirmButton().disabled).toBe(true);
    expect(confirmRequests()).toHaveLength(0);
  });

  it('blocks confirmation while a scan, rematch or preview task is active', async () => {
    tasks = [{ type: 'preview', status: 'running' }];
    detail();
    await screen.findByText('后台任务正在运行，请等待扫描、匹配或预览完成后确认。');
    expect(confirmButton().disabled).toBe(true);
    expect(confirmRequests()).toHaveLength(0);
  });

  it('keeps a failed confirmation and local edits intact until the user retries', async () => {
    let attempts = 0;
    fetchMock.mockImplementation((url: string) => {
      if (url.endsWith('/production/confirm')) return Promise.resolve(++attempts === 1 ? response({ detail: '制作状态已变化，请刷新后重试。' }, 409) : response({ ...server, production_required: false, production_revision: 4 }));
      return Promise.resolve(response(url === '/api/jobs' ? { items: [] } : server));
    });
    detail(); await screen.findByLabelText('备注');
    await waitFor(() => expect(confirmButton().disabled).toBe(false));
    fireEvent.change(screen.getByLabelText('备注'), { target: { value: '保留这份备注' } });
    fireEvent.click(confirmButton());
    await screen.findByText('制作完成未确认：制作状态已变化，请刷新后重试。');
    expect(saved).not.toHaveBeenCalled(); expect(notify).not.toHaveBeenCalled();
    expect((screen.getByLabelText('备注') as HTMLTextAreaElement).value).toBe('保留这份备注');
    expect(confirmButton().disabled).toBe(false);
    fireEvent.click(confirmButton()); await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  });

  it('refreshes production state with rematched sources without replacing local notes', async () => {
    server = { ...server, production_required: false, production_confirmed_at: '2026-10-04T00:00:00Z' };
    detail(); await screen.findByLabelText('备注');
    expect(screen.queryByRole('heading', { name: '制作确认' })).toBeNull();
    fireEvent.change(screen.getByLabelText('备注'), { target: { value: '仍在编辑' } });
    server = { ...server, script_count: 0, production_required: true, production_confirmed_at: null, production_revision: 5 };
    fireEvent.click(screen.getByRole('button', { name: '测试刷新素材' }));
    await screen.findByRole('heading', { name: '制作确认' });
    expect(confirmButton().disabled).toBe(true);
    expect((screen.getByLabelText('备注') as HTMLTextAreaElement).value).toBe('仍在编辑');
    expect(saved).toHaveBeenCalledOnce();
  });

  it('blocks confirmation while script assignments have unsaved edits', async () => {
    detail(); await screen.findByLabelText('备注');
    await waitFor(() => expect(confirmButton().disabled).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: '测试修改对应关系' }));
    expect(confirmButton().disabled).toBe(true);
    expect(screen.getByText('请先保存或放弃尚未保存的脚本对应关系。')).toBeTruthy();
    expect(confirmRequests()).toHaveLength(0);
  });
});
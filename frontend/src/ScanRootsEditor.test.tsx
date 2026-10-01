// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SettingsPage } from './components';
import { ScanRootsEditor } from './ScanRootsEditor';
import type { Settings } from './api';

const year = '/mnt/d/Media/2026';
const workspace = '/mnt/d/Media/workspace';
const initial: Settings = { scan_interval_seconds: 0, scan_roots_revision: 3, roots: [{ path: year, windows_path: 'D:\\Media\\2026', label: '2026', enabled: true, available: true }, { path: workspace, windows_path: 'D:\\Media\\workspace', label: 'workspace', enabled: true, available: false }] };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
let server: Settings;
let failStatus: number;
let fetchMock: ReturnType<typeof vi.fn>;
const checkbox = (label: string) => screen.getByRole('checkbox', { name: `扫描 ${label}` }) as HTMLInputElement;
const saveButton = () => screen.getByRole('button', { name: '保存扫描目录' }) as HTMLButtonElement;

beforeEach(() => {
  server = structuredClone(initial); failStatus = 0;
  fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/settings/scan-roots') {
      if (failStatus) return Promise.resolve(response({ detail: failStatus === 409 ? '扫描目录版本已被其他客户端修改' : '服务暂不可用' }, failStatus));
      const body = JSON.parse(init!.body as string);
      server = { ...server, scan_roots_revision: server.scan_roots_revision! + 1, roots: body.roots.map((root: Record<string, unknown>) => ({ ...root, windows_path: root.path, available: true })) };
    }
    return Promise.resolve(response(server));
  });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('persisted scan directory selection', () => {
  it('adds and deletes directory configuration, then reads the saved catalog on a fresh mount', async () => {
    const view = render(<ScanRootsEditor settings={initial} />);
    fireEvent.change(screen.getByLabelText('目录路径'), { target: { value: 'E:\\素材' } });
    fireEvent.change(screen.getByLabelText('名称（可选）'), { target: { value: '新库存' } });
    fireEvent.click(screen.getByRole('button', { name: '添加目录' }));
    fireEvent.click(screen.getByRole('button', { name: '删除目录 2026' }));
    expect(checkbox('新库存').checked).toBe(true); expect(screen.queryByRole('checkbox', { name: '扫描 2026' })).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(saveButton()); await screen.findByText(/扫描目录已保存/);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).roots).toEqual([{ path: workspace, label: 'workspace', enabled: true }, { path: 'E:\\素材', label: '新库存', enabled: true }]);
    view.unmount(); render(<ScanRootsEditor settings={server} />);
    expect(checkbox('新库存').checked).toBe(true); expect(screen.queryByRole('checkbox', { name: '扫描 2026' })).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => url.includes('/scans'))).toBe(false);
  });

  it('can delete every directory and save an empty catalog', async () => {
    render(<ScanRootsEditor settings={initial} />);
    fireEvent.click(screen.getByRole('button', { name: '删除目录 2026' })); fireEvent.click(screen.getByRole('button', { name: '删除目录 workspace' }));
    fireEvent.click(saveButton()); await screen.findByText(/扫描目录已保存/);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ roots: [], expected_revision: 3 });
    expect(screen.getByText('尚未配置扫描目录，请添加素材所在的目录。')).toBeTruthy();
  });

  it('rejects relative paths and duplicates before changing the draft', () => {
    render(<ScanRootsEditor settings={initial} />);
    fireEvent.change(screen.getByLabelText('目录路径'), { target: { value: 'folder' } }); fireEvent.click(screen.getByRole('button', { name: '添加目录' }));
    expect(screen.getByText(/请输入 Windows 盘符绝对路径/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('目录路径'), { target: { value: 'd:\\Media\\2026\\' } }); fireEvent.click(screen.getByRole('button', { name: '添加目录' }));
    expect(screen.getByText('该目录已在列表中。')).toBeTruthy(); expect(saveButton().disabled).toBe(true); expect(fetchMock).not.toHaveBeenCalled();
  });

  it('preserves the directory draft and allows retry when active tasks temporarily prevent saving', async () => {
    fetchMock.mockResolvedValueOnce(response({ detail: '有任务正在运行，请稍后重试' }, 409));
    render(<ScanRootsEditor settings={initial} />); fireEvent.click(screen.getByRole('button', { name: '删除目录 2026' })); fireEvent.click(saveButton());
    await screen.findByText('扫描目录未保存：有任务正在运行，请稍后重试');
    expect(saveButton().disabled).toBe(false); fireEvent.click(saveButton()); await screen.findByText(/扫描目录已保存/);
  });
  it('uses server enabled flags and does not save or scan until the save button is clicked', () => {
    const settings = { ...initial, roots: [initial.roots[0], { ...(initial.roots[1] as Record<string, unknown>), enabled: false }] };
    render(<ScanRootsEditor settings={settings} />);
    expect(checkbox('2026').checked).toBe(true); expect(checkbox('workspace').checked).toBe(false);
    expect(saveButton().disabled).toBe(true);
    fireEvent.click(checkbox('workspace'));
    expect(saveButton().disabled).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText(/取消勾选不会删除已有库存或标签，保存也不会触发扫描/)).toBeTruthy();
  });

  it('saves exact configured paths with the revision and prevents duplicate submissions', async () => {
    let finish: ((value: Response) => void) | undefined;
    fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
    render(<ScanRootsEditor settings={initial} />); fireEvent.click(checkbox('workspace'));
    fireEvent.click(saveButton()); fireEvent.click(screen.getByRole('button', { name: '正在保存扫描目录' }));
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/settings/scan-roots'); expect(init.method).toBe('PUT');
    expect(JSON.parse(init.body)).toEqual({ roots: [{ path: year, label: '2026', enabled: true }, { path: workspace, label: 'workspace', enabled: false }], expected_revision: 3 });
    server = { ...server, scan_roots_revision: 4, roots: [server.roots[0], { ...(server.roots[1] as Record<string, unknown>), enabled: false }] };
    await act(async () => { finish!(response(server)); });
    expect(screen.getByText(/扫描目录已保存；尚未执行扫描/)).toBeTruthy();
    expect(saveButton().disabled).toBe(true); expect(checkbox('workspace').checked).toBe(false);
    expect(fetchMock.mock.calls.some(([url]) => url.includes('/scans'))).toBe(false);
  });

  it('retains failed choices and supports retry without reselecting', async () => {
    failStatus = 503; render(<ScanRootsEditor settings={initial} />); fireEvent.click(checkbox('workspace')); fireEvent.click(saveButton());
    await screen.findByText('扫描目录未保存：服务暂不可用');
    expect(checkbox('workspace').checked).toBe(false); expect(saveButton().disabled).toBe(false);
    failStatus = 0; fireEvent.click(saveButton()); await screen.findByText(/扫描目录已保存/);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(2);
  });

  it('keeps drafts through actual settings polling instead of replacing them with another client’s changes', async () => {
    vi.useFakeTimers();
    render(<SettingsPage revision={0} capabilities={{ can_open_folder: false, reason: '仅主机可用' }} />);
    await act(async () => { await Promise.resolve(); });
    fireEvent.click(checkbox('workspace'));
    server = { ...initial, scan_roots_revision: 8, roots: [{ ...(initial.roots[0] as Record<string, unknown>), enabled: false }, initial.roots[1]] };
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/settings')).toHaveLength(2);
    expect(checkbox('2026').checked).toBe(true); expect(checkbox('workspace').checked).toBe(false);
    expect(screen.getByText('未保存')).toBeTruthy();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false);
  });

  it('allows disabling every directory and persists the choice', async () => {
    render(<ScanRootsEditor settings={initial} />); fireEvent.click(checkbox('workspace')); fireEvent.click(checkbox('2026'));
    expect(screen.getByText('没有启用目录，立即扫描暂不可用。')).toBeTruthy();
    expect(saveButton().disabled).toBe(false); fireEvent.click(saveButton()); await screen.findByText(/扫描目录已保存/);
    expect(checkbox('2026').checked).toBe(false); expect(checkbox('workspace').checked).toBe(false);
  });

  it('keeps a 409 draft until the user explicitly loads current settings and then saves against the latest revision', async () => {
    failStatus = 409; const view = render(<ScanRootsEditor settings={initial} />);
    fireEvent.click(checkbox('workspace')); fireEvent.click(saveButton()); await screen.findByText(/扫描目录已被其他客户端修改/);
    server = { ...initial, scan_roots_revision: 8, roots: [{ ...(initial.roots[0] as Record<string, unknown>), enabled: false }, initial.roots[1]] };
    view.rerender(<ScanRootsEditor settings={server} />);
    expect(checkbox('2026').checked).toBe(true); expect(checkbox('workspace').checked).toBe(false);
    expect(saveButton().disabled).toBe(true); fireEvent.click(saveButton()); expect(fetchMock).toHaveBeenCalledOnce();
    failStatus = 0; fireEvent.click(screen.getByRole('button', { name: '放弃选择并加载最新设置' }));
    await screen.findByText('已加载最新设置，请重新确认扫描目录。');
    expect(checkbox('2026').checked).toBe(false); expect(checkbox('workspace').checked).toBe(true);
    fireEvent.click(checkbox('2026')); fireEvent.click(saveButton()); await screen.findByText(/扫描目录已保存/);
    const latestPut = fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT').at(-1)!;
    expect(JSON.parse(latestPut[1].body).expected_revision).toBe(8);
  });

  it('reads saved settings on a fresh mount instead of keeping browser-only choices', async () => {
    const first = render(<SettingsPage revision={0} capabilities={{ can_open_folder: false, reason: '' }} />);
    await screen.findByRole('checkbox', { name: '扫描 workspace' }); fireEvent.click(checkbox('workspace')); fireEvent.click(saveButton());
    await screen.findByText(/扫描目录已保存/); first.unmount();
    render(<SettingsPage revision={0} capabilities={{ can_open_folder: false, reason: '' }} />);
    await screen.findByRole('checkbox', { name: '扫描 workspace' });
    expect(checkbox('workspace').checked).toBe(false); expect(saveButton().disabled).toBe(true);
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/settings')).toHaveLength(2);
  });

  it('renders legacy string roots without inventing paths or sending a missing version', () => {
    render(<ScanRootsEditor settings={{ roots: [year, workspace], scan_interval_seconds: 0 }} />);
    expect(checkbox('2026').checked).toBe(true); expect(checkbox('workspace').checked).toBe(true);
    expect(saveButton().disabled).toBe(true); expect(screen.getByText(/当前服务尚未提供可保存的目录版本/)).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('keeps the preserved draft if loading the latest version fails', async () => {
    render(<ScanRootsEditor settings={initial} />); fireEvent.click(checkbox('workspace'));
    fetchMock.mockResolvedValueOnce(response({ detail: '无法连接服务' }, 503));
    fireEvent.click(screen.getByRole('button', { name: '放弃选择并加载最新设置' }));
    await screen.findByText('无法加载最新设置：无法连接服务');
    expect(checkbox('workspace').checked).toBe(false); expect(checkbox('2026').checked).toBe(true);
    await waitFor(() => expect(saveButton().disabled).toBe(false));
  });
});

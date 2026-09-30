// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { PreviewSection } from './PreviewSection';
import { WorkDetail } from './components';
import type { Job, PreviewFile, PreviewState, Work } from './api';

const work: Work = { id: 7, script_id: 'S025_001', title: '作品标题', notes: '已有备注', status: 'pending', cover_url: null, video_count: 1, script_count: 1, updated_at: '2026-09-30T12:00:00Z', issues: [], directories: [{ id: 12, path: '/library/S025_001', windows_path: 'D:\\library\\S025_001', available: true }], assets: [{ id: 9, directory_id: 12, name: 'main.mp4', relative_path: 'main.mp4', kind: 'video', size: 1024 }] };
const local = { can_open_folder: true, reason: '' };
const remote = { can_open_folder: false, reason: '不支持打开，仅素材所在主机可用' };
const job: Job = { id: 44, type: 'preview', status: 'queued', progress: 0, created_at: '2026-09-30T12:00:00Z', result: { work_id: 7, script_id: 'S025_001', video_asset_id: 9 } };
const outputFiles: PreviewFile[] = Array.from({ length: 4 }, (_, index) => ['video', 'gif'].map(kind => ({ filename: `clip-${index}.${kind === 'gif' ? 'gif' : 'webm'}`, kind: kind as 'video' | 'gif', clip_index: index, width: 320, height: 180, size: 2048, url: `/api/works/7/preview/files/clip-${index}.${kind === 'gif' ? 'gif' : 'webm'}` }))).flat();
const empty: PreviewState = { job: null, files: [], output_dir: '/output/S025_001', windows_path: 'D:\\previews\\S025_001' };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
let state: PreviewState;
let fetchMock: ReturnType<typeof vi.fn>;
let postError: string | null;
let getError: boolean;
let postCount: number;

beforeEach(() => {
  state = { ...empty }; postCount = 0; postError = null; getError = false;
  fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith('/preview/open-folder')) return Promise.resolve(response({ message: '已发送打开请求' }));
    if (url.endsWith('/preview') && init?.method === 'POST') {
      postCount++;
      if (postError) return Promise.resolve(response({ detail: postError }, 409));
      state = { ...state, job: { ...job, status: 'running', progress: 37, message: '正在生成片段 2 / 4' } };
      return Promise.resolve(response(job, 202));
    }
    if (url.endsWith('/preview')) return Promise.resolve(getError ? response({ detail: '服务暂时不可用' }, 503) : response(state));
    return Promise.resolve(response(work));
  });
  vi.stubGlobal('fetch', fetchMock);
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function(this: HTMLDialogElement) { this.open = true; } });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('preview generation workflow', () => {
  it('submits the only video by asset ID, prevents double clicks and renders actual server progress without replacing edits', async () => {
    render(<WorkDetail id={7} capabilities={local} onClose={vi.fn()} onSaved={vi.fn()} notify={vi.fn()} />);
    const notes = await screen.findByLabelText('备注');
    fireEvent.change(notes, { target: { value: '没有保存的备注' } });
    const button = await screen.findByRole('button', { name: '一键生成预览' });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button); fireEvent.click(button);
    await screen.findByText('37%');
    expect(postCount).toBe(1);
    const post = fetchMock.mock.calls.find(([url, init]) => url.endsWith('/preview') && init?.method === 'POST')!;
    expect(JSON.parse(post[1].body)).toEqual({ video_asset_id: 9 });
    expect((screen.getByRole('progressbar') as HTMLProgressElement).value).toBe(37);
    expect((notes as HTMLTextAreaElement).value).toBe('没有保存的备注');
    state = { ...state, job: { ...job, status: 'completed', progress: 100 }, files: outputFiles };
    await waitFor(() => expect(screen.getAllByRole('link')).toHaveLength(8), { timeout: 3000 });
    expect(screen.queryByRole('progressbar')).toBeNull();
    expect((notes as HTMLTextAreaElement).value).toBe('没有保存的备注');
    expect(document.querySelector('video')).toBeNull();
    expect(document.querySelector('img[src$=".gif"]')).toBeNull();
  });

  it('restores the persisted active job after closing and reopening without another submission', async () => {
    state = { ...empty, job: { ...job, status: 'running', progress: 62, message: '正在处理 GIF' } };
    const first = render(<PreviewSection work={work} capabilities={local} />);
    await screen.findByText('62%');
    first.unmount();
    render(<PreviewSection work={work} capabilities={local} />);
    await screen.findByText('正在处理 GIF');
    expect((screen.getByRole('button', { name: '正在生成预览' }) as HTMLButtonElement).disabled).toBe(true);
    expect(postCount).toBe(0);
  });

  it('requires selecting a video when there are multiple candidates and ignores unavailable directories', async () => {
    const multiple = { ...work, directories: [...work.directories, { ...work.directories[0], id: 13, available: false }], assets: [...work.assets!, { ...work.assets![0], id: 10, name: 'second.mp4', relative_path: 'second.mp4' }, { ...work.assets![0], id: 11, directory_id: 13, name: 'offline.mp4', relative_path: 'offline.mp4' }] };
    render(<PreviewSection work={multiple} capabilities={local} />);
    const button = await screen.findByRole('button', { name: '一键生成预览' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    const select = screen.getByLabelText('选择源视频');
    expect(screen.getAllByRole('option')).toHaveLength(3);
    expect(screen.queryByText(/offline\.mp4/)).toBeNull();
    fireEvent.change(select, { target: { value: '10' } });
    fireEvent.click(button);
    await waitFor(() => expect(postCount).toBe(1));
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!;
    expect(JSON.parse(post[1].body)).toEqual({ video_asset_id: 10 });
  });

  it('keeps completed CLI results with no job and blocks folder opening on remote clients', async () => {
    state = { ...empty, files: outputFiles };
    render(<PreviewSection work={work} capabilities={remote} />);
    await waitFor(() => expect(screen.getAllByRole('link')).toHaveLength(8));
    expect(screen.getByRole('button', { name: '一键生成预览' })).toBeTruthy();
    expect(screen.getByText(/已有且输入未变化的结果会复用/)).toBeTruthy();
    const open = screen.getByRole('button', { name: '打开预览文件夹' });
    expect((open as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(open);
    expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/open-folder'))).toBe(false);
    expect(screen.getByText('不支持打开预览文件夹，仅素材所在主机可用。')).toBeTruthy();
    expect(screen.getAllByRole('link').every(link => link.hasAttribute('download'))).toBe(true);
  });

  it('retains prior files on a failed generation and allows retry without inventing success', async () => {
    state = { ...empty, job: { ...job, status: 'failed', error: '缺少同名脚本', progress: 14 }, files: outputFiles };
    render(<PreviewSection work={work} capabilities={local} />);
    await screen.findByText(/预览生成失败：缺少同名脚本/);
    expect(screen.getAllByRole('link')).toHaveLength(8);
    fireEvent.click(screen.getByRole('button', { name: '重试生成预览' }));
    await screen.findByText('37%');
    expect(postCount).toBe(1);
  });

  it('shows a rejected submission with retry and leaves the selected video intact', async () => {
    postError = '这个视频缺少同名脚本';
    render(<PreviewSection work={work} capabilities={local} />);
    const button = await screen.findByRole('button', { name: '一键生成预览' });
    fireEvent.click(button);
    await screen.findByText(/预览任务未提交：这个视频缺少同名脚本/);
    expect(screen.getByText('main.mp4')).toBeTruthy();
    postError = null;
    fireEvent.click(screen.getByRole('button', { name: '重试生成预览' }));
    await screen.findByText('37%');
    expect(postCount).toBe(2);
  });

  it('can retry reading unavailable persisted state without creating a duplicate task', async () => {
    getError = true;
    render(<PreviewSection work={work} capabilities={local} />);
    await screen.findByText(/无法读取预览状态：服务暂时不可用/);
    expect((screen.getByRole('button', { name: '一键生成预览' }) as HTMLButtonElement).disabled).toBe(true);
    state = { ...empty, job: { ...job, status: 'running', progress: 76 } }; getError = false;
    fireEvent.click(screen.getByRole('button', { name: '重试读取状态' }));
    await screen.findByText('76%');
    expect(postCount).toBe(0);
  });
});

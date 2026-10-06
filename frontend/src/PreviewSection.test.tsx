// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { PreviewSection } from './PreviewSection';
import { WorkDetail } from './components';
import type { Job, PreviewFile, PreviewMatching, PreviewState, Work } from './api';

const work: Work = { id: 7, script_id: 'S025_001', title: '作品标题', notes: '已有备注', status: 'pending', cover_url: null, video_count: 1, script_count: 1, updated_at: '2026-09-30T12:00:00Z', issues: [], directories: [{ id: 12, path: '/library/S025_001', windows_path: 'D:\\library\\S025_001', available: true }], assets: [{ id: 9, directory_id: 12, name: 'main.mp4', relative_path: 'main.mp4', kind: 'video', size: 1024 }] };

async function openDetailPreviewTools() {
  fireEvent.click(await screen.findByRole('tab', { name: '预览生成与匹配' }));
}
const local = { can_open_folder: true, reason: '' };
const remote = { can_open_folder: false, reason: '不支持打开，仅素材所在主机可用' };
const job: Job = { id: 44, type: 'preview', status: 'queued', progress: 0, created_at: '2026-09-30T12:00:00Z', result: { work_id: 7, script_id: 'S025_001', video_asset_id: 9 } };
const outputFiles: PreviewFile[] = Array.from({ length: 4 }, (_, index) => ['video', 'gif'].map(kind => ({ filename: `clip-${index}.${kind === 'gif' ? 'gif' : 'webm'}`, kind: kind as 'video' | 'gif', clip_index: index, width: 320, height: 180, size: 2048, url: `/api/works/7/preview/files/clip-${index}.${kind === 'gif' ? 'gif' : 'webm'}` }))).flat();
const empty: PreviewState = { job: null, files: [], output_dir: '/output/S025_001', windows_path: 'D:\\previews\\S025_001' };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
let state: PreviewState;
let matchingState: PreviewMatching;
let currentWork: Work;
let saveConflict: boolean;
let fetchMock: ReturnType<typeof vi.fn>;
let postError: string | null;
let getError: boolean;
let postCount: number;

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined);
  currentWork = work; saveConflict = false;
  matchingState = { work_id: 7, video_asset_id: 9, mode: 'auto', revision: 0, script_asset_ids: { stroke: 21 }, issues: [], videos: work.assets!, scripts: [{ id: 21, name: 'main.funscript', relative_path: 'main.funscript', directory_id: 12, kind: 'script', axis: 'stroke', size: 10 }, { id: 22, name: 'other.funscript', relative_path: 'other.funscript', directory_id: 12, kind: 'script', size: 10 }], job: null, source_changed: false };
  state = { ...empty }; postCount = 0; postError = null; getError = false;
  fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.includes('/preview-matching')) {
      if (init?.method === 'PUT') {
        if (saveConflict) { matchingState = { ...matchingState, revision: 4, script_asset_ids: { stroke: 22 }, mode: 'manual' }; return Promise.resolve(response({ detail: '对应关系已经变化' }, 409)); }
        const body = JSON.parse(init.body as string);
        matchingState = { ...matchingState, revision: matchingState.revision + 1, video_asset_id: body.video_asset_id, script_asset_ids: body.script_asset_ids, mode: 'manual' };
      }
      const requested = new URL(url, 'http://localhost').searchParams.get('video_asset_id');
      return Promise.resolve(response({ ...matchingState, ...(requested ? { video_asset_id: Number(requested) } : {}) }));
    }
    if (url.endsWith('/rematch') && init?.method === 'POST') {
      matchingState = { ...matchingState, job: { ...job, id: 45, type: 'rematch', status: 'running', message: '正在匹配当前编号' } };
      return Promise.resolve(response(matchingState.job, 202));
    }
    if (url.endsWith('/preview/open-folder')) return Promise.resolve(response({ message: '已发送打开请求' }));
    if (url.endsWith('/preview') && init?.method === 'POST') {
      postCount++;
      if (postError) return Promise.resolve(response({ detail: postError }, 409));
      state = { ...state, job: { ...job, status: 'running', progress: 37, message: '正在生成片段 2 / 4' } };
      return Promise.resolve(response(job, 202));
    }
    if (url.endsWith('/preview')) return Promise.resolve(getError ? response({ detail: '服务暂时不可用' }, 503) : response(state));
    return Promise.resolve(response(currentWork));
  });
  vi.stubGlobal('fetch', fetchMock);
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function(this: HTMLDialogElement) { this.open = true; } });
});
afterEach(() => { vi.useRealTimers(); cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('inline generated media preview', () => {
  it('keeps reassociated preview downloads and clears the stale notice when regeneration finishes', async () => {
    vi.useFakeTimers(); state = { ...empty, stale: true, files: outputFiles };
    render(<PreviewSection work={{ ...work, script_id: null, title: '普通文件夹', preview_stale: true }} capabilities={remote} />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText('素材目录已重新关联，旧手动对应关系已失效。请先核对源视频和全部轴脚本，再重新生成预览。旧预览仍可查看。')).toBeTruthy();
    expect(screen.getAllByRole('link')).toHaveLength(8);
    state = { ...state, stale: false };
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(screen.queryByText('素材目录已重新关联，旧手动对应关系已失效。请先核对源视频和全部轴脚本，再重新生成预览。旧预览仍可查看。')).toBeNull();
    expect(screen.getAllByRole('link')).toHaveLength(8);
  });
  const heatmap: PreviewFile = { filename: '热力图.png', kind: 'heatmap', clip_index: 0, width: 2048, height: 690, size: 4096, url: '/api/works/7/preview/files/heatmap.png?v=first' };
  it('loads only the requested video, GIF or heatmap and keeps separate download links', async () => {
    state = { ...empty, files: [...outputFiles.map(file => ({ ...file, url: `${file.url}?v=first` })), heatmap] };
    render(<PreviewSection work={work} capabilities={remote} />);
    await screen.findByRole('button', { name: '查看完整时长热力图' });
    expect(document.querySelector('video, img')).toBeNull();
    expect(screen.getAllByRole('link')).toHaveLength(9);
    fireEvent.click(screen.getByRole('button', { name: '查看片段 1 WebM' }));
    const video = document.querySelector('video')!;
    expect(video.getAttribute('src')).toBe(`${outputFiles[0].url}?v=first&inline=1`);
    expect(video.controls).toBe(true);
    expect(video.preload).toBe('metadata');
    expect(video.autoplay).toBe(false);
    expect(video.hasAttribute('playsinline')).toBe(true);
    expect(document.querySelector('img')).toBeNull();
    fireEvent.loadedMetadata(video);
    expect(screen.queryByText(/正在加载片段/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '查看片段 1 GIF' }));
    expect(document.querySelector('video')).toBeNull();
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledOnce();
    expect(HTMLMediaElement.prototype.load).toHaveBeenCalledOnce();
    const gif = screen.getByRole('img', { name: '片段 1 · GIF' });
    expect(gif.getAttribute('src')).toBe(`${outputFiles[1].url}?v=first&inline=1`);
    fireEvent.load(gif);
    fireEvent.click(screen.getByRole('button', { name: '查看完整时长热力图' }));
    expect(screen.queryByRole('img', { name: '片段 1 · GIF' })).toBeNull();
    expect(screen.getByRole('img', { name: '完整时长热力图' }).getAttribute('src')).toBe(`${heatmap.url}&inline=1`);
    fireEvent.click(screen.getByRole('button', { name: '原尺寸查看' }));
    expect(document.querySelector('.inline-preview-media.zoomed')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '适应宽度' }));
    expect(document.querySelector('.inline-preview-media.zoomed')).toBeNull();
    expect(screen.getByRole('link', { name: '下载完整时长热力图' }).getAttribute('href')).toBe(heatmap.url);
    fireEvent.click(screen.getByRole('button', { name: '关闭内容预览' }));
    expect(document.querySelector('video, img')).toBeNull();
    expect(screen.getAllByRole('link')).toHaveLength(9);
    expect(screen.getAllByRole('link').every(link => link.hasAttribute('download'))).toBe(true);
  });

  it('keeps the selected media during polling and remounts it when its version changes', async () => {
    state = { ...empty, files: [{ ...outputFiles[0], url: `${outputFiles[0].url}?v=first` }] };
    vi.useFakeTimers();
    await act(async () => { render(<PreviewSection work={work} capabilities={local} />); });
    fireEvent.click(screen.getByRole('button', { name: '查看片段 1 WebM' }));
    const video = document.querySelector('video')!;
    fireEvent.loadedMetadata(video);
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(document.querySelector('video')).toBe(video);
    expect(screen.queryByText(/正在加载片段/)).toBeNull();
    state = { ...state, files: [{ ...state.files[0], url: `${outputFiles[0].url}?v=second` }] };
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    const replacement = document.querySelector('video')!;
    expect(replacement).not.toBe(video);
    expect(replacement.getAttribute('src')).toBe(`${outputFiles[0].url}?v=second&inline=1`);
    expect(screen.getByText(/正在加载片段/)).toBeTruthy();
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: '查看片段 1 WebM' }).getAttribute('aria-pressed')).toBe('true');
  });

  it('shows media errors without removing downloads and closing stops the video', async () => {
    state = { ...empty, files: outputFiles };
    const view = render(<PreviewSection work={work} capabilities={local} />);
    fireEvent.click(await screen.findByRole('button', { name: '查看片段 2 WebM' }));
    fireEvent.error(document.querySelector('video')!);
    expect(screen.getByText(/内容加载失败/)).toBeTruthy();
    expect(screen.queryByText(/正在加载片段/)).toBeNull();
    expect(screen.getAllByRole('link')).toHaveLength(9);
    view.unmount();
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledOnce();
    expect(HTMLMediaElement.prototype.load).toHaveBeenCalledOnce();
  });
});

describe('preview generation workflow', () => {
  it('shows the full-length heatmap separately from four clips and keeps legacy results usable', async () => {
    state = { ...empty, files: [...outputFiles, { filename: '热力图.png', kind: 'heatmap', clip_index: 0,
      width: 2048, height: 1002, size: 4096, url: '/api/works/7/preview/files/heatmap.png' }] };
    render(<PreviewSection work={work} capabilities={local} />);
    const png = await screen.findByRole('link', { name: /完整时长热力图/ });
    expect(png.getAttribute('download')).toBe('热力图.png');
    expect(screen.getByText('9 个文件')).toBeTruthy();
    expect(document.querySelectorAll('.preview-clip')).toHaveLength(5);
    expect(document.querySelectorAll('.preview-clip > span')[4].textContent).toBe('热力图');
    expect(screen.queryByText('片段 5')).toBeNull();
    expect(document.querySelector('img')).toBeNull();
    expect(screen.queryByText(/现有结果尚无热力图/)).toBeNull();
  });

  it('submits the only video by asset ID, prevents double clicks and renders actual server progress without replacing edits', async () => {
    render(<WorkDetail id={7} capabilities={local} onClose={vi.fn()} onSaved={vi.fn()} notify={vi.fn()} />);
    const notes = await screen.findByLabelText('备注');
    fireEvent.change(notes, { target: { value: '没有保存的备注' } });
    await openDetailPreviewTools();
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
    matchingState = { ...matchingState, video_asset_id: null, videos: multiple.assets.filter(asset => asset.directory_id === 12) };
    render(<PreviewSection work={multiple} capabilities={local} />);
    const button = await screen.findByRole('button', { name: '一键生成预览' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    const select = screen.getByLabelText('选择源视频');
    expect((select as HTMLSelectElement).options).toHaveLength(3);
    expect(screen.queryByText(/offline\.mp4/)).toBeNull();
    fireEvent.change(select, { target: { value: '10' } });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
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
    expect(screen.getByText(/现有结果尚无热力图/)).toBeTruthy();
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

describe('file mapping and forced regeneration', () => {
  it('forces regeneration even with existing results and keeps their download links while running', async () => {
    state = { ...empty, files: outputFiles };
    render(<PreviewSection work={work} capabilities={local} />);
    const regenerate = await screen.findByRole('button', { name: '重新生成预览' });
    await waitFor(() => expect((regenerate as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(regenerate); fireEvent.click(regenerate);
    await screen.findByText('37%');
    expect(postCount).toBe(1);
    const post = fetchMock.mock.calls.find(([url, init]) => url.endsWith('/preview') && init?.method === 'POST')!;
    expect(JSON.parse(post[1].body)).toEqual({ video_asset_id: 9, force: true });
    expect(screen.getAllByRole('link')).toHaveLength(8);
    expect((screen.getByRole('button', { name: '重新匹配文件' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('saves each axis selection with revision and blocks generation until changes are saved', async () => {
    render(<PreviewSection work={work} capabilities={local} />);
    const pitch = await screen.findByLabelText('Pitch · 俯仰');
    fireEvent.change(pitch, { target: { value: '22' } });
    expect((screen.getByRole('button', { name: '一键生成预览' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '重新匹配文件' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '保存对应关系' }));
    await screen.findByText('视频与各轴脚本的对应关系已保存。');
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT')!;
    expect(JSON.parse(put[1].body)).toEqual({ video_asset_id: 9, script_asset_ids: { stroke: 21, pitch: 22 }, expected_revision: 0 });
    expect(screen.getByText('手动指定')).toBeTruthy();
    expect((screen.getByRole('button', { name: '一键生成预览' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('refreshes authoritative mappings after revision conflicts without retrying the stale write', async () => {
    saveConflict = true;
    render(<PreviewSection work={work} capabilities={local} />);
    fireEvent.change(await screen.findByLabelText('Pitch · 俯仰'), { target: { value: '22' } });
    fireEvent.click(screen.getByRole('button', { name: '保存对应关系' }));
    await screen.findByText(/对应关系已在其他页面更新/);
    await waitFor(() => expect((screen.getByLabelText('Stroke · 主轴') as HTMLSelectElement).value).toBe('22'));
    expect((screen.getByLabelText('Pitch · 俯仰') as HTMLSelectElement).value).toBe('');
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1);
    expect((screen.getByRole('button', { name: '保存对应关系' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('rematches only the current work, refreshes discovered assets and retains title and notes drafts', async () => {
    const saved = vi.fn();
    const author = { id: 1, category: 'author' as const, name: 'Author', support_status: 'unknown' as const, support_url: null, revision: 0, usage_count: 1 };
    const single = { ...author, id: 2, category: 'axis_type' as const, name: '单轴' };
    const multi = { ...single, id: 3, name: '多轴' };
    currentWork = { ...work, axis_type: '单轴', tags: [author, single], tags_revision: 1 };
    render(<WorkDetail id={7} capabilities={local} onClose={vi.fn()} onSaved={saved} notify={vi.fn()} />);
    const notes = await screen.findByLabelText('备注');
    const hero = document.querySelector<HTMLElement>('.work-detail-hero-content')!;
    expect(screen.queryByRole('radio')).toBeNull();
    expect(within(hero).getByText('D:\\library\\S025_001')).toBeTruthy();
    const title = screen.getByLabelText('标题');
    fireEvent.change(notes, { target: { value: '没有保存的备注' } });
    fireEvent.change(title, { target: { value: '正在修改的标题' } });
    await openDetailPreviewTools();
    const rematch = await screen.findByRole('button', { name: '重新匹配文件' });
    await waitFor(() => expect((rematch as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(rematch); fireEvent.click(rematch);
    await screen.findByText('正在匹配当前编号');
    expect((screen.getByRole('button', { name: '重新生成预览' }) as HTMLButtonElement).disabled).toBe(true);
    expect(fetchMock.mock.calls.filter(([url, init]) => url === '/api/works/7/rematch' && init?.method === 'POST')).toHaveLength(1);
    currentWork = { ...work, script_count: 2, axis_type: '多轴', tags: [author, multi], tags_revision: 2,
      directories: [{ ...work.directories[0], id: 13, path: '/archive/S025_001', windows_path: 'D:\\archive\\S025_001' }], assets: [...work.assets!, matchingState.scripts[1]] };
    matchingState = { ...matchingState, source_changed: true, job: { ...matchingState.job!, status: 'completed', message: '文件重新匹配已完成' } };
    await screen.findByText('2 个脚本', {}, { timeout: 3000 });
    expect((title as HTMLInputElement).value).toBe('正在修改的标题');
    expect((notes as HTMLTextAreaElement).value).toBe('没有保存的备注');
    expect(within(hero).getByText('D:\\archive\\S025_001')).toBeTruthy();
    expect(screen.queryByText('D:\\library\\S025_001')).toBeNull();
    expect(screen.queryByText('轴类型 单轴')).toBeNull();
    expect(within(hero).getByText('多轴', { selector: '.tag-chip' })).toBeTruthy();
    expect(within(hero).getByText('Author', { selector: '.tag-chip' })).toBeTruthy();
    expect(saved).toHaveBeenCalledOnce();
    expect(postCount).toBe(0);
  });

  it('keeps rematch failure separate from a completed preview and warns if source inputs changed', async () => {
    state = { ...empty, files: outputFiles, job: { ...job, status: 'completed', progress: 100 } };
    matchingState = { ...matchingState, source_changed: true, job: { ...job, type: 'rematch', status: 'failed', error: '扫描目录不可用' }, issues: ['主轴脚本不存在'] };
    render(<PreviewSection work={work} capabilities={local} />);
    await screen.findByText('重新匹配失败：扫描目录不可用');
    expect(screen.getByText('预览任务已完成')).toBeTruthy();
    expect(screen.getByText('源视频或脚本已变化，现有预览需要重新生成。')).toBeTruthy();
    expect(screen.getAllByRole('link')).toHaveLength(8);
    expect((screen.getByRole('button', { name: '重新生成预览' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('allows persisting an empty selection, prevents generation without scripts and restores saved selections after reopening', async () => {
    const first = render(<PreviewSection work={work} capabilities={local} />);
    fireEvent.change(await screen.findByLabelText('Stroke · 主轴'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: '保存对应关系' }));
    await screen.findByText('视频与各轴脚本的对应关系已保存。');
    expect((screen.getByRole('button', { name: '重新生成预览' }) as HTMLButtonElement).disabled).toBe(true);
    first.unmount();
    render(<PreviewSection work={work} capabilities={local} />);
    expect((await screen.findByLabelText('Stroke · 主轴') as HTMLSelectElement).value).toBe('');
    expect(screen.getByText('手动指定')).toBeTruthy();
    expect(postCount).toBe(0);
  });
});
it('keeps the draft base revision when polling observes an update from another page', async () => {
  vi.useFakeTimers();
  await act(async () => { render(<PreviewSection work={work} capabilities={local} />); });
  fireEvent.change(screen.getByLabelText('Pitch · 俯仰'), { target: { value: '22' } });
  matchingState = { ...matchingState, revision: 5, script_asset_ids: { stroke: 22 }, mode: 'manual' };
  const before = fetchMock.mock.calls.filter(([url]) => url.includes('/preview-matching')).length;
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  vi.useRealTimers();
  expect(fetchMock.mock.calls.filter(([url]) => url.includes('/preview-matching')).length).toBeGreaterThan(before);
  expect((screen.getByLabelText('Pitch · 俯仰') as HTMLSelectElement).value).toBe('22');
  expect((screen.getByLabelText('Stroke · 主轴') as HTMLSelectElement).value).toBe('21');
  saveConflict = true;
  fireEvent.click(screen.getByRole('button', { name: '保存对应关系' }));
  await screen.findByText(/对应关系已在其他页面更新/);
  const put = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT')!;
  expect(JSON.parse(put[1].body).expected_revision).toBe(0);
});

it('warns before closing with an unsaved mapping and does not submit it through the text save button', async () => {
  const onClose = vi.fn();
  render(<WorkDetail id={7} capabilities={local} onClose={onClose} onSaved={vi.fn()} notify={vi.fn()} />);
  await openDetailPreviewTools();
  fireEvent.change(await screen.findByLabelText('Pitch · 俯仰'), { target: { value: '22' } });
  fireEvent.click(screen.getByRole('tab', { name: '资料与标签' }));
  fireEvent.click(screen.getByRole('button', { name: '关闭作品详情' }));
  await screen.findByText('有尚未保存的修改');
  expect(onClose).not.toHaveBeenCalled();
  expect((screen.getByRole('button', { name: '保存信息' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
  expect((screen.getByLabelText('Pitch · 俯仰') as HTMLSelectElement).value).toBe('22');
});
it('allows choosing a single replacement when the original selected video has disappeared', async () => {
  render(<PreviewSection work={work} capabilities={local} />);
  await screen.findByLabelText('Stroke · 主轴');
  fireEvent.click(screen.getByRole('button', { name: '重新匹配文件' }));
  await screen.findByText('正在匹配当前编号');
  matchingState = { ...matchingState, video_asset_id: null, videos: [{ ...work.assets![0], id: 10, name: 'replacement.mp4', relative_path: 'replacement.mp4' }], issues: ['所选视频已消失，请选择源视频'], job: { ...matchingState.job!, status: 'completed', message: '文件重新匹配已完成' } };
  // The server reports the invalid former choice as null while retaining candidates.
  const originalFetch = fetchMock.getMockImplementation()! as (url: string, init?: RequestInit) => Promise<Response>;
  fetchMock.mockImplementation((url: string, init?: RequestInit) => url.includes('preview-matching?video_asset_id=9') ? Promise.resolve(response(matchingState)) : originalFetch(url, init));
  const select = await screen.findByLabelText('选择源视频', {}, { timeout: 3000 });
  expect((select as HTMLSelectElement).options).toHaveLength(2);
  fireEvent.change(select, { target: { value: '10' } });
  await waitFor(() => expect(screen.getByText('replacement.mp4')).toBeTruthy());
  expect((screen.getByLabelText('Stroke · 主轴') as HTMLSelectElement).value).toBe('21');
});
it('can discard a mapping draft if its video vanishes so rematching is never locked out', async () => {
  vi.useFakeTimers();
  await act(async () => { render(<PreviewSection work={work} capabilities={local} />); });
  fireEvent.change(screen.getByLabelText('Pitch · 俯仰'), { target: { value: '22' } });
  matchingState = { ...matchingState, video_asset_id: null, videos: [], scripts: [], script_asset_ids: {}, issues: ['所选视频已消失'] };
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  vi.useRealTimers();
  const discard = screen.getByRole('button', { name: '放弃调整' });
  expect((screen.getByRole('button', { name: '重新匹配文件' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(discard);
  expect((screen.getByRole('button', { name: '重新匹配文件' }) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.queryByRole('button', { name: '放弃调整' })).toBeNull();
});
it('retries refreshing the work source data if the post-rematch detail request failed', async () => {
  matchingState = { ...matchingState, job: { ...job, id: 45, type: 'rematch', status: 'completed' } };
  const refresh = vi.fn().mockRejectedValueOnce(new Error('详情暂时不可读取')).mockResolvedValue(undefined);
  render(<PreviewSection work={work} capabilities={local} onSourcesChanged={refresh} />);
  await screen.findByText('无法读取文件匹配：详情暂时不可读取');
  fireEvent.click(screen.getByRole('button', { name: '重试读取文件匹配' }));
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByText('无法读取文件匹配：详情暂时不可读取')).toBeNull());
});

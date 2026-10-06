// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { WorkDetail } from './components';
import { PreviewSection } from './PreviewSection';
import type { PreviewState, Work } from './api';

const work: Work = { id: 7, script_id: 'S070', title: '作品标题', notes: '', status: 'pending', cover_url: '/api/covers/7', video_count: 1, script_count: 1, issues: [], updated_at: '',
  directories: [{ id: 12, path: '/library/S070', windows_path: 'D:\\library\\S070', available: true }],
  assets: [{ id: 8, name: 'project.ofsp', relative_path: 'project.ofsp', kind: 'other', size: 512, directory_id: 12 },
    { id: 9, name: 'main.mp4', relative_path: 'videos/main.mp4', kind: 'video', size: 1024, directory_id: 12 },
    { id: 10, name: 'main.pitch.funscript', relative_path: 'main.pitch.funscript', kind: 'script', axis: 'pitch', size: 2048, directory_id: 12 }] };
const preview: PreviewState = { job: null, output_dir: '/previews/S070', windows_path: 'D:\\previews\\S070', files: [
  { filename: 'clip-01.webm', kind: 'video', clip_index: 0, width: 640, height: 360, size: 8192, url: '/api/works/7/preview/files/clip-01.webm' },
  { filename: 'clip-01.gif', kind: 'gif', clip_index: 0, width: 320, height: 180, size: 4096, url: '/api/works/7/preview/files/clip-01.gif' },
  { filename: 'heatmap.png', kind: 'heatmap', clip_index: -1, width: 1200, height: 240, size: 2048, url: '/api/works/7/preview/files/heatmap.png' },
] };
beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  vi.stubGlobal('fetch', vi.fn((url: string) => Promise.resolve(new Response(JSON.stringify(url === '/api/jobs' ? { items: [] } : url.endsWith('/preview') ? preview : url.includes('/preview-matching') ? {
    work_id: 7, video_asset_id: 9, mode: 'manual', revision: 1, script_asset_ids: { pitch: 10 }, issues: [], videos: [work.assets!.find(asset => asset.kind === 'video')], scripts: [work.assets!.find(asset => asset.kind === 'script')], job: null, source_changed: false,
  } : work), { headers: { 'Content-Type': 'application/json' } }))));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const page = (supported = false) => render(<WorkDetail id={7} presentation="page" capabilities={{ can_open_folder: supported, can_play_video: supported, reason: '' }} onClose={() => {}} onSaved={() => {}} notify={() => {}} />);

it('browses real video, preview and script files in separate groups and uses the local source player', async () => {
  const view = page(true); await screen.findByLabelText('标题');
  fireEvent.click(screen.getByRole('tab', { name: '素材' }));
  const groups = view.container.querySelectorAll('.detail-assets-columns .source-asset-group > header h3');
  expect([...groups].map(heading => heading.textContent)).toEqual(['视频素材', '预览素材', '脚本素材']);
  await screen.findByRole('button', { name: /clip-01.webm/ });
  const inspector = screen.getByRole('region', { name: '素材详情' });
  expect(within(inspector).getByText('main.mp4')).toBeTruthy();
  const source = inspector.querySelector('video')!;
  expect(source.getAttribute('src')).toBe('/api/works/7/assets/9/media');
  expect(source.controls).toBe(true); expect(source.autoplay).toBe(false);
  expect(within(inspector).getByText('D:\\library\\S070\\videos\\main.mp4')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /main.pitch.funscript/ }));
  expect(within(inspector).getByText('main.pitch.funscript')).toBeTruthy();
  expect(within(inspector).getByText('pitch')).toBeTruthy();
  expect(within(inspector).getByText('2.0 KB')).toBeTruthy();
});

it('uses the existing manual-play preview renderer and supports actual GIF and heatmap files', async () => {
  page(); await screen.findByLabelText('标题'); fireEvent.click(screen.getByRole('tab', { name: '素材' }));
  fireEvent.click(await screen.findByRole('button', { name: /clip-01.webm/ }));
  const inspector = screen.getByRole('region', { name: '素材详情' });
  const video = inspector.querySelector('video')!;
  expect(video.getAttribute('src')).toBe('/api/works/7/preview/files/clip-01.webm?inline=1');
  expect(video.controls).toBe(true); expect(video.autoplay).toBe(false);
  expect(within(inspector).getByText('640 × 360')).toBeTruthy();
  expect(within(inspector).getByText('D:\\previews\\S070\\clip-01.webm')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /clip-01.gif/ }));
  expect(within(inspector).getByRole('img').getAttribute('src')).toBe('/api/works/7/preview/files/clip-01.gif?inline=1');
  fireEvent.click(screen.getByRole('button', { name: /heatmap.png/ }));
  expect(within(inspector).getByRole('img', { name: '完整时长热力图' })).toBeTruthy();
  expect(within(inspector).getByRole('button', { name: '原尺寸查看' })).toBeTruthy();
});

it('keeps generation separate from browsing and retains source mapping drafts across tabs', async () => {
  const view = page(); await screen.findByLabelText('标题'); fireEvent.click(screen.getByRole('tab', { name: '素材' }));
  expect(view.container.querySelector('.preview-matching')).toBeNull();
  fireEvent.click(screen.getByRole('tab', { name: '预览生成与匹配' }));
  await screen.findByLabelText('Pitch · 俯仰');
  fireEvent.change(screen.getByLabelText('Pitch · 俯仰'), { target: { value: '' } });
  fireEvent.click(screen.getByRole('tab', { name: '素材' }));
  expect(screen.queryByRole('button', { name: '保存对应关系' })).toBeNull();
  fireEvent.click(screen.getByRole('tab', { name: '预览生成与匹配' }));
  expect((screen.getByLabelText('Pitch · 俯仰') as HTMLSelectElement).value).toBe('');
  fireEvent.click(screen.getByRole('tab', { name: '资料与标签' }));
  fireEvent.click(screen.getByRole('button', { name: '返回库存' }));
  expect(screen.getByText('有尚未保存的修改')).toBeTruthy();
});

it('keeps source playback unavailable on a remote client', async () => {
  page(); await screen.findByLabelText('标题'); fireEvent.click(screen.getByRole('tab', { name: '素材' }));
  const inspector = screen.getByRole('region', { name: '素材详情' });
  expect(inspector.querySelector('video')).toBeNull();
  expect(within(inspector).getByText('原视频播放仅在素材所在本机可用。')).toBeTruthy();
});
it('reports actual preview state through the optional stable callback', async () => {
  const onState = vi.fn();
  render(<PreviewSection work={work} capabilities={{ can_open_folder: false, reason: '' }} onPreviewStateChanged={onState} />);
  await waitFor(() => expect(onState).toHaveBeenCalledOnce());
  expect(onState).toHaveBeenCalledWith(preview);
});

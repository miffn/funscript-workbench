// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { PreviewSection } from './PreviewSection';
import { beijingToday, ReleaseCalendar } from './ReleaseCalendar';
import { setLanguage } from './i18n';
import type { CalendarWork, PreviewMatching, PreviewState, Work } from './api';

const response = (value: unknown) => Promise.resolve(new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } }));
const calendarWork: CalendarWork = { id: 7, script_id: 'S007', title: '中文作品标题', revision: 'r1', es_published: false, patreon_published: false, es_published_date: null, patreon_published_date: null, es_planned_date: null, patreon_planned_date: null };
const previewWork: Work = { id: 7, script_id: 'S007', title: '中文作品标题', notes: '', status: 'pending', cover_url: null, video_count: 1, script_count: 1, updated_at: '', issues: [], directories: [] };
beforeEach(() => {
  setLanguage({ language: 'en', revision: 1 });
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined);
});
afterEach(() => { cleanup(); setLanguage({ language: 'zh-CN', revision: 0 }); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

it('translates calendar controls and dates while preserving Beijing today and user titles', async () => {
  vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(new Date('2026-10-02T18:00:00Z'));
  vi.stubGlobal('fetch', vi.fn(() => response({ month: '2026-10', today: '2026-10-03', works: [calendarWork], events: [] })));
  render(<ReleaseCalendar onSelect={vi.fn()} onChanged={vi.fn()} />);
  await screen.findByRole('button', { name: 'Add to this day S007' });
  expect(beijingToday()).toBe('2026-10-03');
  expect(screen.getByRole('heading', { name: 'October 2026' })).toBeTruthy();
  expect(screen.getByRole('heading', { name: 'Records for October 3' })).toBeTruthy();
  expect(screen.getByText('Mon')).toBeTruthy();
  expect(screen.getByLabelText('Filter by platform')).toBeTruthy();
  expect(screen.getByRole('button', { name: '2026-10-03' }).getAttribute('aria-pressed')).toBe('true');
  expect(screen.getByText('中文作品标题')).toBeTruthy();
  act(() => setLanguage({ language: 'zh-CN', revision: 2 }));
  expect(screen.getByRole('heading', { name: '2026 年 10 月' })).toBeTruthy();
  expect(screen.getByRole('button', { name: '添加到当天 S007' })).toBeTruthy();
  expect(screen.getByText('中文作品标题')).toBeTruthy();
});

it('translates preview actions, axis labels and selected media without changing filenames', async () => {
  const video = { id: 9, directory_id: 12, name: '原始视频.mp4', relative_path: '原始视频.mp4', kind: 'video' as const, size: 1024 };
  const matching: PreviewMatching = { work_id: 7, video_asset_id: 9, mode: 'auto', revision: 0, script_asset_ids: { stroke: 21 }, videos: [video], scripts: [{ id: 21, directory_id: 12, name: '原始视频.funscript', relative_path: '原始视频.funscript', kind: 'script', axis: 'stroke', size: 100 }], issues: [], job: null, source_changed: false };
  const state: PreviewState = { job: null, files: [{ filename: '预览gif1.gif', kind: 'gif', clip_index: 1, width: 192, height: 108, size: 2000, url: '/api/works/7/preview/files/gif' }], output_dir: '/previews/S007', windows_path: 'D:\\预览\\S007' };
  vi.stubGlobal('fetch', vi.fn((url: string) => response(url.includes('preview-matching') ? matching : state)));
  render(<PreviewSection work={previewWork} capabilities={{ can_open_folder: false, reason: '' }} />);
  await screen.findByRole('button', { name: 'View clip 1 GIF' });
  expect(screen.getByRole('heading', { name: 'Preview generation' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Regenerate previews' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Rematch files' })).toBeTruthy();
  expect(screen.getByLabelText('Stroke · Main axis')).toBeTruthy();
  expect(screen.getByText('原始视频.mp4')).toBeTruthy();
  expect(screen.getAllByRole('option', { name: '原始视频.funscript' }).length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole('button', { name: 'View clip 1 GIF' }));
  expect(screen.getByRole('img', { name: 'Clip 1 · GIF' })).toBeTruthy();
  expect(screen.getByRole('link', { name: /Download Clip 1/ }).getAttribute('download')).toBe('预览gif1.gif');
  act(() => setLanguage({ language: 'zh-CN', revision: 2 }));
  expect(screen.getByRole('img', { name: '片段 1 · GIF' })).toBeTruthy();
  expect(screen.getByRole('button', { name: '关闭内容预览' })).toBeTruthy();
});


it('translates stored preview errors again when the language changes', async () => {
  const matching: PreviewMatching = { work_id: 7, video_asset_id: 9, mode: 'auto', revision: 0, script_asset_ids: { stroke: 21 }, videos: [{ id: 9, directory_id: 12, name: '源视频.mp4', relative_path: '源视频.mp4', kind: 'video', size: 1000 }], scripts: [{ id: 21, directory_id: 12, name: '脚本.funscript', relative_path: '脚本.funscript', kind: 'script', axis: 'stroke', size: 100 }], issues: [], job: null, source_changed: false };
  const state: PreviewState = { job: null, files: [], output_dir: '/previews/S007', windows_path: 'D:\\预览\\S007' };
  const error = '文件匹配已被其他页面修改，请刷新后重新确认';
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => init?.method === 'POST'
    ? Promise.resolve(new Response(JSON.stringify({ detail: error }), { status: 409, headers: { 'Content-Type': 'application/json' } }))
    : response(url.includes('preview-matching') ? matching : state)));
  render(<PreviewSection work={previewWork} capabilities={{ can_open_folder: false, reason: '' }} />);
  const generate = await screen.findByRole('button', { name: 'Generate previews' });
  await waitFor(() => expect((generate as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(generate);
  expect((await screen.findByRole('alert')).textContent).toBe('Could not start the preview task: File matching changed in another page. Refresh and review it again.');
  act(() => setLanguage({ language: 'zh-CN', revision: 2 }));
  expect(screen.getByRole('alert').textContent).toBe(`预览任务未提交：${error}`);
});

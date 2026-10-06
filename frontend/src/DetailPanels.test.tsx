// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Cover, JobsPage, PublicationBadges } from './components';
import type { Job, Work } from './api';

const work = { id: 7, script_id: 'S070', title: 'Work', status: 'pending', es_published: true, patreon_published: false, es_published_date: '2026-10-05', duration_seconds: 2352, cover_url: null } as Work;
const jobs: Job[] = [
  { id: 1, type: 'scan', status: 'completed', created_at: '2026-10-05T04:00:00Z', result: { works: 42 } },
  { id: 2, type: 'preview', status: 'failed', created_at: '2026-10-05T05:00:00Z', inputs: { script_id: 'S070' }, error: '缺少源文件', result: { clip_count: 2 } },
  { id: 3, type: 'rematch', status: 'running', created_at: '2026-10-05T06:00:00Z', progress: 37, message: '正在读取素材' },
];
beforeEach(() => vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({ items: jobs }), { headers: { 'Content-Type': 'application/json' } })))));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('merges publication date with platform and keeps status accessible without duplicate visible text', () => {
  render(<PublicationBadges work={work} />);
  const es = screen.getByLabelText('ES · 已发布 · 2026-10-05');
  expect(es.textContent).toBe('ES · 10/05');
  expect(es.className).toContain('published');
  const patreon = screen.getByLabelText('Patreon · 待发布');
  expect(patreon.textContent).toBe('Patreon');
  expect(patreon.className).toContain('pending');
});

it('shows verified duration on the cover while keeping unavailable duration empty', () => {
  const view = render(<Cover work={work} />);
  expect(screen.getByLabelText('时长').textContent).toBe('39:12');
  view.rerender(<Cover work={{ ...work, duration_seconds: null }} />);
  expect(screen.queryByLabelText('时长')).toBeNull();
});

it('filters real job states and types and reveals server results and errors on expansion', async () => {
  render(<JobsPage revision={0} />);
  await screen.findByRole('button', { name: /文件重新匹配 #3/ });
  expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('37');
  expect(screen.queryByText('缺少源文件')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /^失败\s*1$/ }));
  expect(screen.queryByRole('button', { name: /文件重新匹配 #3/ })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /预览生成 #2/ }));
  expect(screen.getByRole('alert').textContent).toBe('缺少源文件');
  expect(screen.getByText('片段')).toBeTruthy();
  expect(screen.getByText('2', { selector: '.job-results strong' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /^全部\s*3$/ }));
  fireEvent.change(screen.getByLabelText('任务类型'), { target: { value: 'scan' } });
  expect(screen.getByRole('button', { name: /库存扫描 #1/ })).toBeTruthy();
  expect(screen.queryByRole('button', { name: /预览生成 #2/ })).toBeNull();
  fireEvent.change(screen.getByLabelText('任务类型'), { target: { value: 'all' } });
  fireEvent.change(screen.getByLabelText('搜索任务'), { target: { value: 'S070' } });
  expect(screen.getByRole('button', { name: /预览生成 #2/ })).toBeTruthy();
  expect(screen.queryByRole('button', { name: /库存扫描 #1/ })).toBeNull();
});
// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';
import type { Work } from './api';

const work: Work = { id: 4, script_id: 'S064', title: '示例', status: 'pending', es_published: true, patreon_published: false, video_count: 1, script_count: 2, cover_url: null, directories: [], issues: [], updated_at: '' };
const response = (value: unknown) => Promise.resolve(new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } }));
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  window.location.hash = '#/inventory';
  localStorage.clear();
  fetchMock = vi.fn((url: string) => response(url.startsWith('/api/works?') ? { items: [work], total: 1, page: 1, page_size: 24, stats: { total: 10, pending: 4, published: 2, es_published: 5, patreon_published: 3, issues: 0 }, last_scan: null } : url === '/api/capabilities' ? { can_open_folder: false, reason: '' } : { items: [] }));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); window.location.hash = ''; });

it('filters independently by ES and Patreon and displays independent counts', async () => {
  render(<App />);
  const es = await screen.findByRole('button', { name: /ES 已发布\s*5/ });
  expect(screen.getByRole('button', { name: /Patreon 已发布\s*3/ })).toBeTruthy();
  fireEvent.click(es);
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.includes('status=es_published'))).toBe(true));
  expect(screen.getByRole('heading', { name: 'ES 已发布作品' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /Patreon 已发布\s*3/ }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.includes('status=patreon_published'))).toBe(true));
  expect(screen.getByRole('heading', { name: 'Patreon 已发布作品' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /待发布\s*4/ }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.includes('status=pending'))).toBe(true));
});

it('shows both status badges in gallery, list and the quick tag view', async () => {
  render(<App />);
  await screen.findByText('ES 已发布', { selector: '.badge' });
  expect(screen.getByText('Patreon 待发布', { selector: '.badge' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '紧凑目录' }));
  expect(screen.getByText('ES 已发布', { selector: '.badge' })).toBeTruthy();
  expect(screen.getByText('Patreon 待发布', { selector: '.badge' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '标签列表' }));
  expect(screen.getByText('ES 已发布', { selector: '.badge' })).toBeTruthy();
  expect(screen.getByText('Patreon 待发布', { selector: '.badge' })).toBeTruthy();
  expect(screen.queryByRole('img')).toBeNull();
});

it('opens a saved platform route directly', async () => {
  window.location.hash = '#/patreon_published';
  render(<App />);
  await screen.findByText('Patreon 待发布', { selector: '.badge' });
  expect(fetchMock.mock.calls.some(([url]) => url.includes('status=patreon_published'))).toBe(true);
  expect(screen.getByRole('heading', { name: 'Patreon 已发布作品' })).toBeTruthy();
});

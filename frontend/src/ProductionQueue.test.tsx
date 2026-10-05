// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';
import { setLanguage } from './i18n';

const waiting = { id: 1, script_id: 'S901', title: '制作中的作品', status: 'pending', cover_url: null, video_count: 1, script_count: 0, directories: [], issues: [], updated_at: '2026-10-05', production_required: true };
const ready = { ...waiting, id: 2, script_id: 'S902', title: '完成的作品', script_count: 1, production_required: false };
const response = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  window.location.hash = '#/inventory'; setLanguage({ language: 'zh-CN', revision: 0 });
  fetchMock = vi.fn(async (url: string) => {
    if (url.startsWith('/api/works')) {
      const status = new URL(url, 'http://localhost').searchParams.get('status');
      const items = status === 'to_make' ? [waiting] : status === 'pending' ? [ready] : [waiting, ready];
      return response({ items, total: items.length, stats: { total: 2, to_make: 1, pending: 1, published: 0, issues: 0 }, last_scan: null });
    }
    return response(url === '/api/profile' ? { name: 'Name', bio: '', avatar: null, revision: 0 } : url === '/api/capabilities' ? { can_open_folder: false, reason: '' } : { items: [] });
  });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); act(() => setLanguage({ language: 'zh-CN', revision: 0 })); });

it('shows a separate production count and opens the correct filter before pending releases', async () => {
  render(<App />);
  const production = await screen.findByRole('button', { name: /待制作.*1/ });
  const pending = screen.getByRole('button', { name: /待发布.*1/ });
  expect(production.compareDocumentPosition(pending) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(production);
  await screen.findByRole('heading', { name: '待制作库存' });
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.includes('status=to_make'))).toBe(true));
  await screen.findByText('制作中的作品');
  expect(screen.queryByText('完成的作品')).toBeNull();
  fireEvent.click(pending);
  await screen.findByText('完成的作品');
  expect(screen.queryByText('制作中的作品')).toBeNull();
});

it('restores the production route directly and supports English labels', async () => {
  window.location.hash = '#/to_make'; setLanguage({ language: 'en', revision: 1 });
  render(<App />);
  await screen.findByRole('heading', { name: 'Production queue' });
  expect(screen.getByRole('button', { name: /Production queue.*1/ }).getAttribute('aria-current')).toBe('page');
  expect(fetchMock.mock.calls.some(([url]) => url.includes('status=to_make'))).toBe(true);
  expect(screen.getByText('制作中的作品')).toBeTruthy();
});

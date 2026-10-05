// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';
import { setLanguage } from './i18n';

const avatar = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aDAAAAABJRU5ErkJggg==';
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
let profile = { name: 'User', bio: '', avatar: avatar as string | null, revision: 1 };
let icon: HTMLLinkElement;

beforeEach(() => {
  window.location.hash = '#/settings'; setLanguage({ language: 'zh-CN', revision: 0 });
  profile = { name: 'User', bio: '', avatar, revision: 1 };
  icon = document.createElement('link'); icon.rel = 'icon'; icon.href = '/favicon.svg'; document.head.append(icon);
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/profile') {
      if (init?.method === 'PUT') profile = { ...JSON.parse(init.body as string), revision: profile.revision + 1 };
      return response(profile);
    }
    if (url.startsWith('/api/works')) return response({ items: [], total: 0, stats: { total: 0, pending: 0, published: 0, issues: 0 }, last_scan: null });
    if (url === '/api/settings') return response({ roots: [], scan_roots_revision: 0 });
    if (url === '/api/mcp-auth') return response({ enabled: false, can_manage: false, revision: 0 });
    if (url === '/api/capabilities') return response({ can_open_folder: false, reason: '' });
    return response({ items: [] });
  }));
});
afterEach(() => { cleanup(); icon.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('uses the persisted avatar on page load, resets after saving removal and restores it after a new upload', async () => {
  const view = render(<App />);
  await waitFor(() => expect(icon.getAttribute('href')).toBe(avatar));
  expect(icon.type).toBe('image/png');
  fireEvent.click(screen.getByText('工作台资料', { selector: 'h2' }));
  fireEvent.click(screen.getByRole('button', { name: '移除头像' }));
  expect(icon.getAttribute('href')).toBe(avatar); // Unsaved draft does not change the tab icon.
  fireEvent.click(screen.getByRole('button', { name: '保存工作台资料' }));
  await waitFor(() => expect(icon.getAttribute('href')).toBe('/favicon.svg'));
  expect(icon.type).toBe('image/svg+xml');
  fireEvent.change(screen.getByLabelText('上传工作台头像'), { target: { files: [new File(['image'], 'avatar.webp', { type: 'image/webp' })] } });
  await screen.findByRole('img', { name: 'User头像' });
  expect(icon.getAttribute('href')).toBe('/favicon.svg');
  fireEvent.click(screen.getByRole('button', { name: '保存工作台资料' }));
  await waitFor(() => expect(icon.type).toBe('image/webp'));
  const saved = icon.getAttribute('href');
  view.unmount(); render(<App />);
  await waitFor(() => expect(icon.getAttribute('href')).toBe(saved));
});

it('keeps the saved icon when a profile save fails', async () => {
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    if (String(url) === '/api/profile') return init?.method === 'PUT'
      ? response({ detail: 'Save failed' }, 500) : response(profile);
    if (String(url).startsWith('/api/works')) return response({ items: [], total: 0, stats: { total: 0, pending: 0, published: 0, issues: 0 }, last_scan: null });
    if (String(url) === '/api/settings') return response({ roots: [], scan_roots_revision: 0 });
    return response({ items: [] });
  });
  render(<App />); await waitFor(() => expect(icon.getAttribute('href')).toBe(avatar));
  fireEvent.click(screen.getByText('工作台资料', { selector: 'h2' }));
  fireEvent.click(screen.getByRole('button', { name: '移除头像' }));
  fireEvent.click(screen.getByRole('button', { name: '保存工作台资料' }));
  await screen.findByText('Save failed');
  expect(icon.getAttribute('href')).toBe(avatar);
});

it('uses the default icon for an empty or unsupported legacy avatar', async () => {
  profile.avatar = 'https://example.com/legacy-avatar.png';
  render(<App />); await screen.findByText('User', { selector: '.brand strong' });
  expect(icon.getAttribute('href')).toBe('/favicon.svg');
  expect(icon.type).toBe('image/svg+xml');
});

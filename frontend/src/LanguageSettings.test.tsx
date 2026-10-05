// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';
import { LanguageBootstrap, LanguageSettings } from './LanguageSettings';
import { setLanguage, translate } from './i18n';

const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
beforeEach(() => { setLanguage({ language: 'zh-CN', revision: 0 }); window.location.hash = '#/inventory'; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); act(() => setLanguage({ language: 'zh-CN', revision: 0 })); });

it('saves a switch to English and restores the database language on reload', async () => {
  let saved = { language: 'zh-CN', revision: 0 };
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'PUT') {
      const payload = JSON.parse(init.body as string);
      expect(payload).toEqual({ language: 'en', expected_revision: 0 });
      saved = { language: payload.language, revision: 1 };
    }
    return response(saved);
  });
  vi.stubGlobal('fetch', fetchMock);
  const initial = render(<LanguageSettings />);
  fireEvent.change(screen.getByLabelText('显示语言'), { target: { value: 'en' } });
  await screen.findByRole('heading', { name: 'Interface language' });
  expect(screen.getByRole('status').textContent).toBe('Interface language saved');
  initial.unmount(); act(() => setLanguage({ language: 'zh-CN', revision: 0 }));
  render(<><LanguageBootstrap /><LanguageSettings /></>);
  await screen.findByRole('heading', { name: 'Interface language' });
  expect((screen.getByLabelText('Display language') as HTMLSelectElement).value).toBe('en');
  expect(document.documentElement.lang).toBe('en');
});

it('keeps the current language when saving fails', async () => {
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => response(init?.method === 'PUT' ? { detail: '服务暂不可用' } : { language: 'zh-CN', revision: 0 }, init?.method === 'PUT' ? 503 : 200)));
  render(<LanguageSettings />);
  fireEvent.change(screen.getByLabelText('显示语言'), { target: { value: 'en' } });
  await screen.findByRole('alert');
  expect((screen.getByLabelText('显示语言') as HTMLSelectElement).value).toBe('zh-CN');
});

it('translates the complete inventory shell while keeping user names and titles', async () => {
  setLanguage({ language: 'en', revision: 1 });
  const work = { id: 1, script_id: 'S901', title: '用户作品标题', status: 'pending', cover_url: null, video_count: 1, script_count: 2, directories: [], issues: [], updated_at: '2026-10-01', tags: [{ id: 2, category: 'author', name: '用户作者', support_status: 'none', support_url: null, revision: 0, usage_count: 1 }, { id: 3, category: 'axis_type', name: '多轴', support_status: 'none', support_url: null, revision: 0, usage_count: 1 }] };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => response(url.startsWith('/api/works') ? { items: [work], total: 1, stats: { total: 1, pending: 1, published: 0, issues: 0 }, last_scan: null }
    : url === '/api/profile' ? { name: '用户姓名', bio: '用户简介', avatar: null, revision: 1 }
    : url === '/api/capabilities' ? { can_open_folder: false, reason: '' } : { items: work.tags })));
  render(<App />);
  await screen.findByRole('button', { name: /View S901 用户作品标题/ });
  expect(screen.getByRole('heading', { name: 'Script inventory' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Scan now' })).toBeTruthy();
  expect(screen.getByText('用户作者')).toBeTruthy();
  expect(screen.getByText('Multi-axis', { selector: '.tag-chip' })).toBeTruthy();
  expect(screen.getByText('用户姓名')).toBeTruthy();
  act(() => setLanguage({ language: 'zh-CN', revision: 2 }));
  await waitFor(() => expect(screen.getByRole('heading', { name: '脚本库存' })).toBeTruthy());
  expect(screen.getByText('用户作品标题')).toBeTruthy();
});

it('handles dynamic backend messages without translating user text', () => {
  setLanguage({ language: 'en', revision: 1 });
  expect(translate('请求失败（503）')).toBe('Request failed (503)');
  expect(translate('{0} 的视频封面', { 0: '用户作品标题' })).toBe('Video cover for 用户作品标题');
  expect(translate('原始视频.funscript')).toBe('原始视频.funscript');
  expect(translate('第 {page} / {pages} 页 · 每页 {size} 个', { page: 1, pages: 2, size: 24 })).toBe('Page 1 / 2 · 24 per page');
  expect(translate('个视频', { count: 1 })).toBe('video');
  expect(translate('个视频', { count: 2 })).toBe('videos');
});

it('does not let a delayed language check overwrite a newer saved choice', async () => {
  let finish!: (result: Response) => void;
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { finish = resolve; })));
  render(<><LanguageBootstrap /><LanguageSettings /></>);
  act(() => setLanguage({ language: 'en', revision: 2 }));
  await act(async () => finish(response({ language: 'zh-CN', revision: 1 })));
  expect((screen.getByLabelText('Display language') as HTMLSelectElement).value).toBe('en');
});

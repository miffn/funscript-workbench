// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';
import { WorkLinkButtons, WorkLinkEditor } from './WorkLinks';
import type { Work, WorkLinks } from './api';

const current: WorkLinks = { work_id: 7, links: { patreon: 'https://www.patreon.com/posts/123', video: '', script: '', es: '' }, links_revision: 2 };
const work: Work = { id: 7, script_id: 'S058', title: '测试作品', status: 'pending', video_count: 1, script_count: 3, cover_url: null, issues: [], directories: [], updated_at: '', links: current.links, links_revision: 2 };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  localStorage.clear(); window.location.hash = '#/inventory';
  fetchMock = vi.fn().mockResolvedValue(response(current)); vi.stubGlobal('fetch', fetchMock);
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function(this: HTMLDialogElement) { this.open = true; } });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
function editor(onSaved = vi.fn(), onClose = vi.fn()) {
  render(<WorkLinkEditor work={work} initialKind="video" onSaved={onSaved} onClose={onClose} />); return { onSaved, onClose };
}
describe('inventory link editing', () => {
  it('shows four labeled actions and distinguishes filled from missing links', () => {
    const edit = vi.fn(); render(<WorkLinkButtons work={work} onEdit={edit} />);
    expect(screen.getAllByRole('button')).toHaveLength(4);
    expect(screen.getByRole('button', { name: '编辑 Patreon 文章链接 S058' }).classList.contains('filled')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '填写 ES 帖子链接 S058' }));
    expect(edit).toHaveBeenCalledWith('es');
  });
  it('focuses the chosen field and saves only changed fields with the revision', async () => {
    const callbacks = editor(); const input = await screen.findByLabelText('视频链接');
    await waitFor(() => expect(document.activeElement).toBe(input));
    fireEvent.change(input, { target: { value: 'https://example.com/video?part=2' } });
    const updated = { ...current, links: { ...current.links, video: 'https://example.com/video?part=2' }, links_revision: 3 };
    fetchMock.mockResolvedValueOnce(response(updated)); fireEvent.click(screen.getByRole('button', { name: '保存链接' }));
    await waitFor(() => expect(callbacks.onSaved).toHaveBeenCalledWith(updated));
    expect(JSON.parse(fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')![1].body)).toEqual({ links: { video: 'https://example.com/video?part=2' }, expected_revision: 2 });
    expect(callbacks.onClose).toHaveBeenCalledOnce();
  });
  it('can clear an imported URL without changing other fields', async () => {
    const callbacks = editor(); const input = await screen.findByLabelText('Patreon 文章链接');
    fireEvent.change(input, { target: { value: '' } });
    fetchMock.mockResolvedValueOnce(response({ ...current, links: { ...current.links, patreon: '' }, links_revision: 3 }));
    fireEvent.click(screen.getByRole('button', { name: '保存链接' }));
    await waitFor(() => expect(callbacks.onSaved).toHaveBeenCalledOnce());
    expect(JSON.parse(fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')![1].body).links).toEqual({ patreon: '' });
  });
  it.each(['javascript:alert(1)', 'https://user:secret@example.com', 'https://example.com/a b'])('rejects unsafe URL %s before saving', async value => {
    editor(); const input = await screen.findByLabelText('视频链接');
    fireEvent.change(input, { target: { value } });
    expect((input as HTMLInputElement).value).toBe(value);
    fireEvent.click(screen.getByRole('button', { name: '保存链接' }));
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false);
  });
  it('keeps failed edits and guards unsaved closing', async () => {
    const callbacks = editor(); const input = await screen.findByLabelText('视频链接');
    fireEvent.change(input, { target: { value: 'https://example.com/video' } });
    fetchMock.mockResolvedValueOnce(response({ detail: '数据库忙' }, 503));
    fireEvent.click(screen.getByRole('button', { name: '保存链接' })); await screen.findByText('链接未保存：数据库忙');
    expect((input as HTMLInputElement).value).toBe('https://example.com/video');
    expect(callbacks.onSaved).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '关闭链接编辑' }));
    expect(callbacks.onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '继续编辑链接' }));
    expect(screen.queryByText('链接修改尚未保存')).toBeNull();
  });
  it('prevents overwriting newer links until explicit refresh', async () => {
    editor(); const input = await screen.findByLabelText('视频链接');
    fireEvent.change(input, { target: { value: 'https://example.com/draft' } });
    fetchMock.mockResolvedValueOnce(response({ detail: 'revision mismatch' }, 409));
    fireEvent.click(screen.getByRole('button', { name: '保存链接' }));
    await screen.findByText(/链接已被其他页面更新/);
    expect((input as HTMLInputElement).value).toBe('https://example.com/draft');
    expect((screen.getByRole('button', { name: '保存链接' }) as HTMLButtonElement).disabled).toBe(true);
    fetchMock.mockResolvedValueOnce(response({ ...current, links: { ...current.links, video: 'https://example.com/new' }, links_revision: 3 }));
    fireEvent.click(screen.getByRole('button', { name: '放弃输入并读取最新链接' }));
    await waitFor(() => expect((screen.getByLabelText('视频链接') as HTMLInputElement).value).toBe('https://example.com/new'));
  });
  it('opens the link editor from a list row without opening work detail or nesting buttons', async () => {
    localStorage.setItem('workbench-view', 'list');
    fetchMock.mockImplementation((url: string) => Promise.resolve(response(url.startsWith('/api/works?') ? { items: [work], total: 1, page: 1, page_size: 24, stats: { total: 1, pending: 1, published: 0, issues: 0 }, last_scan: null } : url === '/api/tags' ? { items: [], categories: [] } : url === '/api/capabilities' ? { can_open_folder: false, reason: '' } : url === '/api/jobs' ? { items: [] } : current)));
    const { container } = render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: '填写 视频链接 S058' }));
    await screen.findByLabelText('视频链接');
    expect(screen.getByRole('dialog', { name: 'S058 · 发布链接' })).toBeTruthy();
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/works/7')).toBe(false);
    expect(container.querySelector('button button')).toBeNull();
    expect(screen.getByRole('button', { name: '查看 S058 测试作品' })).toBeTruthy();
  });
});

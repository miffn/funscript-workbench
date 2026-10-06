// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { WorkLinkEditor } from './WorkLinks';
import type { WorkLinks } from './api';

const current: WorkLinks = { work_id: 7, links_revision: 2, publication_revision: 'a'.repeat(64),
  links: { es: '', patreon: '', video: '', script: '' }, es_published: false, patreon_published: false,
  es_published_date: null, patreon_published_date: null, es_planned_date: null, patreon_planned_date: null };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
 fetchMock = vi.fn().mockResolvedValue(response(current)); vi.stubGlobal('fetch', fetchMock);
 Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function(this: HTMLDialogElement) { this.open = true; } });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function editor() {
 const saved = vi.fn(), close = vi.fn();
 render(<WorkLinkEditor work={{ id: 7, script_id: 'S007', title: '作品' }} initialKind="es" onSaved={saved} onClose={close} />);
 return { saved, close };
}
it('saves platform state, independent planned/actual dates and links atomically with both revisions', async () => {
 const callbacks = editor(); await screen.findByLabelText('ES 帖子链接');
 fireEvent.click(screen.getByRole('button', { name: '切换 Patreon 发布状态' }));
 fireEvent.change(screen.getByLabelText('ES 计划日期'), { target: { value: '2026-10-09' } });
 fireEvent.change(screen.getByLabelText('Patreon 发布日期'), { target: { value: '2026-10-06' } });
 fireEvent.change(screen.getByLabelText('ES 帖子链接'), { target: { value: 'https://example.test/es' } });
 const updated = { ...current, links_revision: 3, patreon_published: true, es_planned_date: '2026-10-09', patreon_published_date: '2026-10-06', links: { ...current.links, es: 'https://example.test/es' } };
 fetchMock.mockResolvedValueOnce(response(updated));
 fireEvent.click(screen.getByRole('button', { name: '保存链接' }));
 await waitFor(() => expect(callbacks.saved).toHaveBeenCalledWith(updated));
 expect(JSON.parse(fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')![1].body)).toEqual({ links: { es: 'https://example.test/es' }, expected_revision: 2,
  expected_publication_revision: 'a'.repeat(64), patreon_published: true, es_planned_date: '2026-10-09', patreon_published_date: '2026-10-06' });
});
it('guards closing a date-only draft and retains it after a concurrent publication conflict', async () => {
 const callbacks = editor(); await screen.findByLabelText('ES 帖子链接');
 fireEvent.change(screen.getByLabelText('ES 计划日期'), { target: { value: '2026-10-09' } });
 fireEvent.click(screen.getByRole('button', { name: '关闭链接编辑' }));
 expect(callbacks.close).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button', { name: '继续编辑链接' }));
 fetchMock.mockResolvedValueOnce(response({ detail: '已修改' }, 409));
 fireEvent.click(screen.getByRole('button', { name: '保存链接' }));
 await screen.findByRole('alert');
 expect((screen.getByLabelText('ES 计划日期') as HTMLInputElement).value).toBe('2026-10-09');
 expect((screen.getByRole('button', { name: '保存链接' }) as HTMLButtonElement).disabled).toBe(true);
 expect(callbacks.saved).not.toHaveBeenCalled();
});

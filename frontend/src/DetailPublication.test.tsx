// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { WorkDetail } from './components';
import type { Work, WorkLinkKind, WorkLinks } from './api';

vi.mock('./PreviewSection', () => ({ PreviewSection: () => null }));
vi.mock('./WorkLinks', () => ({
  WorkLinkButtons: ({ onEdit }: { onEdit: (kind: WorkLinkKind) => void }) => <button onClick={() => onEdit('es')}>编辑发布链接</button>,
  WorkLinkEditor: ({ onSaved, onClose }: { onSaved: (value: WorkLinks) => void; onClose: () => void }) => <button onClick={() => {
    onSaved({ work_id: 7, links: { es: 'https://example.com/new', patreon: '', video: '', script: '' }, links_revision: 3,
      es_published: true, patreon_published: true, es_published_date: '2026-10-06', patreon_published_date: '2026-10-06', es_planned_date: '2026-10-08' });
    onClose();
  }}>保存发布快编</button>,
}));

const fixture: Work = { id: 7, script_id: 'S070', title: '已有标题', notes: '已有备注', status: 'pending', es_published: false, patreon_published: false, es_published_date: '2026-10-01', patreon_published_date: null, video_count: 1, script_count: 1, issues: [], cover_url: null, updated_at: '', directories: [] };
let server: Work;
beforeEach(() => {
  server = fixture;
  vi.stubGlobal('fetch', vi.fn((url: string) => Promise.resolve(new Response(JSON.stringify(url === '/api/jobs' ? { items: [] } : server), { headers: { 'Content-Type': 'application/json' } }))));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('refreshes publication information after quick editing while retaining independent local drafts', async () => {
  render(<WorkDetail id={7} presentation="page" capabilities={{ can_open_folder: false, reason: '' }} onClose={() => {}} onSaved={() => {}} notify={() => {}} />);
  const notes = await screen.findByLabelText('备注');
  fireEvent.change(notes, { target: { value: '本地备注草稿' } });
  fireEvent.change(screen.getByLabelText('ES 发布日期'), { target: { value: '2026-09-29' } });
  fireEvent.click(screen.getByRole('button', { name: '编辑发布链接' }));
  server = { ...fixture, es_published: true, patreon_published: true, es_published_date: '2026-10-06', patreon_published_date: '2026-10-06', es_planned_date: '2026-10-08', links_revision: 3, links: { es: 'https://example.com/new', patreon: '', video: '', script: '' } };
  fireEvent.click(screen.getByRole('button', { name: '保存发布快编' }));
  await waitFor(() => expect((screen.getByLabelText('Patreon 发布日期') as HTMLInputElement).value).toBe('2026-10-06'));
  expect(screen.getByLabelText('ES · 已发布 · 2026-10-06')).toBeTruthy();
  expect((notes as HTMLTextAreaElement).value).toBe('本地备注草稿');
  expect((screen.getByLabelText('ES 发布日期') as HTMLInputElement).value).toBe('2026-09-29');
  expect(screen.getByText('2026-10-08')).toBeTruthy();
  expect((screen.getByRole('button', { name: '保存信息' }) as HTMLButtonElement).disabled).toBe(false);
});
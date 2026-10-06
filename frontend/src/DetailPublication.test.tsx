// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { Mock } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { WorkDetail } from './components';
import type { Work } from './api';

vi.mock('./PreviewSection', () => ({ PreviewSection: ({ onDirtyChange }: { onDirtyChange: (dirty: boolean) => void }) => <button onClick={() => onDirtyChange(true)}>修改源匹配草稿</button> }));
const fixture: Work = { id: 7, script_id: 'S070', title: '已有标题', notes: '已有备注', status: 'pending', es_published: false, patreon_published: false, es_published_date: '2026-10-01', patreon_published_date: null, links: { es: '', patreon: '', video: '', script: '' }, links_revision: 1, video_count: 1, script_count: 1, issues: [], cover_url: null, updated_at: '', directories: [] };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
let server: Work;
let fetchMock: Mock<(url: string, init?: RequestInit) => Promise<Response>>;
const saved = vi.fn();
const close = vi.fn();
const linksResponse = () => ({ ...server, work_id: server.id, publication_revision: 'publication-v1' });
beforeEach(() => {
  server = structuredClone(fixture); saved.mockClear(); close.mockClear();
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/jobs') return response({ items: [] });
    if (url.endsWith('/preview')) return response({ job: null, files: [], output_dir: '', windows_path: '' });
    if (url.endsWith('/links')) {
      if (init?.method === 'PATCH') {
        const { links, expected_revision: _revision, expected_publication_revision: _publication, ...fields } = JSON.parse(String(init.body));
        server = { ...server, ...fields, links: { ...server.links!, ...links }, links_revision: server.links_revision! + 1 };
      }
      return response(linksResponse());
    }
    if (init?.method === 'PATCH') server = { ...server, ...JSON.parse(String(init.body)) };
    return response(server);
  });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const page = () => render(<WorkDetail id={7} presentation="page" capabilities={{ can_open_folder: false, reason: '' }} onClose={close} onSaved={saved} notify={() => {}} />);
const tab = (name: string) => fireEvent.click(screen.getByRole('tab', { name }));

it('uses the approved hero and three real keyboard tabs with only the selected panel visible', async () => {
  const view = page();
  await screen.findByRole('heading', { name: '已有标题', level: 1 });
  expect(view.container.querySelector('.detail-header')).toBeNull();
  const tabs = screen.getAllByRole('tab');
  expect(tabs.map(element => element.textContent)).toEqual(['资料与标签', '素材', '发布信息']);
  expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
  expect(screen.getByRole('tabpanel').getAttribute('aria-labelledby')).toBe('work-detail-tab-tags');
  fireEvent.keyDown(tabs[0], { key: 'ArrowRight' });
  expect(document.activeElement).toBe(tabs[1]);
  expect(screen.getByRole('tabpanel').getAttribute('aria-labelledby')).toBe('work-detail-tab-assets');
  expect(screen.getByRole('heading', { name: '视频素材' })).toBeTruthy();
  expect(screen.getByRole('heading', { name: '脚本素材' })).toBeTruthy();
  expect(screen.queryByRole('button', { name: '保存信息' })).toBeNull();
  fireEvent.keyDown(tabs[1], { key: 'End' });
  expect(document.activeElement).toBe(tabs[2]);
  await screen.findByRole('button', { name: '切换 ES 发布状态' });
  expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
  fireEvent.keyDown(tabs[2], { key: 'Home' });
  expect(screen.getByRole('button', { name: '保存信息' })).toBeTruthy();
});

it('preserves text, source-matching and publication drafts across all tabs and protects leaving', async () => {
  page(); const notes = await screen.findByLabelText('备注');
  fireEvent.change(notes, { target: { value: '本地备注草稿' } });
  tab('素材'); fireEvent.click(screen.getByRole('button', { name: '修改源匹配草稿' }));
  tab('发布信息'); fireEvent.click(await screen.findByRole('button', { name: '编辑 ES 发布日期' }));
  fireEvent.change(screen.getByLabelText('ES 发布日期'), { target: { value: '2026-09-29' } });
  tab('资料与标签');
  expect((notes as HTMLTextAreaElement).value).toBe('本地备注草稿');
  tab('发布信息'); expect((screen.getByLabelText('ES 发布日期') as HTMLInputElement).value).toBe('2026-09-29');
  fireEvent.click(screen.getByRole('button', { name: '返回库存' }));
  expect(close).not.toHaveBeenCalled();
  expect(screen.getByText('有尚未保存的修改')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
  expect((screen.getByLabelText('ES 发布日期') as HTMLInputElement).value).toBe('2026-09-29');
});

it('saves shared publication fields atomically without writing or replacing local title and notes', async () => {
  page(); await screen.findByLabelText('备注');
  fireEvent.change(screen.getByLabelText('标题'), { target: { value: '本地标题' } });
  fireEvent.change(screen.getByLabelText('备注'), { target: { value: '本地备注' } });
  tab('发布信息');
  fireEvent.click(await screen.findByRole('button', { name: '切换 ES 发布状态' }));
  fireEvent.click(screen.getByRole('button', { name: '编辑 ES 发布日期' }));
  fireEvent.change(screen.getByLabelText('ES 发布日期'), { target: { value: '2026-09-29' } });
  fireEvent.click(screen.getByRole('button', { name: '保存链接' }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  await waitFor(() => expect((screen.getByRole('button', { name: '返回库存' }) as HTMLButtonElement).disabled).toBe(false));
  const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH');
  expect(writes).toHaveLength(1);
  expect(writes[0][0]).toBe('/api/works/7/links');
  expect(JSON.parse(String(writes[0][1]?.body))).toEqual({ links: {}, expected_revision: 1, expected_publication_revision: 'publication-v1', es_published: true, es_published_date: '2026-09-29' });
  expect(screen.getByLabelText('ES · 已发布 · 2026-09-29')).toBeTruthy();
  tab('资料与标签');
  expect((screen.getByLabelText('标题') as HTMLInputElement).value).toBe('本地标题');
  expect((screen.getByLabelText('备注') as HTMLTextAreaElement).value).toBe('本地备注');
  expect((screen.getByRole('button', { name: '保存信息' }) as HTMLButtonElement).disabled).toBe(false);
});

it('blocks leaving and cross-tab writes while shared publication persistence is pending', async () => {
  page(); await screen.findByLabelText('标题'); tab('发布信息');
  fireEvent.click(await screen.findByRole('button', { name: '切换 ES 发布状态' }));
  let finish: ((value: Response) => void) | undefined;
  const original = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation((url: string, init?: RequestInit) => url.endsWith('/links') && init?.method === 'PATCH' ? new Promise<Response>(resolve => { finish = resolve; }) : original(url, init));
  fireEvent.click(screen.getByRole('button', { name: '保存链接' }));
  expect((screen.getByRole('button', { name: '返回库存' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('tab', { name: '资料与标签' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '正在保存' }));
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1);
  await act(async () => finish!(response({ ...linksResponse(), es_published: true, links_revision: 2 })));
  await waitFor(() => expect((screen.getByRole('button', { name: '返回库存' }) as HTMLButtonElement).disabled).toBe(false));
});
it('preserves current platform states and local release edits after a rejected publication write', async () => {
  server = { ...server, es_published: true };
  page(); await screen.findByLabelText('备注');
  fireEvent.change(screen.getByLabelText('备注'), { target: { value: '尚未保存备注' } });
  tab('发布信息');
  const patreon = await screen.findByRole('button', { name: '切换 Patreon 发布状态' });
  expect(patreon.getAttribute('aria-pressed')).toBe('false');
  fireEvent.click(patreon);
  fetchMock.mockResolvedValueOnce(response({ detail: '写入失败' }, 503));
  fireEvent.click(screen.getByRole('button', { name: '保存链接' }));
  await screen.findByText('链接未保存：写入失败');
  expect(screen.getByLabelText('ES · 已发布 · 2026-10-01')).toBeTruthy();
  expect(screen.getByLabelText('Patreon · 待发布')).toBeTruthy();
  expect(patreon.getAttribute('aria-pressed')).toBe('true');
  expect(saved).not.toHaveBeenCalled();
  expect((screen.getByLabelText('备注') as HTMLTextAreaElement).value).toBe('尚未保存备注');
});

it('publishes Patreon independently and retracts ES in a later write without replacing text or mapping drafts', async () => {
  server = { ...server, es_published: true };
  page(); await screen.findByLabelText('备注');
  fireEvent.change(screen.getByLabelText('标题'), { target: { value: '未保存标题' } });
  fireEvent.change(screen.getByLabelText('备注'), { target: { value: '未保存备注' } });
  tab('素材'); fireEvent.click(screen.getByRole('button', { name: '修改源匹配草稿' }));
  tab('发布信息');
  fireEvent.click(await screen.findByRole('button', { name: '切换 Patreon 发布状态' }));
  fireEvent.click(screen.getByRole('button', { name: '保存链接' }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  await waitFor(() => expect((screen.getByRole('button', { name: '返回库存' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '切换 ES 发布状态' }));
  fireEvent.click(screen.getByRole('button', { name: '保存链接' }));
  await waitFor(() => expect(saved).toHaveBeenCalledTimes(2));
  await waitFor(() => expect((screen.getByRole('button', { name: '返回库存' }) as HTMLButtonElement).disabled).toBe(false));
  const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH').map(([, init]) => JSON.parse(String(init?.body)));
  expect(writes).toEqual([
    { links: {}, expected_revision: 1, expected_publication_revision: 'publication-v1', patreon_published: true },
    { links: {}, expected_revision: 2, expected_publication_revision: 'publication-v1', es_published: false },
  ]);
  expect(server.es_published).toBe(false); expect(server.patreon_published).toBe(true);
  tab('资料与标签');
  expect((screen.getByLabelText('标题') as HTMLInputElement).value).toBe('未保存标题');
  expect((screen.getByLabelText('备注') as HTMLTextAreaElement).value).toBe('未保存备注');
  fireEvent.click(screen.getByRole('button', { name: '返回库存' }));
  expect(screen.getByText('有尚未保存的修改')).toBeTruthy();
});

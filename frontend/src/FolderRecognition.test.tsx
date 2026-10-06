// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { Mock } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ScanCandidates } from './ScanCandidates';
import { IssuesPage, JobsPage, WorkDetail } from './components';
import { workIdentity } from './api';
import type { ScanCandidates as CandidateData, Work } from './api';
import { setLanguage } from './i18n';

const candidate = { id: 8, name: '重新命名的目录', path: '/library/renamed', windows_path: 'D:\\library\\renamed', root_path: '/library', available: true, revision: 3, missing_work_ids: [7], video_count: 2, script_count: 1 };
const fixture: Work = { id: 7, script_id: null, title: '原有作品', notes: '原有备注', status: 'published', video_count: 2, script_count: 1, cover_url: null, issues: [], updated_at: '', production_required: false, production_revision: 4, es_published: true, patreon_published: true, es_published_date: '2026-10-01', patreon_published_date: '2026-10-02', links: { es: 'https://example.com/es', patreon: 'https://example.com/patreon', video: '', script: '' }, directories: [{ id: 12, path: '/library/source', windows_path: 'D:\\library\\source', available: true }] };
let source: CandidateData;
let current: Work;
let fetchMock: Mock<(url: string, init?: RequestInit) => Promise<Response>>;
let failure: number;
const changed = vi.fn();
const selected = vi.fn();
const counted = vi.fn();
const saved = vi.fn();
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
beforeEach(() => {
  setLanguage({ language: 'zh-CN', revision: 0 }); changed.mockClear(); selected.mockClear(); counted.mockClear(); saved.mockClear(); failure = 0;
  current = structuredClone(fixture);
  source = { items: [{ ...candidate }], works: [{ id: 7, script_id: null, title: '原有作品', association_revision: 9 }], total: 1 };
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function(this: HTMLDialogElement) { this.open = true; } });
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/scan-candidates') return response(source);
    if (url.endsWith('/resolve')) {
      if (failure) return response({ detail: '候选已变化' }, failure);
      const body = JSON.parse(String(init?.body));
      if (body.action === 'create') current = { ...fixture, id: 99, script_id: null, title: candidate.name };
      source = { ...source, items: [], total: 0 };
      return response({ candidate_id: 8, action: body.action, work: current });
    }
    if (url === '/api/jobs') return response({ items: [] });
    if (url.endsWith('/preview')) return response({ job: null, files: [], output_dir: '', windows_path: '', stale: false });
    if (url.endsWith('/preview-matching')) return response({ work_id: 7, video_asset_id: null, mode: 'auto', revision: 0, script_asset_ids: {}, issues: [], videos: [], scripts: [], job: null, source_changed: false });
    if (url.endsWith('/production/reset')) {
      if (failure) return response({ detail: '制作确认状态已变化，请刷新作品后重试' }, failure);
      current = { ...current, production_required: true, production_revision: 5 };
    }
    if (init?.method === 'PATCH') {
      const payload = JSON.parse(String(init.body)); current = { ...current, ...payload };
      if (payload.es_published && !current.es_published_date) current.es_published_date = '2026-10-06';
    }
    return response(current);
  });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); act(() => setLanguage({ language: 'zh-CN', revision: 0 })); });
const posts = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
function candidates() { return render(<ScanCandidates revision={0} onChanged={changed} onSelect={selected} onCount={counted} />); }
function detail() { return render(<WorkDetail id={7} presentation="page" capabilities={{ can_open_folder: false, reason: '' }} onClose={() => {}} onSaved={saved} notify={() => {}} />); }

it('uses IDs independently from nullable numbers or editable titles', () => {
  expect(workIdentity({ id: 7, script_id: null, title: '目录名字' })).toBe('目录名字');
  expect(workIdentity({ id: 7, script_id: null, title: '  ' })).toBe('work-7');
  expect(workIdentity({ work_id: 8, script_id: 'S025_001', title: '目录' })).toBe('S025_001');
});

it('shows an unnumbered work name on queued preview tasks without exposing a fake Script ID', async () => {
  fetchMock.mockImplementation(async () => response({ items: [{ id: 44, type: 'preview', status: 'queued', created_at: '2026-10-06T00:00:00Z', inputs: { work_id: 7, script_id: null, title: '普通文件夹' }, result: null }] }));
  render(<JobsPage revision={0} />);
  expect(await screen.findByRole('button', { name: /预览生成.*普通文件夹/ })).toBeTruthy();
  expect(screen.queryByText(/^null$/)).toBeNull();
});

it('uses the associated work ID for each issue, independently of the issue record ID', async () => {
  fetchMock.mockImplementation(async (url: string) => response(url === '/api/scan-candidates' ? { items: [], works: [], total: 0 } : { items: [
    { id: 2, work_id: 3, script_id: null, type: 'directory_missing', message: '目录已移动' },
    { id: 9, work_id: 3, script_id: null, type: 'duration_error', message: '时长读取失败' },
    { id: 10, work_id: null, script_id: null, type: 'root_unavailable', message: '根目录不可用' },
  ], total: 3 }));
  render(<IssuesPage revision={0} onSelect={selected} />);
  await screen.findByRole('heading', { name: '目录已移动' });
  expect(screen.getAllByText('work-3')).toHaveLength(2);
  expect(screen.queryByText('work-2')).toBeNull();
  expect(screen.queryByText('work-9')).toBeNull();
  expect(screen.queryByText('work-10')).toBeNull();
  expect(screen.getAllByRole('button', { name: '查看作品' })).toHaveLength(2);
});

it('never resolves a scan candidate without explicit choice, and associates an existing work with both revisions', async () => {
  candidates(); await screen.findByRole('heading', { name: candidate.name });
  expect(posts()).toHaveLength(0); expect((screen.getByRole('button', { name: '确认创建' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByLabelText('关联已有作品'));
  expect((screen.getByRole('button', { name: '确认关联' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('选择可关联的作品'), { target: { value: '7' } });
  fireEvent.click(screen.getByRole('button', { name: '确认关联' }));
  await screen.findByText('已将文件夹关联到 原有作品，既有作品资料保留。');
  expect(JSON.parse(String(posts()[0][1]?.body))).toEqual({ action: 'associate', expected_revision: 3, work_id: 7, expected_work_revision: 9 });
  expect(current).toMatchObject({ id: 7, title: '原有作品', notes: '原有备注', es_published_date: '2026-10-01', patreon_published_date: '2026-10-02' });
  expect(changed).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: '查看作品' })); expect(selected).toHaveBeenCalledWith(7);
});

it('creates a separate unnumbered work only after explicit confirmation', async () => {
  candidates(); await screen.findByRole('heading', { name: candidate.name });
  fireEvent.click(screen.getByLabelText('创建新作品')); fireEvent.click(screen.getByRole('button', { name: '确认创建' }));
  await screen.findByText(`已创建作品 ${candidate.name}。`);
  expect(JSON.parse(String(posts()[0][1]?.body))).toEqual({ action: 'create', expected_revision: 3 });
  expect(current.id).toBe(99); expect(current.script_id).toBeNull();
});

it('locks candidate resolution while writing and prevents duplicate submissions', async () => {
  let finish: ((value: Response) => void) | undefined;
  const original = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation((url: string, init?: RequestInit) => url.endsWith('/resolve') ? new Promise<Response>(resolve => { finish = resolve; }) : original(url, init));
  candidates(); await screen.findByRole('heading', { name: candidate.name });
  fireEvent.click(screen.getByLabelText('创建新作品')); fireEvent.click(screen.getByRole('button', { name: '确认创建' }));
  const pending = screen.getByRole('button', { name: '正在确认…' }) as HTMLButtonElement;
  expect(pending.disabled).toBe(true); fireEvent.click(pending); expect(posts()).toHaveLength(1);
  await act(async () => finish!(response({ candidate_id: 8, action: 'create', work: current })));
  await screen.findByText('已创建作品 原有作品。');
});

it('reloads conflicting candidates and forces a fresh explicit choice without retrying the mutation', async () => {
  candidates(); await screen.findByRole('heading', { name: candidate.name });
  failure = 409; fireEvent.click(screen.getByLabelText('创建新作品')); fireEvent.click(screen.getByRole('button', { name: '确认创建' }));
  await screen.findByText(/候选或作品资料已变化，已重新读取。请检查后重新选择；正在运行的素材任务需等待完成。/);
  expect(posts()).toHaveLength(1);
  await waitFor(() => expect((screen.getByRole('button', { name: '确认创建' }) as HTMLButtonElement).disabled).toBe(true));
  expect((screen.getByLabelText('创建新作品') as HTMLInputElement).checked).toBe(false);
});

it('blocks unavailable candidates and displays English operations without changing folder or work names', async () => {
  source.items[0].available = false; setLanguage({ language: 'en', revision: 1 }); candidates();
  await screen.findByRole('heading', { name: candidate.name });
  expect(screen.getByRole('heading', { name: 'Scan candidates' })).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Confirm creation' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByLabelText('Associate existing work').closest('fieldset') as HTMLFieldSetElement).disabled).toBe(true);
  expect(posts()).toHaveLength(0);
});

it('keeps production and both platform operations available and resets only production while retaining local drafts', async () => {
  detail(); await screen.findByLabelText('备注');
  const manager = screen.getByRole('heading', { name: '状态管理' }).closest('section')!;
  expect(within(manager).getByRole('button', { name: '退回待制作' })).toBeTruthy();
  const publication = screen.getByRole('heading', { name: '发布信息' }).closest('section')!;
  expect(within(publication).getByRole('button', { name: '将 ES 改为未发布' })).toBeTruthy();
  expect(within(publication).getByRole('button', { name: '将 Patreon 改为未发布' })).toBeTruthy();
  await waitFor(() => expect((screen.getByRole('button', { name: '退回待制作' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText('标题'), { target: { value: '未保存标题' } });
  fireEvent.change(screen.getByLabelText('备注'), { target: { value: '未保存备注' } });
  fireEvent.change(screen.getByLabelText('ES 发布日期'), { target: { value: '2026-09-28' } });
  fireEvent.click(screen.getByRole('button', { name: '退回待制作' }));
  await screen.findByRole('button', { name: '确认制作完成' });
  expect(JSON.parse(String(posts()[0][1]?.body))).toEqual({ expected_revision: 4 });
  expect(current).toMatchObject({ production_required: true, es_published: true, patreon_published: true, es_published_date: '2026-10-01', patreon_published_date: '2026-10-02', links: fixture.links });
  expect((screen.getByLabelText('标题') as HTMLInputElement).value).toBe('未保存标题');
  expect((screen.getByLabelText('备注') as HTMLTextAreaElement).value).toBe('未保存备注');
  expect((screen.getByLabelText('ES 发布日期') as HTMLInputElement).value).toBe('2026-09-28');
});

it('keeps confirmed production intact after a rejected reset and does not confirm a scriptless work', async () => {
  failure = 409; const view = detail(); await screen.findByLabelText('备注');
  await waitFor(() => expect((screen.getByRole('button', { name: '退回待制作' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '退回待制作' }));
  await screen.findByText('制作状态未更新：制作确认状态已变化，请刷新作品后重试');
  expect(current.production_required).toBe(false);
  view.unmount(); current = { ...current, script_count: 0, production_required: true }; detail();
  await screen.findByText('尚无可用脚本，请添加脚本后扫描或重新匹配文件。');
  expect((screen.getByRole('button', { name: '确认制作完成' }) as HTMLButtonElement).disabled).toBe(true);
});

it('blocks repeated production resets and platform writes while the reset is pending', async () => {
  let finish: ((value: Response) => void) | undefined;
  const original = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation((url: string, init?: RequestInit) => url.endsWith('/production/reset') ? new Promise<Response>(resolve => { finish = resolve; }) : original(url, init));
  detail(); await screen.findByLabelText('备注');
  await waitFor(() => expect((screen.getByRole('button', { name: '退回待制作' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: '退回待制作' }));
  const busy = screen.getByRole('button', { name: '正在退回待制作' }) as HTMLButtonElement;
  expect(busy.disabled).toBe(true); fireEvent.click(busy);
  expect((screen.getByRole('button', { name: '将 ES 改为未发布' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: '返回库存' }) as HTMLButtonElement).disabled).toBe(true);
  expect(posts()).toHaveLength(1);
  await act(async () => finish!(response({ ...current, production_required: true, production_revision: 5 })));
  await screen.findByRole('button', { name: '确认制作完成' });
});

it('shows a folder title and missing-association guidance without inventing a Script ID', async () => {
  current = { ...current, association_status: 'missing' }; detail();
  expect(await screen.findByRole('heading', { name: '原有作品', level: 1 })).toBeTruthy();
  expect(screen.getByText('素材关联失败，请重新扫描。文件夹改名或迁移后，请在待处理的扫描候选里关联已有作品。')).toBeTruthy();
  expect(document.title).toBe('原有作品 · Funscript 工作台');
  expect(screen.queryByText(/^null$/)).toBeNull();
});

it('synchronizes a newly defaulted publication date and does not clear it when later saving notes', async () => {
  current = { ...current, status: 'pending', es_published: false, es_published_date: null }; detail(); await screen.findByLabelText('备注');
  fireEvent.change(screen.getByLabelText('备注'), { target: { value: '新备注' } });
  fireEvent.click(screen.getByRole('button', { name: '标记 ES 已发布' }));
  await screen.findByRole('button', { name: '将 ES 改为未发布' });
  expect((screen.getByLabelText('ES 发布日期') as HTMLInputElement).value).toBe('2026-10-06');
  fireEvent.click(screen.getByRole('button', { name: '保存信息' })); await waitFor(() => expect(saved).toHaveBeenCalledTimes(2));
  const payload = JSON.parse(String(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH').at(-1)![1]?.body));
  expect(payload).toEqual({ title: '原有作品', notes: '新备注' });
  expect(current.es_published_date).toBe('2026-10-06');
});

it('retains an explicitly edited date draft when publication writes default another date', async () => {
  current = { ...current, status: 'pending', es_published: false, es_published_date: null }; detail(); await screen.findByLabelText('ES 发布日期');
  fireEvent.change(screen.getByLabelText('ES 发布日期'), { target: { value: '2026-09-25' } });
  fireEvent.click(screen.getByRole('button', { name: '标记 ES 已发布' }));
  await screen.findByRole('button', { name: '将 ES 改为未发布' });
  expect((screen.getByLabelText('ES 发布日期') as HTMLInputElement).value).toBe('2026-09-25');
  expect(current.es_published_date).toBe('2026-10-06');
});

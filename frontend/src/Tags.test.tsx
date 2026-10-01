// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import App from './App';
import { TagsPage, WorkTagEditor } from './Tags';
import { WorkDetail } from './components';
import type { Tag, Work, WorkTags } from './api';

const makeTag = (id: number, category: Tag['category'], name: string): Tag => ({ id, category, name, revision: 1, usage_count: 0, support_status: 'unknown', support_url: null });
const tags = [makeTag(1, 'author', '作者 A'), makeTag(2, 'author', '作者 B'), makeTag(3, 'video_type', 'Real'), makeTag(4, 'video_type', 'Anime'), makeTag(5, 'release_type', 'Paid'), makeTag(6, 'release_type', 'Free Sample'), makeTag(7, 'tier', 'Main Tier'), makeTag(8, 'tier', 'Free'), makeTag(9, 'custom', '短片'), makeTag(10, 'custom', '收藏')];
const work: Work = { id: 7, script_id: 'S025_001', title: '作品标题', notes: '旧备注', status: 'pending', cover_url: '/api/covers/7', video_count: 1, script_count: 1, issues: [], directories: [], assets: [], tags: [], tags_revision: 2, updated_at: '2026-09-30T00:00:00Z' };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
let fetchMock: ReturnType<typeof vi.fn>;
let binding: WorkTags;
let catalog: Tag[];
let failStatus: number;
let importReport: Record<string, unknown> | null;

beforeEach(() => {
  binding = { work_id: 7, tags: [], tags_revision: 2 }; catalog = tags.map(tag => ({ ...tag })); failStatus = 0; importReport = null;
  localStorage.clear(); window.location.hash = '#/inventory';
  fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/tags' && init?.method === 'POST' || url.startsWith('/api/tags/') && init?.method === 'PATCH') {
      if (failStatus) return Promise.resolve(response({ detail: '资料有冲突' }, failStatus));
      return Promise.resolve(response({ ...catalog[0], ...JSON.parse(init!.body as string) }));
    }
    if (url === '/api/tags') return Promise.resolve(response({ items: catalog, categories: [], import_report: importReport }));
    if (url === '/api/works/7/tags') {
      if (init?.method === 'PUT') {
        if (failStatus) return Promise.resolve(response({ detail: '版本有冲突' }, failStatus));
        const body = JSON.parse(init.body as string);
        binding = { ...binding, tags: catalog.filter(tag => body.tag_ids.includes(tag.id)), tags_revision: binding.tags_revision + 1 };
      }
      return Promise.resolve(response(binding));
    }
    if (url.startsWith('/api/works?')) return Promise.resolve(response({ items: [{ ...work, tags: binding.tags }], total: 1, page: 1, page_size: 24, stats: { total: 1, pending: 1, published: 0, issues: 0 }, last_scan: null }));
    if (url === '/api/capabilities') return Promise.resolve(response({ can_open_folder: false, reason: '其他客户端不支持打开' }));
    if (url === '/api/jobs') return Promise.resolve(response({ items: [] }));
    if (url.includes('/preview-matching')) return Promise.resolve(response({ work_id: 7, video_asset_id: null, mode: 'auto', revision: 0, script_asset_ids: {}, issues: [], videos: [], scripts: [], job: null, source_changed: false }));
    if (url.endsWith('/preview')) return Promise.resolve(response({ job: null, files: [], output_dir: '', windows_path: '' }));
    return Promise.resolve(response(work));
  });
  vi.stubGlobal('fetch', fetchMock);
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function(this: HTMLDialogElement) { this.open = true; } });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('quick tagging without media', () => {
  it.each(['gallery', 'list', 'tags'])('shows categorized labels in %s mode and exposes every filter category', async (view) => {
    const axis = makeTag(11, 'axis_type', '多轴');
    catalog.push(axis);
    binding.tags = [catalog[0], catalog[2], axis, catalog[4], catalog[6], catalog[8]];
    localStorage.setItem('workbench-view', view);
    render(<App />);
    const row = (await screen.findByRole('button', { name: view === 'tags' ? '编辑标签 S025_001' : '查看 S025_001 作品标题' })).closest('article')!;
    for (const [category, name] of [['author', '作者 A'], ['video_type', 'Real'], ['axis_type', '多轴'], ['release_type', 'Paid'], ['tier', 'Main Tier'], ['custom', '短片']]) {
      expect(row.querySelector(`.tag-chip.${category}`)?.textContent).toContain(name);
    }
    const select = screen.getByLabelText('按标签筛选');
    for (const label of ['作者', '视频类型', '轴类型', '发布类型', '档位', '自定义分类']) expect(within(select).getByRole('group', { name: label })).toBeTruthy();
    for (const id of [3, 11, 5, 7, 9]) {
      fireEvent.change(select, { target: { value: String(id) } });
      await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => new URL(url, 'http://localhost').searchParams.get('tag_id') === String(id))).toBe(true));
    }
  });

  it('renders the saved tag-list preference and edits labels without any cover or preview request', async () => {
    localStorage.setItem('workbench-view', 'tags'); render(<App />);
    await screen.findByRole('button', { name: '编辑标签 S025_001' });
    expect(document.querySelector('img, video')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '编辑标签 S025_001' }));
    await screen.findByRole('button', { name: '作者 A' });
    fireEvent.click(screen.getByRole('button', { name: '作者 A' }));
    fireEvent.click(screen.getByRole('button', { name: '保存标签' }));
    await screen.findByText('作品标签已保存');
    expect(await screen.findByTitle('作者：作者 A')).toBeTruthy();
    expect(document.querySelector('img, video')).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => url.includes('/preview') || url.includes('/covers/'))).toBe(false);
    expect(binding.tags.map(tag => tag.id)).toEqual([1]);
    expect(work.status).toBe('pending');
  });

  it('sends tag filters and untagged-only as separate mutually exclusive inventory parameters', async () => {
    localStorage.setItem('workbench-view', 'tags'); render(<App />);
    await screen.findByRole('button', { name: '编辑标签 S025_001' });
    fireEvent.change(screen.getByLabelText('按标签筛选'), { target: { value: '2' } });
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.includes('tag_id=2'))).toBe(true));
    fireEvent.click(screen.getByLabelText('仅看未标注'));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.includes('untagged_only=true'))).toBe(true));
    const requests = fetchMock.mock.calls.filter(([url]) => url.startsWith('/api/works?'));
    expect(requests.at(-1)![0]).not.toContain('tag_id=');
  });

  it('keeps title and notes drafts when tags are updated from the regular detail', async () => {
    render(<WorkDetail id={7} capabilities={{ can_open_folder: false, reason: '' }} onClose={vi.fn()} onSaved={vi.fn()} notify={vi.fn()} />);
    const notes = await screen.findByLabelText('备注'); fireEvent.change(notes, { target: { value: '尚未保存的备注' } });
    const title = screen.getByLabelText('标题'); fireEvent.change(title, { target: { value: '尚未保存的标题' } });
    fireEvent.click(screen.getByRole('button', { name: '编辑标签' }));
    await screen.findByRole('button', { name: '作者 A' }); fireEvent.click(screen.getByRole('button', { name: '作者 A' }));
    fireEvent.click(screen.getByRole('button', { name: '保存标签' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: '保存标签' })).toBeNull());
    expect((notes as HTMLTextAreaElement).value).toBe('尚未保存的备注');
    expect((title as HTMLInputElement).value).toBe('尚未保存的标题');
    expect(screen.getByText('作者 A')).toBeTruthy();
  });
});

describe('binding revisions and category rules', () => {
  it('replaces single-choice labels, permits multiple custom labels and validates free/paid combinations', async () => {
    render(<WorkTagEditor work={work} onClose={vi.fn()} onSaved={vi.fn()} />);
    await screen.findByRole('button', { name: '作者 A' });
    for (const name of ['作者 A', '作者 B', '短片', '收藏', 'Paid', 'Free']) fireEvent.click(screen.getByRole('button', { name }));
    expect(screen.getByRole('button', { name: '作者 A' }).getAttribute('aria-pressed')).toBe('false');
    expect((screen.getByRole('button', { name: '保存标签' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Paid 应搭配/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Main Tier' }));
    fireEvent.click(screen.getByRole('button', { name: '保存标签' }));
    await waitFor(() => expect(binding.tags_revision).toBe(3));
    const [, init] = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT')!;
    expect(JSON.parse(init.body)).toEqual({ tag_ids: [2, 9, 10, 5, 7], expected_revision: 2 });
  });

  it('retains selected tags on a failed save and permits a retry', async () => {
    failStatus = 503; render(<WorkTagEditor work={work} onClose={vi.fn()} onSaved={vi.fn()} />);
    await screen.findByRole('button', { name: '作者 A' }); fireEvent.click(screen.getByRole('button', { name: '作者 A' }));
    fireEvent.click(screen.getByRole('button', { name: '保存标签' })); await screen.findByText(/标签未保存：版本有冲突/);
    expect(screen.getByRole('button', { name: '作者 A' }).getAttribute('aria-pressed')).toBe('true');
    failStatus = 0; fireEvent.click(screen.getByRole('button', { name: '保存标签' }));
    await waitFor(() => expect(binding.tags).toHaveLength(1));
  });

  it('does not overwrite a 409 conflict and reloads authoritative choices before saving again', async () => {
    failStatus = 409; render(<WorkTagEditor work={work} onClose={vi.fn()} onSaved={vi.fn()} />);
    await screen.findByRole('button', { name: '作者 A' }); fireEvent.click(screen.getByRole('button', { name: '作者 A' }));
    fireEvent.click(screen.getByRole('button', { name: '保存标签' })); await screen.findByText(/标签已被其他客户端修改/);
    fireEvent.click(screen.getByRole('button', { name: '保存标签' }));
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1);
    binding = { work_id: 7, tags: [catalog[1]], tags_revision: 8 }; failStatus = 0;
    fireEvent.click(screen.getByRole('button', { name: '刷新并重新编辑' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '作者 B' }).getAttribute('aria-pressed')).toBe('true'));
    fireEvent.click(screen.getByRole('button', { name: '作者 A' })); fireEvent.click(screen.getByRole('button', { name: '保存标签' }));
    await waitFor(() => expect(binding.tags_revision).toBe(9));
  });
});

describe('shared author tag management', () => {
  it('shows support conflicts without picking a URL, and saves an explicitly selected URL with its tag revision', async () => {
    catalog[0] = { ...catalog[0], support_candidates: ['https://one.example/creator', 'https://two.example/creator'], revision: 4 };
    importReport = { matched: 58, skipped: 29, created: 17, bindings: 91, conflicts: [{ name: '作者 A', message: '作者支持地址存在冲突', values: catalog[0].support_candidates }], warnings: [], dry_run: false };
    const changed = vi.fn(); render(<TagsPage revision={0} onChanged={changed} />);
    await screen.findByText('最近一次历史资料导入'); expect(screen.getByText('58')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '编辑标签 作者 A' }));
    expect((screen.getByLabelText('支持地址状态') as HTMLSelectElement).value).toBe('unknown');
    expect(screen.queryByLabelText('支持作者 URL')).toBeNull();
    fireEvent.click(screen.getAllByRole('button', { name: '使用此地址' })[1]);
    expect((screen.getByLabelText('支持作者 URL') as HTMLInputElement).value).toBe('https://two.example/creator');
    fireEvent.click(screen.getByRole('button', { name: '保存资料' })); await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    const [, init] = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')!;
    expect(JSON.parse(init.body)).toEqual({ expected_revision: 4, name: '作者 A', support_status: 'url', support_url: 'https://two.example/creator' });
  });

  it('validates author support URLs and keeps failed edits', async () => {
    render(<TagsPage revision={0} onChanged={vi.fn()} />); await screen.findByRole('button', { name: '编辑标签 作者 A' });
    fireEvent.click(screen.getByRole('button', { name: '编辑标签 作者 A' }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '作者新名称' } });
    fireEvent.change(screen.getByLabelText('支持地址状态'), { target: { value: 'url' } });
    fireEvent.change(screen.getByLabelText('支持作者 URL'), { target: { value: 'https://name:secret@example.com/' } });
    fireEvent.click(screen.getByRole('button', { name: '保存资料' })); await screen.findByText(/请输入有效的 http/);
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false);
    fireEvent.change(screen.getByLabelText('支持作者 URL'), { target: { value: 'https://example.com/creator' } }); failStatus = 503;
    fireEvent.click(screen.getByRole('button', { name: '保存资料' })); await screen.findByText(/标签未保存：资料有冲突/);
    expect((screen.getByLabelText('名称') as HTMLInputElement).value).toBe('作者新名称');
    expect((screen.getByLabelText('支持作者 URL') as HTMLInputElement).value).toBe('https://example.com/creator');
  });

  it('creates an author with an explicit no-support status instead of an invented URL', async () => {
    const changed = vi.fn(); render(<TagsPage revision={0} onChanged={changed} />); await screen.findByRole('button', { name: '编辑标签 作者 A' });
    fireEvent.click(screen.getByRole('button', { name: '创建标签' }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '新作者' } });
    fireEvent.change(screen.getByLabelText('支持地址状态'), { target: { value: 'none' } });
    fireEvent.click(screen.getAllByRole('button', { name: '创建标签' })[1]);
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    const [, init] = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!;
    expect(JSON.parse(init.body)).toEqual({ category: 'author', name: '新作者', support_status: 'none', support_url: null });
  });
});

// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { TagChips, TagsPage, WorkTagEditor } from './Tags';
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
        binding = { ...binding, tags: [...catalog.filter(tag => body.tag_ids.includes(tag.id) && tag.category !== 'duration'), ...binding.tags.filter(tag => tag.category === 'duration')], tags_revision: binding.tags_revision + 1 };
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

describe('consistent compact tags', () => {
  it('shows one flat text style without category prefixes or duplicate duration', () => {
    render(<TagChips tags={[catalog[0], catalog[2], makeTag(12, 'duration', '18 分钟'), catalog[8]]} />);
    expect(screen.getByTitle('作者：作者 A').className).toContain('tag-chip author');
    expect(screen.getByTitle('视频类型：Real')).toBeTruthy();
    expect(screen.getByTitle('自定义分类：短片')).toBeTruthy();
    expect(document.querySelector('.tag-chip-category')).toBeNull();
    expect(screen.queryByText('18 分钟')).toBeNull();
  });
  it('does not invent a duration value when measurement is missing', () => {
    render(<TagChips tags={[]} durationStatus="unknown" durationError="读取失败" />);
    expect(screen.getByText('未标注')).toBeTruthy();
    expect(screen.queryByText('0 分钟')).toBeNull();
    expect(screen.queryByText('待读取')).toBeNull();
  });
});

describe('direct tag transfers', () => {
  it('undoes the most recent transfer through the current server revision', async () => {
    binding.tags = [catalog[0]];
    render(<WorkTagEditor work={work} onClose={vi.fn()} onSaved={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '作者 B' }));
    await waitFor(() => expect(binding.tags_revision).toBe(3));
    fireEvent.click(screen.getByRole('button', { name: '撤销' }));
    await waitFor(() => expect(binding.tags_revision).toBe(4));
    expect(binding.tags.map(tag => tag.id)).toEqual([1]);
    expect(screen.queryByRole('button', { name: '撤销' })).toBeNull();
    const writes = fetchMock.mock.calls.filter(([, options]) => options?.method === 'PUT');
    expect(JSON.parse(writes.at(-1)![1].body)).toEqual({ tag_ids: [1], expected_revision: 3 });
  });

  it('blocks duplicate transfers and closing while a server write is pending', async () => {
    let finish: (value: Response) => void = () => {};
    const originalFetch = fetchMock.getMockImplementation() as (url: string, init?: RequestInit) => Promise<Response>;
    fetchMock.mockImplementation((url: string, init?: RequestInit) => init?.method === 'PUT' ? new Promise<Response>(resolve => { finish = resolve; }) : originalFetch(url, init));
    const onClose = vi.fn();
    render(<WorkTagEditor work={work} onClose={onClose} onSaved={vi.fn()} />);
    const choice = await screen.findByRole('button', { name: '作者 A' });
    fireEvent.click(choice); fireEvent.click(choice);
    fireEvent.click(screen.getByRole('button', { name: '完成' }));
    fireEvent.click(screen.getByRole('button', { name: '关闭标签编辑' }));
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(1);
    expect(onClose).not.toHaveBeenCalled();
    finish(response({ work_id: 7, tags: [catalog[0]], tags_revision: 3 }));
    await waitFor(() => expect(screen.getByRole('button', { name: '作者 A' }).getAttribute('aria-pressed')).toBe('true'));
    expect((screen.getByRole('button', { name: '完成' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('adds on the right, removes on the left, and saves each step with the latest revision', async () => {
    const onSaved = vi.fn(), onClose = vi.fn();
    render(<WorkTagEditor work={work} onClose={onClose} onSaved={onSaved} />);
    const library = await screen.findByRole('region', { name: '标签库' });
    fireEvent.click(within(library).getByRole('button', { name: '作者 A' }));
    await waitFor(() => expect(binding.tags_revision).toBe(3));
    const current = screen.getByRole('region', { name: '当前标签' });
    expect(within(current).getByRole('button', { name: '作者 A' })).toBeTruthy();
    expect(within(library).queryByRole('button', { name: '作者 A' })).toBeNull();
    fireEvent.click(within(current).getByRole('button', { name: '作者 A' }));
    await waitFor(() => expect(binding.tags_revision).toBe(4));
    const requests = fetchMock.mock.calls.filter(([, options]) => options?.method === 'PUT');
    expect(requests.map(([, options]) => JSON.parse(options.body))).toEqual([{ tag_ids: [1], expected_revision: 2 }, { tag_ids: [], expected_revision: 3 }]);
    expect(onSaved).toHaveBeenCalledTimes(2); expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '完成' })); expect(onClose).toHaveBeenCalledOnce();
  });

  it('replaces single-choice tags in one request and permits multiple custom tags', async () => {
    binding.tags = [catalog[0]];
    render(<WorkTagEditor work={work} onClose={vi.fn()} onSaved={vi.fn()} />);
    await screen.findByRole('button', { name: '作者 B' });
    fireEvent.click(screen.getByRole('button', { name: '作者 B' }));
    await waitFor(() => expect(binding.tags.map(tag => tag.id)).toEqual([2]));
    for (const name of ['短片', '收藏']) {
      fireEvent.click(screen.getByRole('button', { name }));
      await waitFor(() => expect(screen.getByRole('button', { name }).getAttribute('aria-pressed')).toBe('true'));
    }
    expect(binding.tags.map(tag => tag.id)).toEqual([2, 9, 10]);
    const requests = fetchMock.mock.calls.filter(([, options]) => options?.method === 'PUT');
    expect(JSON.parse(requests[0][1].body)).toEqual({ tag_ids: [2], expected_revision: 2 });
  });

  it('prevents incompatible release/tier combinations without changing server tags', async () => {
    binding.tags = [catalog[4], catalog[6]];
    render(<WorkTagEditor work={work} onClose={vi.fn()} onSaved={vi.fn()} />);
    await screen.findByRole('button', { name: 'Free' });
    fireEvent.click(screen.getByRole('button', { name: 'Free' }));
    expect(screen.getByRole('alert').textContent).toContain('Paid 应搭配');
    expect(binding.tags.map(tag => tag.id)).toEqual([5, 7]);
    expect(fetchMock.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false);
  });

  it('keeps the authoritative selection on failure and retries the same requested change', async () => {
    failStatus = 503;
    render(<WorkTagEditor work={work} onClose={vi.fn()} onSaved={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '作者 A' }));
    await screen.findByText(/标签未保存：版本有冲突/);
    expect(screen.getByRole('button', { name: '作者 A' }).getAttribute('aria-pressed')).toBe('false');
    failStatus = 0; fireEvent.click(screen.getByRole('button', { name: '重试保存' }));
    await waitFor(() => expect(binding.tags.map(tag => tag.id)).toEqual([1]));
  });

  it('blocks writes after 409 and refreshes authoritative tags before a new choice', async () => {
    failStatus = 409;
    render(<WorkTagEditor work={work} onClose={vi.fn()} onSaved={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '作者 A' }));
    await screen.findByText(/标签已被其他客户端修改/);
    fireEvent.click(screen.getByRole('button', { name: '作者 B' }));
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(1);
    binding = { work_id: 7, tags: [catalog[1]], tags_revision: 8 }; failStatus = 0;
    fireEvent.click(screen.getByRole('button', { name: '刷新并重新编辑' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '作者 B' }).getAttribute('aria-pressed')).toBe('true'));
    fireEvent.click(screen.getByRole('button', { name: '作者 A' }));
    await waitFor(() => expect(binding.tags_revision).toBe(9));
    const writes = fetchMock.mock.calls.filter(([, options]) => options?.method === 'PUT');
    expect(JSON.parse(writes.at(-1)![1].body)).toEqual({ tag_ids: [1], expected_revision: 8 });
  });

  it('shows all editable categories without paging and keeps category/search filters independent of selected labels', async () => {
    catalog.push(...Array.from({ length: 50 }, (_, index) => makeTag(100 + index, 'author', `新增作者 ${index}`)));
    catalog.push(makeTag(160, 'axis_type', '单轴'), makeTag(161, 'axis_type', '多轴'), makeTag(162, 'duration', '18 分钟'));
    binding.tags = [catalog[0]];
    render(<WorkTagEditor work={work} onClose={vi.fn()} onSaved={vi.fn()} />);
    const library = await screen.findByRole('region', { name: '标签库' });
    const countOptions = () => within(library).getAllByRole('button').filter(button => button.classList.contains('tag-option')).length;
    expect(countOptions()).toBe(catalog.filter(tag => tag.category !== 'duration' && tag.id !== 1).length);
    for (const category of ['作者', '视频类型', '轴类型', '发布类型', '档位', '自定义分类']) {
      expect(within(library).getByRole('heading', { name: category })).toBeTruthy();
    }
    expect(within(library).getByRole('button', { name: '新增作者 49' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '标签库下一页' })).toBeNull();
    expect(screen.queryByRole('button', { name: '标签库上一页' })).toBeNull();
    expect(within(library).queryByRole('button', { name: '18 分钟' })).toBeNull();
    const categories = within(library).getByRole('navigation', { name: '标签类别' });
    expect(within(categories).queryByRole('button', { name: '时间' })).toBeNull();
    fireEvent.click(within(categories).getByRole('button', { name: '作者' }));
    expect(countOptions()).toBe(51);
    fireEvent.change(screen.getByLabelText('搜索作者或标签'), { target: { value: '新增作者 49' } });
    expect(countOptions()).toBe(1);
    expect(within(screen.getByRole('region', { name: '当前标签' })).getByRole('button', { name: '作者 A' })).toBeTruthy();
    fireEvent.click(within(categories).getByRole('button', { name: '视频类型' }));
    expect(within(library).queryByRole('button', { name: '新增作者 49' })).toBeNull();
    expect(within(library).getByText('没有匹配的标签')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('搜索作者或标签'), { target: { value: '' } });
    expect(countOptions()).toBe(2);
    expect(within(library).getByRole('button', { name: 'Real' })).toBeTruthy();
    expect(within(library).getByRole('button', { name: 'Anime' })).toBeTruthy();
    expect(within(screen.getByRole('region', { name: '当前标签' })).getByRole('button', { name: '作者 A' })).toBeTruthy();
    fireEvent.click(within(categories).getByRole('button', { name: '全部类别' }));
    expect(countOptions()).toBe(61);
  });

  it('keeps automatic duration bindings out of manual transfer requests', async () => {
    const duration = makeTag(12, 'duration', '18 分钟'); catalog.push(duration); binding.tags = [catalog[0], duration];
    render(<WorkTagEditor work={work} onClose={vi.fn()} onSaved={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '作者 A' }));
    await waitFor(() => expect(binding.tags_revision).toBe(3));
    expect(screen.queryByText('18 分钟')).toBeNull();
    expect(binding.tags.map(tag => tag.id)).toEqual([12]);
    const write = fetchMock.mock.calls.find(([, options]) => options?.method === 'PUT')!;
    expect(JSON.parse(write[1].body)).toEqual({ tag_ids: [], expected_revision: 2 });
  });
});
describe('shared author tag management', () => {
  it('uses the sample category rail and table with inline editing and real linked works', async () => {
    const openWork = vi.fn();
    render(<TagsPage revision={0} onChanged={vi.fn()} onOpenWork={openWork} />);
    const edit = await screen.findByRole('button', { name: '编辑标签 作者 A' });
    expect(document.querySelector('.tag-manager-layout>.tag-manager-categories')).toBeTruthy();
    expect(screen.getByText('作品数')).toBeTruthy();
    fireEvent.click(edit);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.querySelector('.tag-catalog-editor .tag-catalog-edit-grid')).toBeTruthy();
    const related = await screen.findByRole('button', { name: /S025_001 · 作品标题/ });
    fireEvent.click(related); expect(openWork).toHaveBeenCalledWith(7);
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/works?tag_id=1&page=1&page_size=6')).toBe(true);
  });

  it('keeps an inline draft until save or cancellation before switching categories', async () => {
    render(<TagsPage revision={0} onChanged={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '编辑标签 作者 A' }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '未保存名称' } });
    const category = within(screen.getByRole('navigation', { name: '标签类别' })).getByRole('button', { name: '视频类型 2' });
    expect((category as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(category);
    expect((screen.getByLabelText('名称') as HTMLInputElement).value).toBe('未保存名称');
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect((category as HTMLButtonElement).disabled).toBe(false);
  });

  it('keeps automatic duration management read-only and excludes it from new tag categories', async () => {
    catalog.push(makeTag(12, 'duration', '18 分钟'));
    render(<TagsPage revision={0} onChanged={vi.fn()} />);
    await screen.findByRole('button', { name: '编辑标签 作者 A' });
    const categoryNavigation = screen.getByRole('navigation', { name: '标签类别' });
    fireEvent.click(within(categoryNavigation).getByRole('button', { name: '时间 1' }));
    fireEvent.click(screen.getByRole('button', { name: '选择标签 18 分钟' }));
    expect(screen.getByText('自动更新 · 只读')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '创建标签' })).toBeNull();
    expect(screen.queryByRole('button', { name: '编辑标签 18 分钟' })).toBeNull();
    fireEvent.click(within(categoryNavigation).getByRole('button', { name: '作者 2' }));
    fireEvent.click(screen.getByRole('button', { name: '创建标签' }));
    expect(within(screen.getByRole('group', { name: '类别' })).queryByRole('button', { name: '时间' })).toBeNull();
  });

  it('shows support conflicts without picking a URL, and saves an explicitly selected URL with its tag revision', async () => {
    catalog[0] = { ...catalog[0], support_candidates: ['https://one.example/creator', 'https://two.example/creator'], revision: 4 };
    importReport = { matched: 58, skipped: 29, created: 17, bindings: 91, conflicts: [{ name: '作者 A', message: '作者支持地址存在冲突', values: catalog[0].support_candidates }], warnings: [], dry_run: false };
    const changed = vi.fn(); render(<TagsPage revision={0} onChanged={changed} />);
    await screen.findByText('最近一次历史资料导入'); expect(screen.getByText('58')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '编辑标签 作者 A' }));
    expect(screen.getByRole('button', { name: '未填写 / 待确认' }).getAttribute('aria-pressed')).toBe('true');
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
    fireEvent.click(screen.getByRole('button', { name: '已有支持地址' }));
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
    fireEvent.click(screen.getByRole('button', { name: '明确无支持地址' }));
    fireEvent.click(screen.getAllByRole('button', { name: '创建标签' })[1]);
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    const [, init] = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!;
    expect(JSON.parse(init.body)).toEqual({ category: 'author', name: '新作者', support_status: 'none', support_url: null });
  });
});

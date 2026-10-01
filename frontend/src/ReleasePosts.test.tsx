// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EsTemplateSettings, ReleasePostEditor } from './ReleasePosts';

const inputs = { release_title: '', intro_markdown: '', cover_markdown: '', preview_markdown: '', heatmap_markdown: '', attachment_markdown: '', selected_script_ids: [], selected_preview_filenames: [], no_creator_link: false, video_asset_id: null };
const output = { title: '【Free】【S999】【Single-axis】 Test release', body: '[center][/center]\n\n[Preview will be inserted here]', status: 'draft', missing: ['请上传预览'], warnings: [], generated_at: '2026-10-02T00:00:00Z', template_revision: 0, stale: false };
const state = { work_id: 999, revision: 3, inputs, output, sources: { scripts: [{ id: 15, name: 'sample.funscript', relative_path: 'sample.funscript', kind: 'script', size: 30, directory_id: 1, download_url: '/api/works/999/es-post/scripts/15' }], videos: [], previews: [{ filename: 'preview.gif', kind: 'gif', size: 50, clip_index: 1, width: 192, height: 108, url: '/api/works/999/preview/files/preview.gif' }], author_support: { name: 'Author', status: 'unknown', url: null } } };
const work = { id: 999, script_id: 'S999', title: 'Test release', notes: 'private inventory note', tags: [{ category: 'release_type', name: 'Free Sample' }] };
const template = { name: 'Local template', body: '{{header}}\n{{preview}}\n{{recent}}\n{{footer}}', config: { brandingHeaderMarkdown: '[center][/center]', brandingFooterMarkdown: '', recentPinnedIds: ['S046'], promoButtons: [{ id: 'video-link', label: 'Video Link', imageUrl: '', linkSource: 'videoLink', group: 'action', width: '300' }], retainedOption: 'keep' }, revision: 2 };
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function(this: HTMLDialogElement) { this.open = true; } });
  fetchMock = vi.fn((path: string) => Promise.resolve(response(path === '/api/works/999' ? work : path === '/api/es-template' ? template : state)));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); Reflect.deleteProperty(document, 'execCommand'); });

describe('ES post editing and generation', () => {
  it('loads a saved draft without regenerating or exposing private notes', async () => {
    render(<ReleasePostEditor workId={999} onClose={vi.fn()} />);
    await screen.findByLabelText('贴文正文');
    expect(fetchMock.mock.calls).toHaveLength(2);
    expect(screen.getByText('请上传预览')).toBeTruthy();
    expect((screen.getByLabelText(/公开发布说明/) as HTMLTextAreaElement).value).toBe('');
    expect(document.body.textContent).not.toContain('private inventory note');
    expect((screen.getByRole('checkbox', { name: /sample.funscript/ }) as HTMLInputElement).checked).toBe(false);
  });

  it('generates and persists a draft automatically when no saved output exists', async () => {
    fetchMock.mockImplementation((path: string, init?: RequestInit) => Promise.resolve(response(path === '/api/works/999' ? work : init?.method === 'POST' ? { ...state, revision: 4 } : { ...state, output: null })));
    render(<ReleasePostEditor workId={999} onClose={vi.fn()} />);
    await screen.findByLabelText('贴文正文');
    const generate = fetchMock.mock.calls.find(call => call[0].endsWith('/generate'));
    expect(generate?.[1]?.method).toBe('POST');
    expect(JSON.parse(generate?.[1]?.body as string)).toEqual({ expected_revision: 3 });
  });

  it('saves explicit script and preview selection before generating with the new revision', async () => {
    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/api/works/999') return Promise.resolve(response(work));
      if (init?.method === 'PUT') return Promise.resolve(response({ ...state, revision: 4, inputs: JSON.parse(init.body as string).inputs, output: { ...output, stale: true } }));
      if (init?.method === 'POST') return Promise.resolve(response({ ...state, revision: 5, output: { ...output, status: 'ready', missing: [] } }));
      return Promise.resolve(response(state));
    });
    render(<ReleasePostEditor workId={999} onClose={vi.fn()} />);
    await screen.findByLabelText('贴文正文');
    fireEvent.click(screen.getByRole('checkbox', { name: /sample.funscript/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: '正文预览素材：preview.gif' }));
    fireEvent.change(screen.getByLabelText(/ES 预览 Markdown/), { target: { value: '![preview](upload://preview.gif)' } });
    fireEvent.change(screen.getByLabelText('ES 脚本附件 Markdown'), { target: { value: '[script](upload://sample.funscript)' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /我确认这个作品没有支持作者的链接/ }));
    expect((screen.getByRole('button', { name: '复制正文' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '更新贴文' }));
    await screen.findByText('资料齐全');
    const saved = fetchMock.mock.calls.find(call => call[1]?.method === 'PUT');
    expect(JSON.parse(saved?.[1]?.body as string)).toMatchObject({ expected_revision: 3, inputs: { selected_script_ids: [15], selected_preview_filenames: ['preview.gif'], no_creator_link: true } });
    const generated = fetchMock.mock.calls.find(call => call[1]?.method === 'POST');
    expect(JSON.parse(generated?.[1]?.body as string)).toEqual({ expected_revision: 4 });
  });

  it('copies body using a modal-local fallback on LAN HTTP', async () => {
    const copy = vi.fn(() => {
      expect(document.activeElement?.closest('dialog')).toBeTruthy();
      expect((document.activeElement as HTMLTextAreaElement).value).toBe(output.body);
      return true;
    });
    Object.defineProperty(document, 'execCommand', { configurable: true, value: copy });
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    render(<ReleasePostEditor workId={999} onClose={vi.fn()} />);
    await screen.findByLabelText('贴文正文');
    fireEvent.click(screen.getByRole('button', { name: '复制正文' }));
    await screen.findByText('正文已复制');
    expect(copy).toHaveBeenCalledWith('copy');
  });

  it('saves a cover independently of incomplete release data using the new input revision', async () => {
    let savedInputs = inputs;
    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/api/works/999') return Promise.resolve(response(work));
      if (init?.method === 'PUT') { savedInputs = JSON.parse(init.body as string).inputs; return Promise.resolve(response({ ...state, revision: 4, inputs: savedInputs, output: { ...output, stale: true } })); }
      if (path.endsWith('/cover')) return Promise.resolve(response({ ...state, revision: 5, inputs: savedInputs, saved_cover_url: 'upload://cover-a.gif', output: { ...output, stale: true } }));
      return Promise.resolve(response(state));
    });
    render(<ReleasePostEditor workId={999} onClose={vi.fn()} />);
    await screen.findByLabelText('贴文正文');
    fireEvent.change(screen.getByLabelText(/ES 封面 Markdown/), { target: { value: '![cover](upload://cover-a.gif)' } });
    fireEvent.change(screen.getByLabelText(/ES 预览 Markdown/), { target: { value: '![video](upload://video-b.webm)' } });
    fireEvent.click(screen.getByRole('button', { name: '保存预览封面' }));
    await screen.findByText('预览封面已保存，之后生成贴文时会自动取用');
    const coverCall = fetchMock.mock.calls.find(call => call[0].endsWith('/cover'));
    expect(JSON.parse(coverCall?.[1]?.body as string)).toEqual({ expected_revision: 4 });
    expect(savedInputs).toMatchObject({ cover_markdown: '![cover](upload://cover-a.gif)', preview_markdown: '![video](upload://video-b.webm)' });
    expect((screen.getByLabelText(/ES 封面 Markdown/) as HTMLTextAreaElement).value).toBe('![cover](upload://cover-a.gif)');
    expect((screen.getByLabelText(/ES 预览 Markdown/) as HTMLTextAreaElement).value).toBe('![video](upload://video-b.webm)');
    expect(screen.getByText(/已保存封面，可用于近期作品预览/)).toBeTruthy();
    expect(screen.getByText('请上传预览')).toBeTruthy();
    expect(document.querySelector('img')).toBeNull();
  });

  it('keeps video-only Markdown when cover validation rejects it', async () => {
    fetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/api/works/999') return Promise.resolve(response(work));
      if (init?.method === 'PUT') return Promise.resolve(response({ ...state, revision: 4, inputs: JSON.parse(init.body as string).inputs }));
      if (path.endsWith('/cover')) return Promise.resolve(response({ detail: '需要上传 GIF 或图片作为封面' }, 422));
      return Promise.resolve(response(state));
    });
    render(<ReleasePostEditor workId={999} onClose={vi.fn()} />);
    await screen.findByLabelText('贴文正文');
    fireEvent.change(screen.getByLabelText(/ES 封面 Markdown/), { target: { value: '![video](upload://preview.webm)' } });
    fireEvent.click(screen.getByRole('button', { name: '保存预览封面' }));
    await screen.findByRole('alert');
    expect((screen.getByLabelText(/ES 封面 Markdown/) as HTMLTextAreaElement).value).toBe('![video](upload://preview.webm)');
    expect(screen.getByRole('alert').textContent).toContain('GIF 或图片');
  });

  it('separates cover candidates, body preview media and heatmaps', async () => {
    fetchMock.mockImplementation((path: string) => Promise.resolve(response(path === '/api/works/999' ? work : { ...state, inputs: { ...inputs, cover_markdown: '![saved](upload://existing.gif)', preview_markdown: '![video](upload://inside.webm)' }, saved_cover_url: 'upload://existing.gif', sources: { ...state.sources, previews: [...state.sources.previews, { filename: 'movie.webm', kind: 'video', size: 60, url: '/video' }, { filename: 'heatmap.png', kind: 'heatmap', size: 40, url: '/heatmap' }] } })));
    render(<ReleasePostEditor workId={999} onClose={vi.fn()} />);
    await screen.findByLabelText('贴文正文');
    const coverSection = within(screen.getByRole('heading', { name: '封面' }).closest('section')!);
    expect(coverSection.getByRole('checkbox', { name: '封面素材：preview.gif' })).toBeTruthy();
    expect(coverSection.queryByRole('checkbox', { name: /movie.webm|heatmap.png/ })).toBeNull();
    const bodySection = within(screen.getByRole('heading', { name: '贴文内部预览' }).closest('section')!);
    expect(bodySection.getByRole('checkbox', { name: '正文预览素材：movie.webm' })).toBeTruthy();
    expect(bodySection.queryByRole('checkbox', { name: /heatmap.png/ })).toBeNull();
    expect(screen.getByRole('checkbox', { name: '热力图素材：heatmap.png' })).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/ES 预览 Markdown/), { target: { value: '![new](upload://new-body.gif)' } });
    expect((screen.getByLabelText(/ES 封面 Markdown/) as HTMLTextAreaElement).value).toBe('![saved](upload://existing.gif)');
    expect(screen.getByText(/已保存封面，可用于近期作品预览/)).toBeTruthy();
  });

  it('retains edits on a revision conflict and guards unsaved closing', async () => {
    const close = vi.fn();
    fetchMock.mockImplementation((path: string, init?: RequestInit) => Promise.resolve(init?.method === 'PUT' ? response({ detail: '其他页面已更新，当前输入已保留' }, 409) : response(path === '/api/works/999' ? work : state)));
    render(<ReleasePostEditor workId={999} onClose={close} />);
    await screen.findByLabelText('贴文正文');
    fireEvent.change(screen.getByLabelText(/发布标题/), { target: { value: 'My edited title' } });
    fireEvent.click(screen.getByRole('button', { name: '保存资料' }));
    await screen.findByRole('alert');
    expect((screen.getByLabelText(/发布标题/) as HTMLInputElement).value).toBe('My edited title');
    fireEvent.click(screen.getByRole('button', { name: /^关闭$/ }));
    expect(close).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
    expect((screen.getByLabelText(/发布标题/) as HTMLInputElement).value).toBe('My edited title');
  });

  it('keeps script attachment selection out of paid releases', async () => {
    fetchMock.mockImplementation((path: string) => Promise.resolve(response(path === '/api/works/999' ? { ...work, tags: [{ category: 'release_type', name: 'Paid' }] } : state)));
    render(<ReleasePostEditor workId={999} onClose={vi.fn()} />);
    await screen.findByLabelText('贴文正文');
    expect(screen.queryByLabelText('ES 脚本附件 Markdown')).toBeNull();
    expect(screen.queryByRole('checkbox', { name: /sample.funscript/ })).toBeNull();
  });
});

describe('database-backed ES template editing', () => {
  it('edits structured fields and preserves other theme options with revision protection', async () => {
    fetchMock.mockImplementation((_path: string, init?: RequestInit) => Promise.resolve(response(init?.method === 'PUT' ? { ...JSON.parse(init.body as string), revision: 3 } : template)));
    render(<EsTemplateSettings />);
    await screen.findByLabelText('模板名称');
    fireEvent.change(screen.getByLabelText('模板名称'), { target: { value: 'My template' } });
    fireEvent.click(screen.getByText('主题与导航设置'));
    fireEvent.change(screen.getByLabelText(/品牌页脚 Markdown/), { target: { value: '[center]footer[/center]' } });
    fireEvent.change(screen.getByLabelText(/近期作品固定编号/), { target: { value: 'S046, S999' } });
    fireEvent.click(screen.getByRole('button', { name: '保存 ES 模板' }));
    await screen.findByRole('status');
    const saved = fetchMock.mock.calls.find(call => call[1]?.method === 'PUT');
    expect(JSON.parse(saved?.[1]?.body as string)).toMatchObject({ name: 'My template', expected_revision: 2, config: { retainedOption: 'keep', recentPinnedIds: ['S046', 'S999'], brandingFooterMarkdown: '[center]footer[/center]' } });
  });

  it('retains malformed advanced JSON and never submits it', async () => {
    render(<EsTemplateSettings />);
    await screen.findByLabelText('模板名称');
    fireEvent.click(screen.getByText('高级设置 JSON'));
    fireEvent.change(screen.getByLabelText(/主题配置 JSON/), { target: { value: '{oops' } });
    fireEvent.click(screen.getByRole('button', { name: '保存 ES 模板' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('有效的 JSON'));
    expect((screen.getByLabelText(/主题配置 JSON/) as HTMLTextAreaElement).value).toBe('{oops');
    expect(fetchMock.mock.calls).toHaveLength(1);
  });
});

import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Check, CircleAlert, LoaderCircle, Plus, RefreshCw, Search, Tag as TagIcon, X } from 'lucide-react';
import { ApiError, errorMessage, formatDate, request, safeLink } from './api';
import type { Tag, TagCatalog, TagCategory, Work, WorkTags } from './api';

export const tagCategories: [TagCategory, string][] = [['author', '作者'], ['video_type', '视频类型'], ['release_type', '发布类型'], ['tier', '档位'], ['custom', '自定义分类']];
export function tagLabel(category: TagCategory) { return tagCategories.find(([key]) => key === category)?.[1] || category; }
export function tagCombinationError(tags: Tag[]) {
  const release = tags.find(tag => tag.category === 'release_type')?.name;
  const tier = tags.find(tag => tag.category === 'tier')?.name;
  if (!release || !tier) return '';
  if (release === 'Free Sample' && tier !== 'Free') return 'Free Sample 应搭配 Free 档位，请调整发布类型或档位。';
  if (release === 'Paid' && tier === 'Free') return 'Paid 应搭配 Main Tier 或 Extra Tier，请调整发布类型或档位。';
  return '';
}
export function TagChips({ tags = [], emptyLabel = '未标注' }: { tags?: Tag[]; emptyLabel?: string }) {
  return tags.length ? <div className="tag-chips">{tags.map(tag => <span className={`tag-chip ${tag.category}`} key={tag.id} title={`${tagLabel(tag.category)}：${tag.name}`}>{tag.name}</span>)}</div> : <span className="tag-empty">{emptyLabel}</span>;
}
export function useTagCatalog(revision: number) {
  const [catalog, setCatalog] = useState<TagCatalog | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    request<TagCatalog>('/api/tags', { signal: controller.signal }).then(value => { if (!controller.signal.aborted) { setCatalog(value); setError(''); } }).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); });
    return () => controller.abort();
  }, [revision, retry]);
  return { catalog, error, refresh: () => setRetry(value => value + 1) };
}

function TagDialog({ title, subtitle, children, footer, onClose, dirty, saving }: { title: string; subtitle: string; children: ReactNode; footer: ReactNode; onClose: () => void; dirty: boolean; saving: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const keepButton = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const [confirm, setConfirm] = useState(false);
  const close = () => { if (saving) return; if (dirty) setConfirm(true); else onClose(); };
  useEffect(() => { dialog.current?.showModal(); const previous = document.body.style.overflow; document.body.style.overflow = 'hidden'; return () => { document.body.style.overflow = previous; }; }, []);
  useEffect(() => { if (confirm) keepButton.current?.focus(); }, [confirm]);
  return <dialog ref={dialog} className="tag-dialog" aria-labelledby="tag-dialog-title" onCancel={event => { event.preventDefault(); close(); }}><div className="detail-header"><div><span className="section-label">{subtitle}</span><h2 id="tag-dialog-title">{title}</h2></div><button ref={closeButton} autoFocus className="icon-button" aria-label="关闭标签编辑" onClick={close} disabled={saving}><X size={20} /></button></div>{confirm && <div className="discard-confirm" role="alert"><strong>标签修改尚未保存</strong><p>关闭会放弃本次修改。</p><div><button className="button small" ref={keepButton} onClick={() => { setConfirm(false); closeButton.current?.focus(); }}>继续编辑</button><button className="button small" onClick={onClose}>放弃标签修改并关闭</button></div></div>}<div className="tag-dialog-body">{children}</div><div className="detail-actions">{footer}</div></dialog>;
}

export function WorkTagEditor({ work, onClose, onSaved }: { work: Pick<Work, 'id' | 'script_id' | 'title'>; onClose: () => void; onSaved: (value: WorkTags) => void }) {
  const [catalog, setCatalog] = useState<Tag[]>([]);
  const [binding, setBinding] = useState<WorkTags | null>(null);
  const [ids, setIds] = useState<number[]>([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [retry, setRetry] = useState(0);
  const lock = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true);
    Promise.all([request<TagCatalog>('/api/tags', { signal: controller.signal }), request<WorkTags>(`/api/works/${work.id}/tags`, { signal: controller.signal })]).then(([all, current]) => { if (!controller.signal.aborted) { setCatalog(all.items); setBinding(current); setIds(current.tags.map(tag => tag.id)); setError(''); setConflict(false); } }).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [work.id, retry]);
  const selected = catalog.filter(tag => ids.includes(tag.id));
  const dirty = !!binding && [...ids].sort((a, b) => a - b).join(',') !== binding.tags.map(tag => tag.id).sort((a, b) => a - b).join(',');
  const combinationError = tagCombinationError(selected);
  const choose = (tag: Tag) => { if (saving || conflict) return; setError(''); setIds(previous => previous.includes(tag.id) ? previous.filter(id => id !== tag.id) : [...previous.filter(id => tag.category === 'custom' || catalog.find(item => item.id === id)?.category !== tag.category), tag.id]); };
  const save = async () => {
    if (lock.current || !binding || !dirty || combinationError || conflict) return;
    lock.current = true; setSaving(true); setError('');
    try { const result = await request<WorkTags>(`/api/works/${work.id}/tags`, { method: 'PUT', body: JSON.stringify({ tag_ids: ids, expected_revision: binding.tags_revision }) }); if (alive.current) { setBinding(result); setIds(result.tags.map(tag => tag.id)); onSaved(result); onClose(); } }
    catch (error) { if (alive.current) { setError(error instanceof ApiError && error.status === 409 ? '标签已被其他客户端修改，请刷新最新标签后重新确认。' : `标签未保存：${errorMessage(error)}`); setConflict(error instanceof ApiError && error.status === 409); } }
    finally { lock.current = false; if (alive.current) setSaving(false); }
  };
  return <TagDialog title="编辑作品标签" subtitle={work.script_id} onClose={onClose} dirty={dirty} saving={saving} footer={<div><span className="help-text">{selected.length} 个标签已选择</span><button className="button primary" onClick={() => void save()} disabled={!dirty || saving || loading || !!combinationError || conflict}>{saving ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />}{saving ? '正在保存' : '保存标签'}</button></div>}>
    <p className="tag-work-title">{work.title}</p><p className="help-text">作者和分类每类选择一个，自定义分类可多选。标签不会改变发布状态。</p>
    {error && <div className="notice error" role="alert"><span><CircleAlert size={16} />{error}</span>{(conflict || !binding) && <button className="button small" onClick={() => setRetry(value => value + 1)}>刷新并重新编辑</button>}</div>}
    {loading ? <div className="loading-state" role="status"><LoaderCircle size={18} className="spin" />正在读取标签</div> : binding && <><label className="search-field tag-search"><Search size={17} /><span className="sr-only">搜索作者或标签</span><input type="search" placeholder="搜索作者、标签…" value={query} onChange={event => setQuery(event.target.value)} /></label>{tagCategories.map(([category, label]) => { const options = catalog.filter(tag => tag.category === category && (tag.name.toLowerCase().includes(query.trim().toLowerCase()) || ids.includes(tag.id))); const author = selected.find(tag => tag.category === 'author'); return <fieldset className="tag-choice-group" key={category} disabled={saving || conflict}><legend>{label}<small>{category === 'custom' ? '多选' : '单选，可取消'}</small></legend><div className="tag-options">{options.map(tag => <button type="button" className={ids.includes(tag.id) ? 'tag-option selected' : 'tag-option'} aria-pressed={ids.includes(tag.id)} onClick={() => choose(tag)} key={tag.id}>{ids.includes(tag.id) && <Check size={14} />}{tag.name}</button>)}</div>{!options.length && <p className="help-text">{query ? '没有匹配的标签' : '暂无标签，可在标签管理中创建。'}</p>}{category === 'author' && author && <p className="help-text">{author.support_status === 'url' ? <a href={safeLink(author.support_url) || undefined} target="_blank" rel="noreferrer">支持 {author.name}</a> : author.support_status === 'none' ? '该作者已明确无支持地址。' : author.support_candidates?.length ? '该作者存在支持地址冲突，请在标签管理中确认。' : '该作者支持地址尚未填写。'}</p>}</fieldset>; })}{combinationError && <p className="inline-error" role="alert">{combinationError}</p>}<p className="help-text">支持地址随作者标签复用；需要新增或修改标签，可前往“标签管理”。</p></>}
  </TagDialog>;
}

function CatalogEditor({ tag, onClose, onSaved }: { tag: Tag | null; onClose: () => void; onSaved: () => void }) {
  const [current, setCurrent] = useState<Tag | null>(tag);
  const [category, setCategory] = useState<TagCategory>(tag?.category || 'author');
  const [name, setName] = useState(tag?.name || '');
  const [status, setStatus] = useState<Tag['support_status']>(tag?.support_status || 'unknown');
  const [url, setUrl] = useState(tag?.support_url || '');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const lock = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const dirty = current ? name !== current.name || status !== current.support_status || (status === 'url' && url !== (current.support_url || '')) : !!name.trim() || status !== 'unknown' || !!url;
  const refresh = async () => {
    if (!current || saving) return; setSaving(true);
    try { const all = await request<TagCatalog>('/api/tags'); const latest = all.items.find(item => item.id === current.id); if (!latest) throw new Error('此标签已不存在，请关闭后重新打开。'); if (alive.current) { setCurrent(latest); setCategory(latest.category); setName(latest.name); setStatus(latest.support_status); setUrl(latest.support_url || ''); setConflict(false); setError(''); } }
    catch (error) { if (alive.current) setError(errorMessage(error)); }
    finally { if (alive.current) setSaving(false); }
  };
  const save = async () => {
    if (lock.current || conflict || !name.trim()) return;
    let supportUrl: string | null = null;
    if (category === 'author' && status === 'url') {
      try { const parsed = new URL(url.trim()); if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error(); supportUrl = parsed.href; }
      catch { setError('请输入有效的 http(s) 支持地址，不包含用户名或密码。'); return; }
    }
    lock.current = true; setSaving(true); setError('');
    try { await request<Tag>(current ? `/api/tags/${current.id}` : '/api/tags', { method: current ? 'PATCH' : 'POST', body: JSON.stringify({ ...(current ? { expected_revision: current.revision } : { category }), name: name.trim(), ...(category === 'author' ? { support_status: status, support_url: supportUrl } : {}) }) }); if (alive.current) { onSaved(); onClose(); } }
    catch (error) { if (alive.current) { const stale = !!current && error instanceof ApiError && error.status === 409; setError(stale ? '该标签已被其他客户端修改，请刷新最新资料后重新编辑。' : `标签未保存：${errorMessage(error)}`); setConflict(stale); } }
    finally { lock.current = false; if (alive.current) setSaving(false); }
  };
  const choices = category === 'release_type' ? ['Free Sample', 'Paid'] : category === 'tier' ? ['Free', 'Main Tier', 'Extra Tier'] : null;
  return <TagDialog title={current ? '编辑标签资料' : '创建标签'} subtitle="标签管理" onClose={onClose} dirty={dirty} saving={saving} footer={<div><span className="help-text">{current ? '绑定此标签的作品共享资料' : '标签保存在服务端'}</span><button className="button primary" onClick={() => void save()} disabled={saving || !name.trim() || conflict || !!current && !dirty}>{saving ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />}{saving ? '正在保存' : current ? '保存资料' : '创建标签'}</button></div>}>
    {error && <div className="notice error" role="alert"><span>{error}</span>{conflict && <button className="button small" onClick={() => void refresh()}>刷新并重新编辑</button>}</div>}
    <div className="tag-manage-form"><label htmlFor="tag-category">类别</label><select id="tag-category" value={category} disabled={!!current || saving || conflict} onChange={event => { const next = event.target.value as TagCategory; setCategory(next); setName(next === 'release_type' ? 'Free Sample' : next === 'tier' ? 'Free' : ''); setStatus('unknown'); setUrl(''); }}>{tagCategories.map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select><label htmlFor="tag-name">名称</label>{choices ? <select id="tag-name" value={name} onChange={event => setName(event.target.value)} disabled={saving || conflict}>{!name && <option value="">请选择</option>}{choices.map(value => <option key={value}>{value}</option>)}</select> : <input id="tag-name" maxLength={200} value={name} onChange={event => setName(event.target.value)} disabled={saving || conflict} />}
      {category === 'author' && <><label htmlFor="tag-support-status">支持地址状态</label><select id="tag-support-status" value={status} onChange={event => setStatus(event.target.value as Tag['support_status'])} disabled={saving || conflict}><option value="unknown">未填写 / 待确认</option><option value="none">明确无支持地址</option><option value="url">已有支持地址</option></select>{status === 'url' && <><label htmlFor="tag-support-url">支持作者 URL</label><input id="tag-support-url" type="url" value={url} placeholder="https://…" onChange={event => setUrl(event.target.value)} disabled={saving || conflict} /></>}<p className="help-text">地址跟随作者标签保存，绑定这个作者的作品可直接复用。</p>{!!current?.support_candidates?.length && <div className="support-candidates"><h3>历史资料存在不同支持地址</h3><p className="help-text">请明确选择，系统不会自动挑选。</p>{current.support_candidates.map(candidate => <div key={candidate}><code>{candidate}</code><button className="button small" disabled={saving || conflict} onClick={() => { setStatus('url'); setUrl(candidate); }}>使用此地址</button></div>)}</div>}</>}
    </div>
  </TagDialog>;
}

export function TagsPage({ revision, onChanged }: { revision: number; onChanged: () => void }) {
  const { catalog, error, refresh } = useTagCatalog(revision);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<TagCategory | 'all'>('all');
  const [editing, setEditing] = useState<Tag | null | undefined>(undefined);
  const items = (catalog?.items || []).filter(tag => (category === 'all' || tag.category === category) && tag.name.toLowerCase().includes(query.trim().toLowerCase()));
  const report = catalog?.import_report;
  const reportStats: [string, string][] = [['matched', '匹配库存'], ['skipped', '跳过条目'], ['created', '新建标签'], ['bindings', '绑定关系']];
  const reportIssues = report ? [...(Array.isArray(report.conflicts) ? report.conflicts : []), ...(Array.isArray(report.warnings) ? report.warnings : [])] as { message?: string; name?: string; values?: string[] }[] : [];
  return <>{report && <section className="tag-import-report"><h2>{report.dry_run ? '历史资料导入预览' : '最近一次历史资料导入'}</h2>{typeof report.imported_at === 'string' && <p className="help-text">{formatDate(report.imported_at)}</p>}<div className="tag-import-stats">{reportStats.filter(([key]) => typeof report[key] === 'number').map(([key, label]) => <span key={key}>{label}<strong>{String(report[key])}</strong></span>)}</div>{reportIssues.length > 0 && <details className="tag-import-conflicts"><summary>查看导入冲突与提示（{reportIssues.length} 项）</summary>{reportIssues.map((issue, index) => <div className="tag-import-issue" key={index}><p>{issue.message || '资料需要进一步确认'}</p>{issue.values?.map(value => <code key={value}>{value}</code>)}{issue.name && <button className="button small" onClick={() => { setQuery(issue.name!); setCategory('all'); }}>查看 {issue.name} 标签</button>}</div>)}</details>}</section>}<div className="tag-manager-toolbar"><label className="search-field"><Search size={18} /><span className="sr-only">搜索标签资料</span><input type="search" placeholder="搜索标签名称…" value={query} onChange={event => setQuery(event.target.value)} /></label><label className="tag-filter"><span className="sr-only">标签类别</span><select value={category} onChange={event => setCategory(event.target.value as TagCategory | 'all')}><option value="all">所有类别</option>{tagCategories.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><button className="button primary" onClick={() => setEditing(null)}><Plus size={16} />创建标签</button></div>{error && <div className="notice error" role="alert"><span>{error}</span><button className="button small" onClick={refresh}><RefreshCw size={14} />重试</button></div>}{!catalog && !error ? <div className="loading-state" role="status"><LoaderCircle size={18} className="spin" />正在读取标签资料</div> : <div className="tag-manager-list">{!items.length && <p className="tag-manager-empty">没有匹配的标签，可以创建新标签。</p>}{items.map(tag => <article className="tag-manager-row" key={tag.id}><div className="tag-manager-symbol"><TagIcon size={18} /></div><div className="tag-manager-info"><h2>{tag.name}<span className="badge neutral">{tagLabel(tag.category)}</span></h2><p>{tag.usage_count} 个库存使用{tag.category === 'author' && <> · {tag.support_status === 'unknown' && tag.support_candidates?.length ? '支持地址待确认' : tag.support_status === 'url' ? '已设置支持地址' : tag.support_status === 'none' ? '明确无支持地址' : '支持地址未填写'}</>}</p>{tag.category === 'author' && tag.support_status === 'url' && tag.support_url && <a href={safeLink(tag.support_url) || undefined} target="_blank" rel="noreferrer">{tag.support_url}</a>}</div><button className="button small" onClick={() => setEditing(tag)} aria-label={`编辑标签 ${tag.name}`}>编辑资料</button></article>)}</div>}{editing !== undefined && <CatalogEditor tag={editing} onClose={() => setEditing(undefined)} onSaved={() => { refresh(); onChanged(); }} />}</>;
}


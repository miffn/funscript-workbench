import { workIdentity } from './api';
import { useI18n, translate, tagName } from './i18n';
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Check, CircleAlert, LoaderCircle, Plus, RefreshCw, Search, Tag as TagIcon, X } from 'lucide-react';
import { ApiError, errorMessage, formatDate, request, safeLink } from './api';
import type { Tag, TagCatalog, TagCategory, Work, WorkTags } from './api';

export const tagCategories: [TagCategory, string][] = [['author', '作者'], ['video_type', '视频类型'], ['axis_type', '轴类型'], ['release_type', '发布类型'], ['tier', '档位'], ['duration', '时间'], ['custom', '自定义分类']];
const manualTagCategories = tagCategories.filter(([category]) => category !== 'duration');
export function tagLabel(category: TagCategory) { return translate(tagCategories.find(([key]) => key === category)?.[1] || category); }
export function tagCombinationError(tags: Tag[]) {
  const release = tags.find(tag => tag.category === 'release_type')?.name;
  const tier = tags.find(tag => tag.category === 'tier')?.name;
  if (!release || !tier) return '';
  if (release === 'Free Sample' && tier !== 'Free') return translate('Free Sample 应搭配 Free 档位，请调整发布类型或档位。');
  if (release === 'Paid' && tier === 'Free') return translate('Paid 应搭配 Main Tier 或 Extra Tier，请调整发布类型或档位。');
  return '';
}
export function TagChips({ tags = [], emptyLabel = translate('未标注'), durationStatus, durationError }: { tags?: Tag[]; emptyLabel?: string; durationStatus?: string; durationError?: string | null }) {
  useI18n();
  const ordered = tagCategories.flatMap(([category]) => tags.filter(tag => tag.category === category));
  const unknownDuration = !!durationStatus && !tags.some(tag => tag.category === 'duration');
  const durationLabel = durationStatus === 'partial' ? translate('读取不完整') : durationStatus === 'stale' ? translate('待更新') : translate('待读取');
  return ordered.length || unknownDuration ? <div className="tag-chips">{ordered.map(tag => <span className={`tag-chip ${tag.category}`} key={tag.id} title={translate('{category}：{name}{suffix}', { category: tagLabel(tag.category), name: tagName(tag), suffix: tag.category === 'duration' ? translate('（自动读取）') : '' })}><span className="tag-chip-category">{tagLabel(tag.category)}</span>{tagName(tag)}</span>)}{unknownDuration && <span className="tag-chip duration duration-unknown" title={durationError ? translate(durationError) : translate('扫描或重新匹配时自动读取视频时长')}><span className="tag-chip-category">{translate("时间")}</span>{durationLabel}</span>}</div> : <span className="tag-empty">{emptyLabel}</span>;
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
  useI18n();
  const dialog = useRef<HTMLDialogElement>(null);
  const keepButton = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const [confirm, setConfirm] = useState(false);
  const close = () => { if (saving) return; if (dirty) setConfirm(true); else onClose(); };
  useEffect(() => { dialog.current?.showModal(); const previous = document.body.style.overflow; document.body.style.overflow = 'hidden'; return () => { document.body.style.overflow = previous; }; }, []);
  useEffect(() => { if (confirm) keepButton.current?.focus(); }, [confirm]);
  return <dialog ref={dialog} className="tag-dialog" aria-labelledby="tag-dialog-title" onCancel={event => { event.preventDefault(); close(); }}><div className="detail-header"><div><span className="section-label">{subtitle}</span><h2 id="tag-dialog-title">{title}</h2></div><button ref={closeButton} autoFocus className="icon-button" aria-label={translate("关闭标签编辑")} onClick={close} disabled={saving}><X size={20} /></button></div>{confirm && <div className="discard-confirm" role="alert"><strong>{translate("标签修改尚未保存")}</strong><p>{translate("关闭会放弃本次修改。")}</p><div><button className="button small" ref={keepButton} onClick={() => { setConfirm(false); closeButton.current?.focus(); }}>{translate("继续编辑")}</button><button className="button small" onClick={onClose}>{translate("放弃标签修改并关闭")}</button></div></div>}<div className="tag-dialog-body">{children}</div><div className="detail-actions">{footer}</div></dialog>;
}

export function WorkTagEditor({ work, onClose, onSaved }: { work: Pick<Work, 'id' | 'script_id' | 'title' | 'duration_status' | 'duration_error'>; onClose: () => void; onSaved: (value: WorkTags) => void }) {
  useI18n();
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
    Promise.all([request<TagCatalog>('/api/tags', { signal: controller.signal }), request<WorkTags>(`/api/works/${work.id}/tags`, { signal: controller.signal })]).then(([all, current]) => { if (!controller.signal.aborted) { setCatalog(all.items); setBinding(current); setIds(current.tags.filter(tag => tag.category !== 'duration').map(tag => tag.id)); setError(''); setConflict(false); } }).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [work.id, retry]);
  const selected = catalog.filter(tag => ids.includes(tag.id));
  const dirty = !!binding && [...ids].sort((a, b) => a - b).join(',') !== binding.tags.filter(tag => tag.category !== 'duration').map(tag => tag.id).sort((a, b) => a - b).join(',');
  const combinationError = tagCombinationError(selected);
  const choose = (tag: Tag) => { if (saving || conflict || tag.category === 'duration') return; setError(''); setIds(previous => previous.includes(tag.id) ? previous.filter(id => id !== tag.id) : [...previous.filter(id => tag.category === 'custom' || catalog.find(item => item.id === id)?.category !== tag.category), tag.id]); };
  const save = async () => {
    if (lock.current || !binding || !dirty || combinationError || conflict) return;
    lock.current = true; setSaving(true); setError('');
    try { const result = await request<WorkTags>(`/api/works/${work.id}/tags`, { method: 'PUT', body: JSON.stringify({ tag_ids: ids.filter(id => catalog.find(tag => tag.id === id)?.category !== 'duration'), expected_revision: binding.tags_revision }) }); if (alive.current) { setBinding(result); setIds(result.tags.filter(tag => tag.category !== 'duration').map(tag => tag.id)); onSaved(result); onClose(); } }
    catch (error) { if (alive.current) { setError(error instanceof ApiError && error.status === 409 ? translate('标签已被其他客户端修改，请刷新最新标签后重新确认。') : translate('标签未保存：{error}', { error: errorMessage(error) })); setConflict(error instanceof ApiError && error.status === 409); } }
    finally { lock.current = false; if (alive.current) setSaving(false); }
  };
  return <TagDialog title={translate("编辑作品标签")} subtitle={workIdentity(work)} onClose={onClose} dirty={dirty} saving={saving} footer={<div><span className="help-text">{translate('{count} 个标签已选择', { count: selected.length })}</span><button className="button primary" onClick={() => void save()} disabled={!dirty || saving || loading || !!combinationError || conflict}>{saving ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />}{saving ? translate('正在保存') : translate('保存标签')}</button></div>}>
    <p className="tag-work-title">{work.title}</p><p className="help-text">{translate("作者和分类每类选择一个，自定义分类可多选。标签不会改变发布状态。")}</p>
    {error && <div className="notice error" role="alert"><span><CircleAlert size={16} />{translate(error)}</span>{(conflict || !binding) && <button className="button small" onClick={() => setRetry(value => value + 1)}>{translate("刷新并重新编辑")}</button>}</div>}
    {loading ? <div className="loading-state" role="status"><LoaderCircle size={18} className="spin" />{translate("正在读取标签")}</div> : binding && <><section className="duration-tag-readonly" aria-label={translate("自动时间标签")}><TagChips tags={binding.tags.filter(tag => tag.category === 'duration')} emptyLabel={translate("时间待读取")} durationStatus={work.duration_status} durationError={work.duration_error} /><p className="help-text">{translate("时间标签根据原视频总时长自动计算，扫描或重新匹配后更新，无需手动选择。")}</p></section><label className="search-field tag-search"><Search size={17} /><span className="sr-only">{translate("搜索作者或标签")}</span><input type="search" placeholder={translate("搜索作者、标签…")} value={query} onChange={event => setQuery(event.target.value)} /></label>{manualTagCategories.map(([category, label]) => { const options = catalog.filter(tag => tag.category === category && ([tag.name, tagName(tag)].some(name => name.toLowerCase().includes(query.trim().toLowerCase())) || ids.includes(tag.id))); const author = selected.find(tag => tag.category === 'author'); return <fieldset className="tag-choice-group" key={category} disabled={saving || conflict}><legend>{translate(label)}<small>{category === 'custom' ? translate('多选') : translate('单选，可取消')}</small></legend><div className="tag-options">{options.map(tag => <button type="button" className={`tag-option ${tag.category}${ids.includes(tag.id) ? ' selected' : ''}`} aria-pressed={ids.includes(tag.id)} onClick={() => choose(tag)} key={tag.id}>{ids.includes(tag.id) && <Check size={14} />}{tagName(tag)}</button>)}</div>{!options.length && <p className="help-text">{query ? translate('没有匹配的标签') : translate('暂无标签，可在标签管理中创建。')}</p>}{category === 'author' && author && <p className="help-text">{author.support_status === 'url' ? <a href={safeLink(author.support_url) || undefined} target="_blank" rel="noreferrer">{translate('支持 {name}', { name: author.name })}</a> : author.support_status === 'none' ? translate('该作者已明确无支持地址。') : author.support_candidates?.length ? translate('该作者存在支持地址冲突，请在标签管理中确认。') : translate('该作者支持地址尚未填写。')}</p>}</fieldset>; })}{combinationError && <p className="inline-error" role="alert">{combinationError}</p>}<p className="help-text">{translate("支持地址随作者标签复用；需要新增或修改标签，可前往“标签管理”。")}</p></>}
  </TagDialog>;
}

function CatalogEditor({ tag, onClose, onSaved }: { tag: Tag | null; onClose: () => void; onSaved: () => void }) {
  useI18n();
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
    try { const all = await request<TagCatalog>('/api/tags'); const latest = all.items.find(item => item.id === current.id); if (!latest) throw new Error(translate('此标签已不存在，请关闭后重新打开。')); if (alive.current) { setCurrent(latest); setCategory(latest.category); setName(latest.name); setStatus(latest.support_status); setUrl(latest.support_url || ''); setConflict(false); setError(''); } }
    catch (error) { if (alive.current) setError(errorMessage(error)); }
    finally { if (alive.current) setSaving(false); }
  };
  const save = async () => {
    if (lock.current || conflict || !name.trim() || category === 'duration') return;
    let supportUrl: string | null = null;
    if (category === 'author' && status === 'url') {
      try { const parsed = new URL(url.trim()); if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error(); supportUrl = parsed.href; }
      catch { setError(translate('请输入有效的 http(s) 支持地址，不包含用户名或密码。')); return; }
    }
    lock.current = true; setSaving(true); setError('');
    try { await request<Tag>(current ? `/api/tags/${current.id}` : '/api/tags', { method: current ? 'PATCH' : 'POST', body: JSON.stringify({ ...(current ? { expected_revision: current.revision } : { category }), name: name.trim(), ...(category === 'author' ? { support_status: status, support_url: supportUrl } : {}) }) }); if (alive.current) { onSaved(); onClose(); } }
    catch (error) { if (alive.current) { const stale = !!current && error instanceof ApiError && error.status === 409; setError(stale ? translate('该标签已被其他客户端修改，请刷新最新资料后重新编辑。') : translate('标签未保存：{error}', { error: errorMessage(error) })); setConflict(stale); } }
    finally { lock.current = false; if (alive.current) setSaving(false); }
  };
  const choices = category === 'release_type' ? ['Free Sample', 'Paid'] : category === 'tier' ? ['Free', 'Main Tier', 'Extra Tier'] : null;
  return <TagDialog title={current ? translate('编辑标签资料') : translate('创建标签')} subtitle={translate("标签管理")} onClose={onClose} dirty={dirty} saving={saving} footer={<div><span className="help-text">{current ? translate('绑定此标签的作品共享资料') : translate('标签保存在服务端')}</span><button className="button primary" onClick={() => void save()} disabled={saving || !name.trim() || conflict || !!current && !dirty}>{saving ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />}{saving ? translate('正在保存') : current ? translate('保存资料') : translate('创建标签')}</button></div>}>
    {error && <div className="notice error" role="alert"><span>{translate(error)}</span>{conflict && <button className="button small" onClick={() => void refresh()}>{translate("刷新并重新编辑")}</button>}</div>}
    <div className="tag-manage-form"><label htmlFor="tag-category">{translate("类别")}</label><select id="tag-category" value={category} disabled={!!current || saving || conflict} onChange={event => { const next = event.target.value as TagCategory; setCategory(next); setName(next === 'release_type' ? 'Free Sample' : next === 'tier' ? 'Free' : ''); setStatus('unknown'); setUrl(''); }}>{manualTagCategories.map(([key, label]) => <option value={key} key={key}>{translate(label)}</option>)}</select><label htmlFor="tag-name">{translate("名称")}</label>{choices ? <select id="tag-name" value={name} onChange={event => setName(event.target.value)} disabled={saving || conflict}>{!name && <option value="">{translate("请选择")}</option>}{choices.map(value => <option key={value}>{value}</option>)}</select> : <input id="tag-name" maxLength={200} value={name} onChange={event => setName(event.target.value)} disabled={saving || conflict} />}
      {category === 'author' && <><label htmlFor="tag-support-status">{translate("支持地址状态")}</label><select id="tag-support-status" value={status} onChange={event => setStatus(event.target.value as Tag['support_status'])} disabled={saving || conflict}><option value="unknown">{translate("未填写 / 待确认")}</option><option value="none">{translate("明确无支持地址")}</option><option value="url">{translate("已有支持地址")}</option></select>{status === 'url' && <><label htmlFor="tag-support-url">{translate("支持作者 URL")}</label><input id="tag-support-url" type="url" value={url} placeholder="https://…" onChange={event => setUrl(event.target.value)} disabled={saving || conflict} /></>}<p className="help-text">{translate("地址跟随作者标签保存，绑定这个作者的作品可直接复用。")}</p>{!!current?.support_candidates?.length && <div className="support-candidates"><h3>{translate("历史资料存在不同支持地址")}</h3><p className="help-text">{translate("请明确选择，系统不会自动挑选。")}</p>{current.support_candidates.map(candidate => <div key={candidate}><code>{candidate}</code><button className="button small" disabled={saving || conflict} onClick={() => { setStatus('url'); setUrl(candidate); }}>{translate("使用此地址")}</button></div>)}</div>}</>}
    </div>
  </TagDialog>;
}

export function TagsPage({ revision, onChanged }: { revision: number; onChanged: () => void }) {
  useI18n();
  const { catalog, error, refresh } = useTagCatalog(revision);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<TagCategory | 'all'>('all');
  const [editing, setEditing] = useState<Tag | null | undefined>(undefined);
  const items = (catalog?.items || []).filter(tag => (category === 'all' || tag.category === category) && [tag.name, tagName(tag)].some(name => name.toLowerCase().includes(query.trim().toLowerCase())));
  const report = catalog?.import_report;
  const reportStats: [string, string][] = [['matched', translate('匹配库存')], ['skipped', translate('跳过条目')], ['created', translate('新建标签')], ['bindings', translate('绑定关系')]];
  const reportIssues = report ? [...(Array.isArray(report.conflicts) ? report.conflicts : []), ...(Array.isArray(report.warnings) ? report.warnings : [])] as { message?: string; name?: string; values?: string[] }[] : [];
  return <>{report && <section className="tag-import-report"><h2>{report.dry_run ? translate('历史资料导入预览') : translate('最近一次历史资料导入')}</h2>{typeof report.imported_at === 'string' && <p className="help-text">{formatDate(report.imported_at)}</p>}<div className="tag-import-stats">{reportStats.filter(([key]) => typeof report[key] === 'number').map(([key, label]) => <span key={key}>{label}<strong>{String(report[key])}</strong></span>)}</div>{reportIssues.length > 0 && <details className="tag-import-conflicts"><summary>{translate('查看导入冲突与提示（{count} 项）', { count: reportIssues.length })}</summary>{reportIssues.map((issue, index) => <div className="tag-import-issue" key={index}><p>{issue.message ? translate(issue.message) : translate('资料需要进一步确认')}</p>{issue.values?.map(value => <code key={value}>{value}</code>)}{issue.name && <button className="button small" onClick={() => { setQuery(issue.name!); setCategory('all'); }}>{translate('查看 {name} 标签', { name: issue.name })}</button>}</div>)}</details>}</section>}<div className="tag-manager-toolbar"><label className="search-field"><Search size={18} /><span className="sr-only">{translate("搜索标签资料")}</span><input type="search" placeholder={translate("搜索标签名称…")} value={query} onChange={event => setQuery(event.target.value)} /></label><label className="tag-filter"><span className="sr-only">{translate("标签类别")}</span><select value={category} onChange={event => setCategory(event.target.value as TagCategory | 'all')}><option value="all">{translate("所有类别")}</option>{tagCategories.map(([key, label]) => <option key={key} value={key}>{translate(label)}</option>)}</select></label>{category !== 'duration' && <button className="button primary" onClick={() => setEditing(null)}><Plus size={16} />{translate("创建标签")}</button>}</div><p className="help-text duration-catalog-help">{translate("时间标签由原视频总时长自动生成，扫描或重新匹配时更新；此类别只读，不支持新建或手动修改。")}</p>{error && <div className="notice error" role="alert"><span>{translate(error)}</span><button className="button small" onClick={refresh}><RefreshCw size={14} />{translate("重试")}</button></div>}{!catalog && !error ? <div className="loading-state" role="status"><LoaderCircle size={18} className="spin" />{translate("正在读取标签资料")}</div> : <div className="tag-manager-list">{!items.length && <p className="tag-manager-empty">{category === 'duration' ? translate('暂无时间标签，扫描或重新匹配视频后自动生成。') : translate('没有匹配的标签，可以创建新标签。')}</p>}{items.map(tag => <article className="tag-manager-row" key={tag.id}><div className="tag-manager-symbol"><TagIcon size={18} /></div><div className="tag-manager-info"><h2>{tagName(tag)}<span className={`tag-chip ${tag.category}`}>{tagLabel(tag.category)}</span></h2><p>{translate('{count} 个库存使用', { count: tag.usage_count || 0 })}{tag.category === 'author' && <> · {tag.support_status === 'unknown' && tag.support_candidates?.length ? translate('支持地址待确认') : tag.support_status === 'url' ? translate('已设置支持地址') : tag.support_status === 'none' ? translate('明确无支持地址') : translate('支持地址未填写')}</>}</p>{tag.category === 'author' && tag.support_status === 'url' && tag.support_url && <a href={safeLink(tag.support_url) || undefined} target="_blank" rel="noreferrer">{tag.support_url}</a>}</div>{tag.category === 'duration' ? <span className="tag-chip duration duration-readonly-label">{translate("自动更新 · 只读")}</span> : <button className="button small" onClick={() => setEditing(tag)} aria-label={translate('编辑标签 {name}', { name: tagName(tag) })}>{translate("编辑资料")}</button>}</article>)}</div>}{editing !== undefined && <CatalogEditor tag={editing} onClose={() => setEditing(undefined)} onSaved={() => { refresh(); onChanged(); }} />}</>;
}


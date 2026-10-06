import { useDialogBackdropClose } from './useDialogBackdropClose';
import { workIdentity } from './api';
import { useI18n, translate, tagName } from './i18n';
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Check, CircleAlert, LoaderCircle, Plus, RefreshCw, Search, ChevronLeft, ChevronRight, Minus, Pencil, X } from 'lucide-react';
import { ApiError, errorMessage, formatDate, request, safeLink } from './api';
import type { Tag, TagCatalog, TagCategory, TagCategoryStyle, Work, WorkTags, Inventory } from './api';
import { TagAppearanceFields, TagCategoryStyleEditor, tagColorStyle, validTagColors } from './TagAppearance';
export { tagColorStyle } from './TagAppearance';
import './TagWorkbench.css';

export const tagCategories: [TagCategory, string][] = [['author', '作者'], ['video_type', '视频类型'], ['axis_type', '轴类型'], ['release_type', '发布类型'], ['tier', '档位'], ['duration', '时间'], ['custom', '自定义分类']];
export const manualTagCategories = tagCategories.filter(([category]) => category !== 'duration');
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
  // Duration remains automatic server metadata; the cover owns its visible display.
  void durationStatus; void durationError;
  const ordered = manualTagCategories.flatMap(([category]) => tags.filter(tag => tag.category === category));
  return ordered.length ? <div className="tag-chips tag-text-line">{ordered.map(tag => <span className={`tag-chip ${tag.category}`} style={tagColorStyle(tag)} key={tag.id} title={translate('{category}：{name}{suffix}', { category: tagLabel(tag.category), name: tagName(tag), suffix: '' })}>{tagName(tag)}</span>)}</div> : <span className="tag-empty">{emptyLabel}</span>;
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

export function TagDialog({ title, subtitle, children, footer, onClose, dirty, saving, className = '', closeLabel }: { className?: string; closeLabel?: string; title: string; subtitle: string; children: ReactNode; footer: ReactNode; onClose: () => void; dirty: boolean; saving: boolean }) {
  useI18n();
  const dialog = useRef<HTMLDialogElement>(null);
  const keepButton = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const [confirm, setConfirm] = useState(false);
  const close = () => { if (saving) return; if (dirty) setConfirm(true); else onClose(); };
  const backdrop = useDialogBackdropClose(close);
  useEffect(() => { dialog.current?.showModal(); const previous = document.body.style.overflow; document.body.style.overflow = 'hidden'; return () => { document.body.style.overflow = previous; }; }, []);
  useEffect(() => { if (confirm) keepButton.current?.focus(); }, [confirm]);
  return <dialog {...backdrop} ref={dialog} className={`tag-dialog ${className}`} aria-labelledby="tag-dialog-title" onCancel={event => { event.preventDefault(); close(); }}><div className="detail-header"><div><h2 id="tag-dialog-title">{title}</h2><p className="tag-dialog-subtitle">{subtitle}</p></div><button ref={closeButton} autoFocus className="icon-button" aria-label={closeLabel || translate("关闭标签编辑")} onClick={close} disabled={saving}><X size={20} /></button></div>{confirm && <div className="discard-confirm" role="alert"><strong>{translate("标签修改尚未保存")}</strong><p>{translate("关闭会放弃本次修改。")}</p><div><button className="button small" ref={keepButton} onClick={() => { setConfirm(false); closeButton.current?.focus(); }}>{translate("继续编辑")}</button><button className="button small" onClick={onClose}>{translate("放弃标签修改并关闭")}</button></div></div>}<div className="tag-dialog-body">{children}</div><div className="detail-actions">{footer}</div></dialog>;
}

export function WorkTagEditor({ work, onClose, onSaved }: { work: Pick<Work, 'id' | 'script_id' | 'title' | 'duration_status' | 'duration_error'>; onClose: () => void; onSaved: (value: WorkTags) => void }) {
  useI18n();
  const [catalog, setCatalog] = useState<Tag[]>([]);
  const [binding, setBinding] = useState<WorkTags | null>(null);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<TagCategory | 'all'>('all');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [conflict, setConflict] = useState(false);
  const [failedIds, setFailedIds] = useState<number[] | null>(null);
  const [undoIds, setUndoIds] = useState<number[] | null>(null);
  const failedUndo = useRef(false);
  const [retry, setRetry] = useState(0);
  const lock = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true);
    Promise.all([request<TagCatalog>('/api/tags', { signal: controller.signal }), request<WorkTags>(`/api/works/${work.id}/tags`, { signal: controller.signal })]).then(([all, current]) => { if (!controller.signal.aborted) { setCatalog(all.items); setBinding(current); setError(''); setConflict(false); setFailedIds(null); setUndoIds(null); setFeedback(''); } }).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [work.id, retry]);
  const selected = binding?.tags.filter(tag => tag.category !== 'duration') || [];
  const ids = selected.map(tag => tag.id);
  const options = catalog.filter(tag => tag.category !== 'duration' && !ids.includes(tag.id) && (category === 'all' || tag.category === category) && [tag.name, tagName(tag)].some(name => name.toLowerCase().includes(query.trim().toLowerCase())));
  const save = async (nextIds: number[], undoing = false) => {
    if (lock.current || !binding || conflict) return;
    const invalid = tagCombinationError(catalog.filter(tag => nextIds.includes(tag.id)));
    if (invalid) { setError(invalid); setFailedIds(null); setFeedback(''); return; }
    lock.current = true; setSaving(true); setError(''); setFeedback(''); setFailedIds(null);
    try {
      const result = await request<WorkTags>(`/api/works/${work.id}/tags`, { method: 'PUT', body: JSON.stringify({ tag_ids: nextIds.filter(id => catalog.find(tag => tag.id === id)?.category !== 'duration'), expected_revision: binding.tags_revision }) });
      if (alive.current) { setUndoIds(undoing ? null : ids); setBinding(result); onSaved(result); setFeedback(translate(undoing ? '已撤销标签修改' : '作品标签已保存')); }
    } catch (error) {
      if (alive.current) {
        const stale = error instanceof ApiError && error.status === 409;
        setError(stale ? translate('标签已被其他客户端修改，请刷新最新标签后重新确认。') : translate('标签未保存：{error}', { error: errorMessage(error) }));
        setConflict(stale); if (!stale) { setFailedIds(nextIds); failedUndo.current = undoing; }
      }
    } finally { lock.current = false; if (alive.current) setSaving(false); }
  };
  const choose = (tag: Tag) => {
    if (saving || conflict || loading || tag.category === 'duration') return;
    const next = ids.includes(tag.id) ? ids.filter(id => id !== tag.id) : [...ids.filter(id => tag.category === 'custom' || selected.find(item => item.id === id)?.category !== tag.category), tag.id];
    void save(next);
  };
  const author = selected.find(tag => tag.category === 'author');
  return <TagDialog className="tag-work-dialog" title={translate('编辑作品标签')} subtitle={work.title?.trim() && work.title.trim().toLowerCase() !== workIdentity(work).toLowerCase() ? `${workIdentity(work)} · ${work.title}` : workIdentity(work)} onClose={onClose} dirty={false} saving={saving} footer={<div className="tag-live-footer"><span className="help-text" role="status">{saving ? <><LoaderCircle size={15} className="spin" /> {translate('正在保存')}</> : feedback || translate('点击标签即保存')} · {translate('{count} 个标签', { count: selected.length })}</span><div>{undoIds && <button className="tag-clear-action" onClick={() => void save(undoIds, true)} disabled={saving || conflict}>{translate('撤销')}</button>}<button className="button primary" onClick={onClose} disabled={saving}>{translate('完成')}</button></div></div>}>

    {error && <div className="notice error" role="alert"><span><CircleAlert size={16} />{translate(error)}</span>{(conflict || !binding) ? <button className="button small" onClick={() => setRetry(value => value + 1)} disabled={loading}>{translate('刷新并重新编辑')}</button> : failedIds && <button className="button small" onClick={() => void save(failedIds, failedUndo.current)} disabled={saving}>{translate('重试保存')}</button>}</div>}
    {loading ? <div className="loading-state" role="status"><LoaderCircle size={18} className="spin" />{translate('正在读取标签')}</div> : binding && <div className="tag-transfer" aria-busy={saving}>
      <section className="tag-transfer-current" aria-label={translate('当前标签')}><div className="tag-transfer-heading"><strong>{translate('现有标签')}</strong><span>{translate('{count} 个标签', { count: selected.length })}</span></div><p className="tag-transfer-hint">{translate('点击移回标签库')}</p><div className="tag-current-groups">{manualTagCategories.flatMap(([key, label]) => selected.filter(tag => tag.category === key).map(tag => <button className={`tag-option tag-selected-row ${tag.category}`} style={tagColorStyle(tag)} aria-label={tagName(tag)} aria-pressed="true" key={tag.id} title={translate('移除 {name}', { name: tagName(tag) })} onClick={() => choose(tag)} disabled={saving || conflict}><span><small>{translate(label)}</small><span className="tag-value">{tagName(tag)}</span></span><Minus size={15} aria-hidden="true" /></button>))}{!selected.length && <p className="tag-library-empty">{translate('还没有标签，从右侧添加。')}</p>}</div>{author && <p className="help-text tag-author-support">{author.support_status === 'url' ? <a href={safeLink(author.support_url) || undefined} target="_blank" rel="noreferrer">{translate('支持 {name}', { name: author.name })}</a> : author.support_status === 'none' ? translate('该作者已明确无支持地址。') : author.support_candidates?.length ? translate('该作者存在支持地址冲突，请在标签管理中确认。') : translate('该作者支持地址尚未填写。')}</p>}</section>
      <section className="tag-transfer-library" aria-label={translate('标签库')}><div className="tag-transfer-heading"><strong>{translate('标签库')}</strong><span>{translate('{count} 个标签', { count: options.length })}</span></div><p className="tag-transfer-hint">{translate('点击添加到作品')}</p><label className="search-field tag-search"><Search size={17} /><span className="sr-only">{translate('搜索作者或标签')}</span><input type="search" placeholder={translate('搜索标签…')} value={query} onChange={event => { setQuery(event.target.value); }} /></label><nav className="tag-category-tabs" aria-label={translate('标签类别')}><button aria-pressed={category === 'all'} onClick={() => { setCategory('all'); }}>{translate('全部类别')}</button>{manualTagCategories.map(([key, label]) => <button key={key} aria-pressed={category === key} onClick={() => { setCategory(key); }}>{translate(label)}</button>)}</nav><div className="tag-library-options">{manualTagCategories.map(([key, label]) => { const optionsInPage = options.filter(tag => tag.category === key); return optionsInPage.length ? <section className="tag-library-group" key={key}><h3>{translate(label)}</h3><div className="tag-library-values">{optionsInPage.map(tag => <button type="button" className={`tag-option tag-library-value ${tag.category}`} style={tagColorStyle(tag)} aria-pressed="false" key={tag.id} title={translate('{category}：{name}{suffix}', { category: tagLabel(tag.category), name: tagName(tag), suffix: '' })} onClick={() => choose(tag)} disabled={saving || conflict}><Plus size={13} aria-hidden="true" /><span className="tag-value">{tagName(tag)}</span></button>)}</div></section> : null; })}{!options.length && <p className="tag-library-empty">{query ? translate('没有匹配的标签') : translate('本类标签都已添加，或还未创建。')}</p>}</div></section>    </div>}
  </TagDialog>;
}
function CatalogEditor({ tag, onClose, onSaved, inline = false, initialCategory = 'author', onOpenWork, onEditingChange, categoryStyles }: { categoryStyles?: TagCategoryStyle[]; tag: Tag | null; onClose: () => void; onSaved: () => void; inline?: boolean; initialCategory?: TagCategory; onOpenWork?: (id: number) => void; onEditingChange?: (value: boolean) => void }) {
  useI18n();
  const [current, setCurrent] = useState<Tag | null>(tag);
  const [category, setCategory] = useState<TagCategory>(tag?.category || initialCategory);
  const [name, setName] = useState(tag?.name || '');
  const [status, setStatus] = useState<Tag['support_status']>(tag?.support_status || 'unknown');
  const [url, setUrl] = useState(tag?.support_url || '');
  const [colorLight, setColorLight] = useState(tag?.color_light || '');
  const [colorDark, setColorDark] = useState(tag?.color_dark || '');
  const [bold, setBold] = useState<boolean | null>(tag?.bold ?? null);
  const colorsValid = validTagColors(colorLight, colorDark);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const lock = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const [feedback, setFeedback] = useState('');
  const colorsDirty = bold !== (current?.bold ?? null) || colorLight !== (current?.color_light || '') || colorDark !== (current?.color_dark || '');
  const dirty = colorsDirty || (current ? name !== current.name || status !== current.support_status || (status === 'url' && url !== (current.support_url || '')) : !!name.trim() || status !== 'unknown' || !!url);
  useEffect(() => { onEditingChange?.(dirty || saving); }, [dirty, saving, onEditingChange]);
  useEffect(() => () => { onEditingChange?.(false); }, [onEditingChange]);
  const refresh = async () => {
    if (!current || saving) return; setSaving(true);
    try { const all = await request<TagCatalog>('/api/tags'); const latest = all.items.find(item => item.id === current.id); if (!latest) throw new Error(translate('此标签已不存在，请关闭后重新打开。')); if (alive.current) { setCurrent(latest); setCategory(latest.category); setName(latest.name); setStatus(latest.support_status); setUrl(latest.support_url || ''); setColorLight(latest.color_light || ''); setColorDark(latest.color_dark || ''); setBold(latest.bold ?? null); setConflict(false); setError(''); } }
    catch (error) { if (alive.current) setError(errorMessage(error)); }
    finally { if (alive.current) setSaving(false); }
  };
  const save = async () => {
    if (lock.current || conflict || !name.trim() || !colorsValid || category === 'duration') return;
    let supportUrl: string | null = null;
    if (category === 'author' && status === 'url') {
      try { const parsed = new URL(url.trim()); if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error(); supportUrl = parsed.href; }
      catch { setError(translate('请输入有效的 http(s) 支持地址，不包含用户名或密码。')); return; }
    }
    lock.current = true; setSaving(true); setError('');
    try { const result = await request<Tag>(current ? `/api/tags/${current.id}` : '/api/tags', { method: current ? 'PATCH' : 'POST', body: JSON.stringify({ ...(current ? { expected_revision: current.revision } : { category }), name: name.trim(), ...(bold !== (current?.bold ?? null) ? { bold } : {}), ...(colorLight !== (current?.color_light || '') ? { color_light: colorLight || null } : {}), ...(colorDark !== (current?.color_dark || '') ? { color_dark: colorDark || null } : {}), ...(category === 'author' && (!current || status !== current.support_status || supportUrl !== current.support_url) ? { support_status: status, support_url: supportUrl } : {}) }) }); if (alive.current) { onSaved(); if (inline && current) { setCurrent(result); setFeedback(translate('标签资料已保存')); } else onClose(); } }
    catch (error) { if (alive.current) { const stale = !!current && error instanceof ApiError && error.status === 409; setError(stale ? translate('该标签已被其他客户端修改，请刷新最新资料后重新编辑。') : translate('标签未保存：{error}', { error: errorMessage(error) })); setConflict(stale); } }
    finally { lock.current = false; if (alive.current) setSaving(false); }
  };
  const choices = category === 'release_type' ? ['Free Sample', 'Paid'] : category === 'tier' ? ['Free', 'Main Tier', 'Extra Tier'] : null;
  const footer = <div className="tag-catalog-actions"><span className="help-text" role="status">{feedback || (current ? translate('{count} 个作品的标签会同步更新', { count: current.usage_count || 0 }) : translate('标签保存在服务端'))}</span><div><button className="button" onClick={onClose} disabled={saving}>{translate('取消')}</button><button className="button primary" onClick={() => void save()} disabled={saving || !name.trim() || !colorsValid || conflict || !!current && !dirty}>{saving ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />}{saving ? translate('正在保存') : current ? translate('保存资料') : translate('创建标签')}</button></div></div>;
  const form = <>
    {error && <div className="notice error" role="alert"><span>{translate(error)}</span>{conflict && <button className="button small" onClick={() => void refresh()}>{translate('刷新并重新编辑')}</button>}</div>}
    <div className="tag-catalog-edit-grid"><div className="tag-manage-form">
      {!current && <fieldset className="tag-inline-choices" disabled={saving || conflict}><legend>{translate('类别')}</legend>{manualTagCategories.map(([key, label]) => <button type="button" key={key} aria-pressed={category === key} onClick={() => { setCategory(key); setName(key === 'release_type' ? 'Free Sample' : key === 'tier' ? 'Free' : ''); setStatus('unknown'); setUrl(''); }}>{translate(label)}</button>)}</fieldset>}
      <label htmlFor="tag-name">{translate('名称')}</label>{choices ? <div className="tag-inline-choices" role="group" aria-label={translate('名称')}>{choices.map(value => <button type="button" key={value} aria-pressed={name === value} onClick={() => setName(value)} disabled={saving || conflict}>{value}</button>)}</div> : <input id="tag-name" maxLength={200} value={name} onChange={event => { setName(event.target.value); setFeedback(''); }} disabled={saving || conflict} />}
      <TagAppearanceFields category={category} name={name || tagLabel(category)} colorLight={colorLight} colorDark={colorDark} bold={bold} inherited={current?.category_style || categoryStyles?.find(style => style.category === category)} setColorLight={value => { setColorLight(value); setFeedback(''); }} setColorDark={value => { setColorDark(value); setFeedback(''); }} setBold={value => { setBold(value); setFeedback(''); }} disabled={saving || conflict} />
      {category === 'author' && <><fieldset className="tag-inline-choices" disabled={saving || conflict}><legend>{translate('支持地址状态')}</legend>{([['unknown', '未填写 / 待确认'], ['none', '明确无支持地址'], ['url', '已有支持地址']] as const).map(([value, label]) => <button type="button" key={value} aria-pressed={status === value} onClick={() => { setStatus(value); setFeedback(''); }}>{translate(label)}</button>)}</fieldset>{status === 'url' && <><label htmlFor="tag-support-url">{translate('支持作者 URL')}</label><input id="tag-support-url" type="url" value={url} placeholder="https://…" onChange={event => { setUrl(event.target.value); setFeedback(''); }} disabled={saving || conflict} /></>}<p className="help-text">{translate('地址跟随作者标签保存，绑定这个作者的作品可直接复用。')}</p>{!!current?.support_candidates?.length && <div className="support-candidates"><h3>{translate('历史资料存在不同支持地址')}</h3><p className="help-text">{translate('请明确选择，系统不会自动挑选。')}</p>{current.support_candidates.map(candidate => <div key={candidate}><code>{candidate}</code><button className="button small" disabled={saving || conflict} onClick={() => { setStatus('url'); setUrl(candidate); }}>{translate('使用此地址')}</button></div>)}</div>}</>}
    </div>{current && <RelatedTagWorks tag={current} onOpenWork={dirty || saving ? undefined : onOpenWork} />}</div>
  </>;
  return inline ? <section className="tag-catalog-editor"><header className="tag-surface-head"><h2>{current ? translate('编辑标签') : translate('创建标签')}</h2><span>{tagLabel(category)}</span></header><div className="tag-catalog-editor-body">{form}{footer}</div></section> : <TagDialog className="tag-catalog-dialog" title={current ? translate('编辑标签资料') : translate('创建标签')} subtitle={translate('标签管理')} onClose={onClose} dirty={dirty} saving={saving} footer={footer}>{form}</TagDialog>;
}

function RelatedTagWorks({ tag, onOpenWork }: { tag: Tag; onOpenWork?: (id: number) => void }) {
  const [data, setData] = useState<Inventory | null>(null);
  const [error, setError] = useState('');
  const [page, setPage] = useState(1);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setData(null); setError('');
    request<Inventory>(`/api/works?tag_id=${tag.id}&page=${page}&page_size=6`, { signal: controller.signal }).then(result => { if (!controller.signal.aborted) setData(result); }).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); });
    return () => controller.abort();
  }, [tag.id, tag.revision, page, retry]);
  return <section className="tag-related-works" aria-label={translate('关联作品')}><h3>{translate('关联作品')}{data && <span>{data.total}</span>}</h3>{error ? <div className="notice error" role="alert"><span>{error}</span><button className="button small" onClick={() => setRetry(value => value + 1)}>{translate('重试')}</button></div> : !data ? <p className="help-text" role="status">{translate('正在读取关联作品')}</p> : !data.items.length ? <p className="help-text">{translate('暂未关联作品')}</p> : <>{data.items.map(work => { const content = <><span className="tag-related-cover">{work.cover_url && <img src={work.cover_url} alt="" loading="lazy" />}</span><span><strong>{workIdentity(work)} · {work.title}</strong><small>{translate('{count} 视频', { count: work.video_count })} · {translate('{count} 脚本', { count: work.script_count })}</small></span></>; return onOpenWork ? <button className="tag-related-work" key={work.id} onClick={() => onOpenWork(work.id)}>{content}</button> : <div className="tag-related-work" key={work.id}>{content}</div>; })}{data.total > 6 && <div className="tag-library-pagination"><span>{page} / {Math.ceil(data.total / 6)}</span><div><button className="icon-button" disabled={page === 1} aria-label={translate('关联作品上一页')} onClick={() => setPage(value => value - 1)}><ChevronLeft size={16} /></button><button className="icon-button" disabled={page * 6 >= data.total} aria-label={translate('关联作品下一页')} onClick={() => setPage(value => value + 1)}><ChevronRight size={16} /></button></div></div>}</>}
  </section>;
}

export function TagsPage({ revision, onChanged, onOpenWork }: { revision: number; onChanged: () => void; onOpenWork?: (id: number) => void }) {
  useI18n();
  const { catalog, error, refresh } = useTagCatalog(revision);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<TagCategory | 'all'>('author');
  const [editing, setEditing] = useState<Tag | null | undefined>(undefined);
  const [blocked, setBlocked] = useState(false);
  const [styleEditing, setStyleEditing] = useState(false);
  const editTag = (tag: Tag | null) => { setStyleEditing(false); setEditing(tag); };
  const [page, setPage] = useState(0);
  const items = (catalog?.items || []).filter(tag => (category === 'all' || tag.category === category) && [tag.name, tagName(tag)].some(name => name.toLowerCase().includes(query.trim().toLowerCase())));
  const totalPages = Math.max(1, Math.ceil(items.length / 24));
  const currentPage = Math.min(page, totalPages - 1);
  const report = catalog?.import_report;
  const reportStats: [string, string][] = [['matched', translate('匹配库存')], ['skipped', translate('跳过条目')], ['created', translate('新建标签')], ['bindings', translate('绑定关系')]];
  const reportIssues = report ? [...(Array.isArray(report.conflicts) ? report.conflicts : []), ...(Array.isArray(report.warnings) ? report.warnings : [])] as { message?: string; name?: string; values?: string[] }[] : [];
  const changeCategory = (next: TagCategory | 'all') => { setCategory(next); setStyleEditing(false); setEditing(undefined); setPage(0); };
  return <div className="tag-management-page">
    {report && <details className="tag-import-report"><summary>{report.dry_run ? translate('历史资料导入预览') : translate('最近一次历史资料导入')}</summary>{typeof report.imported_at === 'string' && <p className="help-text">{formatDate(report.imported_at)}</p>}<div className="tag-import-stats">{reportStats.filter(([key]) => typeof report[key] === 'number').map(([key, label]) => <span key={key}>{label}<strong>{String(report[key])}</strong></span>)}</div>{reportIssues.length > 0 && <div className="tag-import-conflicts">{reportIssues.map((issue, index) => <div className="tag-import-issue" key={index}><p>{issue.message ? translate(issue.message) : translate('资料需要进一步确认')}</p>{issue.values?.map(value => <code key={value}>{value}</code>)}{issue.name && <button className="button small" disabled={blocked} onClick={() => { setQuery(issue.name!); changeCategory('all'); }}>{translate('查看 {name} 标签', { name: issue.name })}</button>}</div>)}</div>}</details>}
    {error && <div className="notice error" role="alert"><span>{translate(error)}</span><button className="button small" onClick={refresh}><RefreshCw size={14} />{translate('重试')}</button></div>}
    <div className="tag-manager-layout"><nav className="tag-manager-categories" aria-label={translate('标签类别')}><button aria-pressed={category === 'all'} disabled={blocked} onClick={() => changeCategory('all')}><span>{translate('所有类别')}</span><small>{catalog?.items.length || 0}</small></button>{tagCategories.map(([key, label]) => <button key={key} aria-pressed={category === key} aria-label={`${translate(label)} ${catalog?.items.filter(tag => tag.category === key).length || 0}`} disabled={blocked} onClick={() => changeCategory(key)}><span>{translate(label)}</span><small>{catalog?.items.filter(tag => tag.category === key).length || 0}</small></button>)}</nav>
      <div className="tag-manager-main"><label className="search-field tag-manager-search"><Search size={17} /><span className="sr-only">{translate('搜索标签资料')}</span><input type="search" placeholder={translate('搜索标签名称…')} value={query} onChange={event => { setQuery(event.target.value); setPage(0); }} /></label>{styleEditing && category !== 'all' && catalog && <TagCategoryStyleEditor key={category} initial={catalog.category_styles?.find(style => style.category === category) || { category, color_light: null, color_dark: null, bold: null, revision: 0 }} name={tagLabel(category)} onEditingChange={setBlocked} onClose={() => { setStyleEditing(false); setBlocked(false); }} onSaved={() => { refresh(); onChanged(); }} />}<section className="tag-manager-table"><header className="tag-surface-head"><h2>{category === 'all' ? translate('所有类别') : tagLabel(category)}</h2><div className="tag-manager-actions">{category !== 'all' && <button className="tag-clear-action" disabled={blocked} onClick={() => { setEditing(undefined); setStyleEditing(true); }}>{translate('编辑类型样式')}</button>}{category !== 'duration' && <button className="tag-clear-action" disabled={blocked} onClick={() => editTag(null)}><Plus size={15} />{translate('创建标签')}</button>}</div></header><div className="tag-table-head"><span>{translate('标签名称')}</span><span>{translate('作品数')}</span><span /></div>
        <div className="tag-table-rows">{!catalog && !error ? <div className="loading-state" role="status"><LoaderCircle size={18} className="spin" />{translate('正在读取标签资料')}</div> : !items.length ? <p className="tag-library-empty">{category === 'duration' ? translate('暂无时间标签，扫描或重新匹配视频后自动生成。') : translate('没有匹配的标签，可以创建新标签。')}</p> : items.slice(currentPage * 24, (currentPage + 1) * 24).map(tag => <div className="tag-table-row" key={tag.id} data-selected={editing?.id === tag.id}><button className={`tag-table-name ${tag.category}`} style={tagColorStyle(tag)} disabled={blocked} onClick={() => editTag(tag)} aria-label={translate('选择标签 {name}', { name: tagName(tag) })}><span className="tag-value">{tagName(tag)}</span>{category === 'all' && <small>{tagLabel(tag.category)}</small>}</button><span className="tag-table-count">{tag.usage_count || 0}</span>{tag.category === 'duration' ? <span className="tag-readonly-status">{translate('只读')}</span> : <button className="tag-clear-action" disabled={blocked} onClick={() => editTag(tag)} aria-label={translate('编辑标签 {name}', { name: tagName(tag) })}>{translate('编辑')}<Pencil size={13} /></button>}</div>)}</div>
        {totalPages > 1 && <div className="tag-library-pagination"><span>{currentPage + 1} / {totalPages}</span><div><button className="icon-button" disabled={!currentPage} onClick={() => setPage(currentPage - 1)} aria-label={translate('标签管理上一页')}><ChevronLeft size={16} /></button><button className="icon-button" disabled={currentPage + 1 === totalPages} onClick={() => setPage(currentPage + 1)} aria-label={translate('标签管理下一页')}><ChevronRight size={16} /></button></div></div>}
      </section>{category === 'duration' && <p className="help-text duration-catalog-help">{translate('时间标签由原视频总时长自动生成，扫描或重新匹配时更新；此类别只读，不支持新建或手动修改。')}</p>}

      {editing !== undefined && (editing?.category === 'duration' ? <section className="tag-catalog-editor"><header className="tag-surface-head"><h2>{tagName(editing)}</h2><span>{translate('自动更新 · 只读')}</span></header><div className="tag-catalog-editor-body"><RelatedTagWorks key={editing.id} tag={editing} onOpenWork={onOpenWork} /></div></section> : <CatalogEditor key={editing?.id ?? 'new'} inline tag={editing} categoryStyles={catalog?.category_styles} initialCategory={category === 'all' || category === 'duration' ? 'author' : category} onEditingChange={setBlocked} onOpenWork={onOpenWork} onClose={() => { setEditing(undefined); setBlocked(false); }} onSaved={() => { refresh(); onChanged(); }} />)}
      </div>
    </div>
  </div>;
}

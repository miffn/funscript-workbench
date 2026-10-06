import { useEffect, useId, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { CalendarDays, Check, CircleDollarSign, Copy, FileDown, LoaderCircle, MessagesSquare, Pencil, Video, X } from 'lucide-react';
import { ApiError, errorMessage, request, safeLink, workIdentity } from './api';
import type { Work, WorkLinkKind, WorkLinks, WorkLinkValues } from './api';
import { useI18n, translate } from './i18n';
import './ReleaseWorkbench.css';
import { useWorkspaceTimezone } from './WorkspaceTimezone';

export const linkTypes = [
  { kind: 'patreon', label: 'Patreon 文章链接', short: 'Patreon', Icon: CircleDollarSign },
  { kind: 'video', label: '视频链接', short: '视频', Icon: Video },
  { kind: 'script', label: '脚本链接', short: '脚本', Icon: FileDown },
  { kind: 'es', label: 'ES 帖子链接', short: 'ES', Icon: MessagesSquare },
] as const;
const emptyLinks: WorkLinkValues = { patreon: '', video: '', script: '', es: '' };
const publicationFields = ['es_published', 'patreon_published', 'es_published_date', 'patreon_published_date', 'es_planned_date', 'patreon_planned_date'] as const;
type PublicationField = typeof publicationFields[number];
type PublicationDraft = Record<PublicationField, boolean | string>;
function publicationDraft(value: WorkLinks): PublicationDraft {
  return Object.fromEntries(publicationFields.map(field => [field, field.endsWith('_published') ? !!value[field] : value[field] || ''])) as PublicationDraft;
}
type ReleaseWork = Pick<Work, 'id' | 'script_id'> & Partial<Pick<Work, 'title'>>;
interface ReleaseProps {
  work: ReleaseWork; onSaved: (value: WorkLinks) => void;
  onDirtyChange?: (dirty: boolean) => void; onBusyChange?: (busy: boolean) => void;
}

export function WorkLinkButtons({ work, onEdit }: { work: Work; onEdit: (kind: WorkLinkKind) => void }) {
  useI18n();
  return <div className="work-link-buttons" role="group" aria-label={translate('{id} 的发布链接', { id: workIdentity(work) })}>
    {linkTypes.map(({ kind, label, short, Icon }) => {
      const filled = !!work.links?.[kind];
      return <button key={kind} type="button" className={`work-link-button ${filled ? 'filled' : 'empty'} ${kind}`}
        aria-label={translate('{action} {label} {id}', { action: filled ? translate('编辑') : translate('填写'), label: translate(label), id: workIdentity(work) })} title={`${translate(short)} · ${filled ? translate('已填写，点击编辑') : translate('未填写，点击添加')}`}
        onClick={() => onEdit(kind)}><Icon size={17} aria-hidden="true" /><span>{translate(short)}</span>{filled && <Check className="link-filled-mark" size={10} aria-hidden="true" />}</button>;
    })}
  </div>;
}

/** One release surface for the detail tab and the inventory's quick editor. */
export function WorkReleasePanel(props: ReleaseProps) { return <ReleaseEditor {...props} />; }
export function WorkLinkEditor(props: ReleaseProps & { initialKind: WorkLinkKind; onClose: () => void }) {
  return <ReleaseEditor {...props} modal />;
}

function ReleaseEditor({ work, initialKind, onClose, onSaved, onDirtyChange, onBusyChange, modal = false }: ReleaseProps & {
  initialKind?: WorkLinkKind; onClose?: () => void; modal?: boolean;
}) {
  useI18n();
  const instance = useId();
  const { timezone } = useWorkspaceTimezone();
  const identity = workIdentity(work), subtitle = work.title?.trim();
  const dialog = useRef<HTMLDialogElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const keepEditing = useRef<HTMLButtonElement>(null);
  const pendingFocus = useRef<string | null>(null);
  const alive = useRef(true), lock = useRef(false), copyLock = useRef(false);
  const [current, setCurrent] = useState<WorkLinks | null>(null);
  const [draft, setDraft] = useState<WorkLinkValues>(emptyLinks);
  const [publication, setPublication] = useState<PublicationDraft | null>(null);
  const [editors, setEditors] = useState<string[]>(initialKind ? [`link-${initialKind}`] : []);
  const [loading, setLoading] = useState(true), [saving, setSaving] = useState(false);
  const [error, setError] = useState(''), [conflict, setConflict] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false), [retry, setRetry] = useState(0);
  const [copyFeedback, setCopyFeedback] = useState<{ kind: WorkLinkKind; status: 'copying' | 'copied' | 'failed' } | null>(null);
  const changed = linkTypes.filter(({ kind }) => draft[kind].trim() !== current?.links[kind]);
  const publicationChanged = current && publication ? publicationFields.filter(field => publication[field] !== publicationDraft(current)[field]) : [];
  const dirty = !!current && (changed.length > 0 || publicationChanged.length > 0);
  const disabled = saving || conflict;
  const close = () => { if (saving) return; if (dirty) setConfirmClose(true); else onClose?.(); };
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { onBusyChange?.(saving); }, [saving, onBusyChange]);
  useEffect(() => {
    alive.current = true;
    if (!modal) return () => { alive.current = false; };
    dialog.current?.showModal();
    const previous = document.body.style.overflow; document.body.style.overflow = 'hidden';
    return () => { alive.current = false; document.body.style.overflow = previous; };
  }, [modal]);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true);
    request<WorkLinks>(`/api/works/${work.id}/links`, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) { setCurrent(value); setDraft(value.links); setPublication(publicationDraft(value)); setError(''); setConflict(false); setCopyFeedback(null); }
    }).catch(error => { if (!controller.signal.aborted) setError(translate('无法读取链接：{error}', { error: errorMessage(error) })); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [work.id, retry]);
  useEffect(() => { if (!loading && current && initialKind) content.current?.querySelector<HTMLInputElement>(`[data-link-input="${initialKind}"]`)?.focus(); }, [loading, current, initialKind]);
  useEffect(() => { if (confirmClose) keepEditing.current?.focus(); }, [confirmClose]);
  useEffect(() => {
    if (!pendingFocus.current) return;
    content.current?.querySelector<HTMLInputElement>(`[data-editor="${pendingFocus.current}"] input`)?.focus();
    pendingFocus.current = null;
  }, [editors]);
  const openEditor = (key: string) => {
    const existing = content.current?.querySelector<HTMLInputElement>(`[data-editor="${key}"] input`);
    if (existing) { existing.focus(); return; }
    pendingFocus.current = key;
    setEditors(previous => previous.includes(key) ? previous : [...previous, key]);
  };
  const finishEditor = (key: string) => setEditors(previous => previous.filter(value => value !== key));
  const reset = () => {
    if (!current) return;
    setDraft(current.links); setPublication(publicationDraft(current)); setEditors([]); setError('');
  };
  const copySavedLink = async (kind: WorkLinkKind) => {
    const value = current?.links[kind];
    if (!value || copyLock.current) return;
    copyLock.current = true; setCopyFeedback({ kind, status: 'copying' });
    try {
      let copied = false;
      if (navigator.clipboard?.writeText) {
        try { await navigator.clipboard.writeText(value); copied = true; } catch { /* Fall back for the local HTTP gateway. */ }
      }
      if (!copied) {
        if (!alive.current || !content.current) return;
        const previous = document.activeElement as HTMLElement | null;
        const text = document.createElement('textarea');
        text.value = value; text.readOnly = true; text.style.cssText = 'position:fixed;left:0;top:0;opacity:0;pointer-events:none';
        content.current.appendChild(text);
        try { text.focus({ preventScroll: true }); text.select(); copied = document.execCommand('copy'); }
        finally { text.remove(); previous?.focus({ preventScroll: true }); }
      }
      if (!copied) throw new Error('Copy was denied');
      if (alive.current) setCopyFeedback({ kind, status: 'copied' });
    } catch { if (alive.current) setCopyFeedback({ kind, status: 'failed' }); }
    finally { copyLock.current = false; }
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!current || !dirty || lock.current || conflict) return;
    const edits: Partial<WorkLinkValues> = {};
    for (const { kind, label } of changed) {
      const value = draft[kind].trim();
      if (value) {
        try {
          const parsed = new URL(value);
          if (!safeLink(value) || !parsed.hostname || parsed.username || parsed.password || /[\x00-\x20\x7f]/.test(value)) throw new Error();
        } catch { setError(translate('{label}应为有效的 http(s) 地址，不包含用户名、密码或空白字符。', { label: translate(label) })); openEditor(`link-${kind}`); return; }
      }
      edits[kind] = value;
    }
    lock.current = true; setSaving(true); setError('');
    try {
      const fields = Object.fromEntries(publicationChanged.map(field => [field, publication![field] === '' ? null : publication![field]]));
      const updated = await request<WorkLinks>(`/api/works/${work.id}/links`, { method: 'PATCH', body: JSON.stringify({ links: edits, expected_revision: current.links_revision,
        ...(current.publication_revision ? { expected_publication_revision: current.publication_revision } : {}), ...fields }) });
      if (alive.current) {
        setCurrent(updated); setDraft(updated.links); setPublication(publicationDraft(updated)); setEditors([]);
        onDirtyChange?.(false); onSaved(updated); if (modal) onClose?.();
      }
    } catch (error) {
      if (alive.current) {
        const stale = error instanceof ApiError && error.status === 409;
        setConflict(stale); setError(stale ? translate('链接已被其他页面更新，请读取最新链接后重新编辑。当前输入仍保留。') : translate('链接未保存：{error}', { error: errorMessage(error) }));
      }
    } finally { lock.current = false; if (alive.current) setSaving(false); }
  };
  function dateRow(platform: 'es' | 'patreon', planned: boolean) {
    const name = platform === 'es' ? 'ES' : 'Patreon', label = planned ? '计划日期' : '发布日期';
    const field: PublicationField = `${platform}_${planned ? 'planned' : 'published'}_date`;
    const key = `date-${field}`, value = String(publication?.[field] || '');
    return <div className="release-value release-date-value"><div><span className="release-value-label">{translate(label)}</span>
      <button type="button" className="release-inline-value" aria-label={translate('编辑 {platform} {label}', { platform: name, label: translate(label) })} disabled={disabled || (planned && !current?.publication_revision)} onClick={() => openEditor(key)}>{value || translate('添加{label}', { label: translate(label) })}</button>
      {editors.includes(key) && <div className="release-inline-editor" data-editor={key}><label htmlFor={`${instance}-${field}`}>{translate(label)}<input id={`${instance}-${field}`} aria-label={`${name} ${translate(label)}`} type="date" min="1900-01-01" max="9999-12-31" disabled={disabled} value={value} onChange={event => setPublication(previous => ({ ...previous!, [field]: event.target.value }))} /></label>
        <div className="release-inline-actions"><button type="button" className="button small" disabled={disabled} onClick={() => finishEditor(key)}>{translate('完成')}</button><button type="button" className="release-text-button" disabled={disabled} onClick={() => { setPublication(previous => ({ ...previous!, [field]: publicationDraft(current!)[field] })); finishEditor(key); }}>{translate('取消')}</button></div>
      </div>}
    </div><button type="button" className="icon-button" title={translate('编辑日期')} aria-label={translate('修改 {platform} {label}', { platform: name, label: translate(label) })} disabled={disabled || (planned && !current?.publication_revision)} onClick={() => openEditor(key)}><CalendarDays size={15} /></button></div>;
  }
  function linkRow(kind: WorkLinkKind) {
    const item = linkTypes.find(item => item.kind === kind)!;
    const value = current?.links[kind] || '', key = `link-${kind}`;
    return <div className="release-value release-link-value" key={kind}><div><span className="release-value-label">{translate(item.label)}</span>
      {safeLink(value) ? <a className="release-link-address" href={safeLink(value)!} target="_blank" rel="noreferrer">{value}</a> : <button type="button" className="release-inline-value" disabled={disabled} onClick={() => openEditor(key)}>{translate('添加{label}', { label: translate(item.label) })}</button>}
      {changed.some(item => item.kind === kind) && !editors.includes(key) && <span className="release-pending-note">{translate('有未保存的修改')}</span>}
      {editors.includes(key) && <div className="release-inline-editor" data-editor={key}><label htmlFor={`${instance}-link-${kind}`}>{translate(item.label)}<input data-link-input={kind} id={`${instance}-link-${kind}`} type="url" inputMode="url" autoComplete="off" spellCheck={false} maxLength={4000} placeholder="https://…" value={draft[kind]} disabled={disabled} onChange={event => { setDraft(previous => ({ ...previous, [kind]: event.target.value })); setError(''); }} /></label>
        <div className="release-inline-actions"><button type="button" className="button small" disabled={disabled} onClick={() => finishEditor(key)}>{translate('完成')}</button><button type="button" className="release-text-button" disabled={disabled} onClick={() => { setDraft(previous => ({ ...previous, [kind]: value })); finishEditor(key); }}>{translate('取消')}</button></div>
      </div>}
    </div><div className="release-link-actions"><button type="button" className="icon-button" disabled={!safeLink(value)} aria-label={translate('复制已保存的{label}', { label: translate(item.label) })} title={translate('复制链接')} aria-busy={copyFeedback?.kind === kind && copyFeedback.status === 'copying'} onClick={() => void copySavedLink(kind)}>{copyFeedback?.kind === kind && copyFeedback.status === 'copied' ? <Check size={15} /> : <Copy size={15} />}</button><button type="button" className="icon-button" disabled={disabled} aria-label={translate('编辑{label}', { label: translate(item.label) })} title={translate('修改链接')} onClick={() => openEditor(key)}><Pencil size={15} /></button></div>
      {copyFeedback?.kind === kind && <span className={copyFeedback.status === 'failed' ? 'inline-error release-copy-feedback' : 'release-copy-feedback'} role={copyFeedback.status === 'failed' ? 'alert' : 'status'}>{copyFeedback.status === 'copied' ? translate('已复制') : copyFeedback.status === 'failed' ? translate('复制失败，请手动复制输入框中的链接。') : translate('正在复制…')}</span>}
    </div>;
  }
  const body = <div className="release-editor-content" ref={content}>
    {confirmClose && <div className="discard-confirm" role="alert"><strong>{translate('链接修改尚未保存')}</strong><p>{translate('关闭会放弃本次输入。')}</p><div><button ref={keepEditing} className="button small" onClick={() => setConfirmClose(false)}>{translate('继续编辑链接')}</button><button className="button small" onClick={onClose}>{translate('放弃链接修改并关闭')}</button></div></div>}
    <p className="release-help">{translate('添加平台链接会自动记录已发布和当天日期')}</p>
    {error && <div className="notice error" role="alert"><span>{translate(error)}</span>{(conflict || !current) && <button className="button small" disabled={loading} onClick={() => setRetry(value => value + 1)}>{conflict ? translate('放弃输入并读取最新链接') : translate('重试读取链接')}</button>}</div>}
    {loading ? <p className="release-loading" role="status"><LoaderCircle className="spin" size={18} />{translate('正在读取发布链接')}</p> : current && publication && <form className="work-release-form" onSubmit={event => void save(event)} noValidate>
      <div className="release-platform-grid">{(['patreon', 'es'] as const).map(platform => {
        const name = platform === 'es' ? 'ES' : 'Patreon', published = !!publication[`${platform}_published`];
        return <section className="release-platform-panel" key={platform} aria-label={`${name} ${translate('发布信息')}`}>
          <header className="release-surface-head"><button type="button" className={`release-status-switch ${published ? 'published' : 'pending'}`} title={`${name} · ${translate(published ? '已发布' : '待发布')}`} aria-pressed={published} aria-label={translate('切换 {platform} 发布状态', { platform: name })} disabled={disabled || !current.publication_revision} onClick={() => setPublication(previous => ({ ...previous!, [`${platform}_published`]: !published }))}>{name}</button><span>{translate('点击切换状态')}</span></header>
          <div className="release-date-pair">{dateRow(platform, true)}{dateRow(platform, false)}</div>{linkRow(platform)}
        </section>;
      })}<section className="release-download-panel"><header className="release-surface-head"><h3>{translate('下载与播放链接')}</h3></header>{linkRow('video')}{linkRow('script')}</section></div>
      <footer className="release-save-bar"><span className="release-save-hint">{dirty ? translate('修改尚未保存') : translate('已保存的发布信息')} · {timezone}</span><div><button type="button" className="button" disabled={saving || !dirty} onClick={modal ? close : reset}>{translate(modal ? '取消' : '重置修改')}</button><button type="submit" className="button primary" disabled={loading || saving || !dirty || conflict}>{saving ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}{saving ? translate('正在保存') : translate('保存链接')}</button></div></footer>
    </form>}
  </div>;
  return modal ? <dialog ref={dialog} className="tag-dialog work-links-dialog" aria-labelledby={`${instance}-title`} onCancel={event => { event.preventDefault(); close(); }}>
    <header className="release-modal-head"><div><h2 id={`${instance}-title`}>{translate('快速编辑发布信息')}</h2><p>{identity}{subtitle && subtitle.toLocaleLowerCase() !== identity.toLocaleLowerCase() ? ` · ${subtitle}` : ''}</p></div><button type="button" className="icon-button" aria-label={translate('关闭链接编辑')} onClick={close} disabled={saving}><X size={20} /></button></header>{body}
  </dialog> : <div className="work-release-panel">{body}</div>;
}

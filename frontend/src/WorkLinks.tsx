import { useI18n, translate } from './i18n';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Check, CircleDollarSign, Copy, ExternalLink, FileDown, LoaderCircle, MessagesSquare, Video, X } from 'lucide-react';
import { ApiError, errorMessage, request, safeLink } from './api';
import type { Work, WorkLinkKind, WorkLinks, WorkLinkValues } from './api';

export const linkTypes = [
  { kind: 'patreon', label: 'Patreon 文章链接', short: 'Patreon', Icon: CircleDollarSign },
  { kind: 'video', label: '视频链接', short: '视频', Icon: Video },
  { kind: 'script', label: '脚本链接', short: '脚本', Icon: FileDown },
  { kind: 'es', label: 'ES 帖子链接', short: 'ES', Icon: MessagesSquare },
] as const;
const emptyLinks: WorkLinkValues = { patreon: '', video: '', script: '', es: '' };

export function WorkLinkButtons({ work, onEdit }: { work: Work; onEdit: (kind: WorkLinkKind) => void }) {
  useI18n();
  return <div className="work-link-buttons" role="group" aria-label={translate('{id} 的发布链接', { id: work.script_id })}>
    {linkTypes.map(({ kind, label, short, Icon }) => {
      const filled = !!work.links?.[kind];
      return <button key={kind} type="button" className={`work-link-button ${filled ? 'filled' : 'empty'} ${kind}`}
        aria-label={translate('{action} {label} {id}', { action: filled ? translate('编辑') : translate('填写'), label: translate(label), id: work.script_id })} title={`${translate(short)} · ${filled ? translate('已填写，点击编辑') : translate('未填写，点击添加')}`}
        onClick={() => onEdit(kind)}><Icon size={17} aria-hidden="true" /><span>{translate(short)}</span>{filled && <Check className="link-filled-mark" size={10} aria-hidden="true" />}</button>;
    })}
  </div>;
}

export function WorkLinkEditor({ work, initialKind, onClose, onSaved }: {
  work: Pick<Work, 'id' | 'script_id'>; initialKind: WorkLinkKind; onClose: () => void; onSaved: (value: WorkLinks) => void;
}) {
  useI18n();
  const dialog = useRef<HTMLDialogElement>(null);
  const initialInput = useRef<HTMLInputElement>(null);
  const keepEditing = useRef<HTMLButtonElement>(null);
  const alive = useRef(true);
  const lock = useRef(false);
  const copyLock = useRef(false);
  const [current, setCurrent] = useState<WorkLinks | null>(null);
  const [draft, setDraft] = useState<WorkLinkValues>(emptyLinks);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [retry, setRetry] = useState(0);
  const [copyFeedback, setCopyFeedback] = useState<{ kind: WorkLinkKind; status: 'copying' | 'copied' | 'failed' } | null>(null);
  const changed = linkTypes.filter(({ kind }) => draft[kind].trim() !== current?.links[kind]);
  const dirty = !!current && changed.length > 0;
  const close = () => { if (saving) return; if (dirty) setConfirmClose(true); else onClose(); };
  useEffect(() => {
    alive.current = true; dialog.current?.showModal();
    const previous = document.body.style.overflow; document.body.style.overflow = 'hidden';
    return () => { alive.current = false; document.body.style.overflow = previous; };
  }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true);
    request<WorkLinks>(`/api/works/${work.id}/links`, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) { setCurrent(value); setDraft(value.links); setError(''); setConflict(false); setCopyFeedback(null); }
    }).catch(error => { if (!controller.signal.aborted) setError(translate('无法读取链接：{error}', { error: errorMessage(error) })); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [work.id, retry]);
  useEffect(() => { if (!loading && current) initialInput.current?.focus(); }, [loading, current]);
  useEffect(() => { if (confirmClose) keepEditing.current?.focus(); }, [confirmClose]);
  const copySavedLink = async (kind: WorkLinkKind) => {
    const value = current?.links[kind];
    if (!value || copyLock.current) return;
    copyLock.current = true; setCopyFeedback({ kind, status: 'copying' });
    try {
      let copied = false;
      if (navigator.clipboard?.writeText) {
        try { await navigator.clipboard.writeText(value); copied = true; } catch { /* Use the HTTP-compatible fallback below. */ }
      }
      if (!copied) {
        if (!alive.current || !dialog.current) return;
        const previous = document.activeElement as HTMLElement | null;
        const text = document.createElement('textarea');
        text.value = value; text.readOnly = true; text.style.cssText = 'position:fixed;left:0;top:0;opacity:0;pointer-events:none';
        // A modal makes elements outside it inert, so select inside this dialog.
        dialog.current.appendChild(text);
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
        } catch { setError(translate('{label}应为有效的 http(s) 地址，不包含用户名、密码或空白字符。', { label: translate(label) })); return; }
      }
      edits[kind] = value;
    }
    lock.current = true; setSaving(true); setError('');
    try {
      const updated = await request<WorkLinks>(`/api/works/${work.id}/links`, { method: 'PATCH', body: JSON.stringify({ links: edits, expected_revision: current.links_revision }) });
      if (alive.current) { onSaved(updated); onClose(); }
    } catch (error) {
      if (alive.current) {
        const stale = error instanceof ApiError && error.status === 409;
        setConflict(stale); setError(stale ? translate('链接已被其他页面更新，请读取最新链接后重新编辑。当前输入仍保留。') : translate('链接未保存：{error}', { error: errorMessage(error) }));
      }
    } finally { lock.current = false; if (alive.current) setSaving(false); }
  };
  return <dialog ref={dialog} className="tag-dialog work-links-dialog" aria-labelledby="work-links-title" onCancel={event => { event.preventDefault(); close(); }}>
    <div className="detail-header"><div><span className="section-label">{translate("发布资料")}</span><h2 id="work-links-title">{translate('{id} · 发布链接', { id: work.script_id })}</h2></div><button className="icon-button" aria-label={translate("关闭链接编辑")} onClick={close} disabled={saving}><X size={20} /></button></div>
    {confirmClose && <div className="discard-confirm" role="alert"><strong>{translate("链接修改尚未保存")}</strong><p>{translate("关闭会放弃本次输入。")}</p><div><button ref={keepEditing} className="button small" onClick={() => { setConfirmClose(false); initialInput.current?.focus(); }}>{translate("继续编辑链接")}</button><button className="button small" onClick={onClose}>{translate("放弃链接修改并关闭")}</button></div></div>}
    <div className="tag-dialog-body">
      <p className="help-text">{translate("链接绑定当前完整编号，保存后扫描或重启不会覆盖。清空输入并保存可移除链接。")}</p>
      <p className="help-text">{translate("首次填写并保存 Patreon / ES 帖子链接时，发布日期默认记当天；可在作品详情手动修改。填写并保存 ES 帖子链接会自动标记 ES 已发布；Patreon 状态仍由你单独维护。移除链接不会撤回发布状态或日期。")}</p>
      {error && <div className="notice error" role="alert"><span>{translate(error)}</span>{(conflict || !current) && <button className="button small" disabled={loading} onClick={() => setRetry(value => value + 1)}>{conflict ? translate('放弃输入并读取最新链接') : translate('重试读取链接')}</button>}</div>}
      {loading ? <p className="loading-state" role="status"><LoaderCircle className="spin" size={18} />{translate("正在读取发布链接")}</p> : current && <form id="work-links-form" className="work-links-form" onSubmit={event => void save(event)} noValidate>
        {linkTypes.map(({ kind, label, Icon }) => <div className={`work-link-field ${kind === initialKind ? 'chosen' : ''}`} key={kind}>
          <label htmlFor={`work-link-${kind}`}><Icon size={16} aria-hidden="true" />{translate(label)}</label>
          <input ref={kind === initialKind ? initialInput : undefined} id={`work-link-${kind}`} type="url" inputMode="url" autoComplete="off" spellCheck={false} maxLength={4000} placeholder="https://…" value={draft[kind]} disabled={saving || conflict} onChange={event => { setDraft(previous => ({ ...previous, [kind]: event.target.value })); setError(''); }} />
          {safeLink(current.links[kind]) && <div className="work-link-actions"><a href={safeLink(current.links[kind])!} target="_blank" rel="noreferrer">{translate("打开已保存的链接")} <ExternalLink size={12} aria-hidden="true" /></a><button type="button" className="button small" aria-label={translate('复制已保存的{label}', { label: translate(label) })} aria-busy={copyFeedback?.kind === kind && copyFeedback.status === 'copying'} onClick={() => void copySavedLink(kind)}>{copyFeedback?.kind === kind && copyFeedback.status === 'copied' ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}{translate('复制')}</button>{copyFeedback?.kind === kind && <span className={copyFeedback.status === 'failed' ? 'inline-error' : 'help-text'} role={copyFeedback.status === 'failed' ? 'alert' : 'status'}>{copyFeedback.status === 'copied' ? translate('已复制') : copyFeedback.status === 'failed' ? translate('复制失败，请手动复制输入框中的链接。') : translate('正在复制…')}</span>}</div>}
        </div>)}
      </form>}
    </div>
    <div className="detail-actions"><div><button className="button" disabled={saving} onClick={close}>{translate("取消")}</button><button type="submit" form="work-links-form" className="button primary" disabled={loading || saving || !dirty || conflict}>{saving ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}{saving ? translate('正在保存') : translate('保存链接')}</button></div></div>
  </dialog>;
}

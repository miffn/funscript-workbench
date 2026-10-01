import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Check, CircleDollarSign, ExternalLink, FileDown, LoaderCircle, MessagesSquare, Video, X } from 'lucide-react';
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
  return <div className="work-link-buttons" role="group" aria-label={`${work.script_id} 的发布链接`}>
    {linkTypes.map(({ kind, label, short, Icon }) => {
      const filled = !!work.links?.[kind];
      return <button key={kind} type="button" className={`work-link-button ${filled ? 'filled' : 'empty'} ${kind}`}
        aria-label={`${filled ? '编辑' : '填写'} ${label} ${work.script_id}`} title={`${short} · ${filled ? '已填写，点击编辑' : '未填写，点击添加'}`}
        onClick={() => onEdit(kind)}><Icon size={17} aria-hidden="true" /><span>{short}</span>{filled && <Check className="link-filled-mark" size={10} aria-hidden="true" />}</button>;
    })}
  </div>;
}

export function WorkLinkEditor({ work, initialKind, onClose, onSaved }: {
  work: Pick<Work, 'id' | 'script_id'>; initialKind: WorkLinkKind; onClose: () => void; onSaved: (value: WorkLinks) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const initialInput = useRef<HTMLInputElement>(null);
  const keepEditing = useRef<HTMLButtonElement>(null);
  const alive = useRef(true);
  const lock = useRef(false);
  const [current, setCurrent] = useState<WorkLinks | null>(null);
  const [draft, setDraft] = useState<WorkLinkValues>(emptyLinks);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [retry, setRetry] = useState(0);
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
      if (!controller.signal.aborted) { setCurrent(value); setDraft(value.links); setError(''); setConflict(false); }
    }).catch(error => { if (!controller.signal.aborted) setError(`无法读取链接：${errorMessage(error)}`); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [work.id, retry]);
  useEffect(() => { if (!loading && current) initialInput.current?.focus(); }, [loading, current]);
  useEffect(() => { if (confirmClose) keepEditing.current?.focus(); }, [confirmClose]);
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
        } catch { setError(`${label}应为有效的 http(s) 地址，不包含用户名、密码或空白字符。`); return; }
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
        setConflict(stale); setError(stale ? '链接已被其他页面更新，请读取最新链接后重新编辑。当前输入仍保留。' : `链接未保存：${errorMessage(error)}`);
      }
    } finally { lock.current = false; if (alive.current) setSaving(false); }
  };
  return <dialog ref={dialog} className="tag-dialog work-links-dialog" aria-labelledby="work-links-title" onCancel={event => { event.preventDefault(); close(); }}>
    <div className="detail-header"><div><span className="section-label">发布资料</span><h2 id="work-links-title">{work.script_id} · 发布链接</h2></div><button className="icon-button" aria-label="关闭链接编辑" onClick={close} disabled={saving}><X size={20} /></button></div>
    {confirmClose && <div className="discard-confirm" role="alert"><strong>链接修改尚未保存</strong><p>关闭会放弃本次输入。</p><div><button ref={keepEditing} className="button small" onClick={() => { setConfirmClose(false); initialInput.current?.focus(); }}>继续编辑链接</button><button className="button small" onClick={onClose}>放弃链接修改并关闭</button></div></div>}
    <div className="tag-dialog-body">
      <p className="help-text">链接绑定当前完整编号，保存后扫描或重启不会覆盖。清空输入并保存可移除链接。</p>
      {error && <div className="notice error" role="alert"><span>{error}</span>{(conflict || !current) && <button className="button small" disabled={loading} onClick={() => setRetry(value => value + 1)}>{conflict ? '放弃输入并读取最新链接' : '重试读取链接'}</button>}</div>}
      {loading ? <p className="loading-state" role="status"><LoaderCircle className="spin" size={18} />正在读取发布链接</p> : current && <form id="work-links-form" className="work-links-form" onSubmit={event => void save(event)} noValidate>
        {linkTypes.map(({ kind, label, Icon }) => <div className={`work-link-field ${kind === initialKind ? 'chosen' : ''}`} key={kind}>
          <label htmlFor={`work-link-${kind}`}><Icon size={16} aria-hidden="true" />{label}</label>
          <input ref={kind === initialKind ? initialInput : undefined} id={`work-link-${kind}`} type="url" inputMode="url" autoComplete="off" spellCheck={false} maxLength={4000} placeholder="https://…" value={draft[kind]} disabled={saving || conflict} onChange={event => { setDraft(previous => ({ ...previous, [kind]: event.target.value })); setError(''); }} />
          {safeLink(current.links[kind]) && <a href={safeLink(current.links[kind])!} target="_blank" rel="noreferrer">打开已保存的链接 <ExternalLink size={12} aria-hidden="true" /></a>}
        </div>)}
      </form>}
    </div>
    <div className="detail-actions"><div><button className="button" disabled={saving} onClick={close}>取消</button><button type="submit" form="work-links-form" className="button primary" disabled={loading || saving || !dirty || conflict}>{saving ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}{saving ? '正在保存' : '保存链接'}</button></div></div>
  </dialog>;
}

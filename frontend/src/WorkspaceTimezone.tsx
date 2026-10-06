import { useDialogBackdropClose } from './useDialogBackdropClose';
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Check, Clock3, LoaderCircle, Search, X } from 'lucide-react';
import { ApiError, errorMessage, request } from './api';
import type { WorkspaceTimezoneState } from './api';
import { useI18n } from './i18n';
import './WorkspaceTimezone.css';

let current: WorkspaceTimezoneState = { timezone: 'Asia/Shanghai', revision: 0, choices: ['Asia/Shanghai', 'UTC'] };
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const getWorkspaceTimezone = () => current;
export function setWorkspaceTimezone(value: WorkspaceTimezoneState) {
  if (typeof value?.timezone !== 'string' || !Number.isInteger(value.revision) || value.revision < current.revision) return;
  try { new Intl.DateTimeFormat('en', { timeZone: value.timezone }); } catch { return; }
  current = { ...value, choices: Array.isArray(value.choices) ? value.choices : current.choices };
  listeners.forEach(listener => listener());
}
export function useWorkspaceTimezone() { return useSyncExternalStore(subscribe, getWorkspaceTimezone, getWorkspaceTimezone); }
/** Call once in App; updates also rerender timestamps elsewhere in the shell. */
export function useWorkspaceTimezoneBootstrap() {
  const state = useWorkspaceTimezone();
  useEffect(() => {
    let alive = true, inFlight = false;
    let controller: AbortController | null = null;
    const load = async () => {
      if (inFlight) return;
      inFlight = true; controller = new AbortController();
      try { const saved = await request<WorkspaceTimezoneState>('/api/settings/timezone', { signal: controller.signal }); if (alive) setWorkspaceTimezone(saved); }
      catch { /* Keep the last known time zone while offline. */ }
      finally { inFlight = false; }
    };
    void load(); const timer = setInterval(() => void load(), 10000);
    return () => { alive = false; clearInterval(timer); controller?.abort(); };
  }, []);
  return state;
}
export function workspaceToday(timezone = getWorkspaceTimezone().timezone) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  return ['year', 'month', 'day'].map(type => parts.find(part => part.type === type)!.value).join('-');
}

export function WorkspaceTimezoneSettings() {
  const { t } = useI18n();
  const saved = useWorkspaceTimezone();
  const dialog = useRef<HTMLDialogElement>(null), search = useRef<HTMLInputElement>(null);
  const [opened, setOpened] = useState(false), [loaded, setLoaded] = useState<WorkspaceTimezoneState | null>(null);
  const [draft, setDraft] = useState(saved.timezone), [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [conflict, setConflict] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false), [retry, setRetry] = useState(0);
  const lock = useRef(false);
  const dirty = !!loaded && draft !== loaded.timezone;
  const close = () => { if (busy) return; if (dirty) setConfirmClose(true); else setOpened(false); };
  const backdrop = useDialogBackdropClose(close);
  useEffect(() => {
    if (!opened) return;
    dialog.current?.showModal(); search.current?.focus();
    const previous = document.body.style.overflow; document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previous; };
  }, [opened]);
  useEffect(() => {
    if (!opened) return;
    const controller = new AbortController(); setLoading(true);
    request<WorkspaceTimezoneState>('/api/settings/timezone', { signal: controller.signal }).then(value => {
      if (controller.signal.aborted) return;
      setLoaded(value); setDraft(value.timezone); setWorkspaceTimezone(value); setError(''); setConflict(false);
    }).catch(reason => { if (!controller.signal.aborted) setError(errorMessage(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [opened, retry]);
  async function save() {
    if (!loaded || !dirty || busy || lock.current || conflict) return;
    lock.current = true; setBusy(true); setError('');
    try {
      const updated = await request<WorkspaceTimezoneState>('/api/settings/timezone', { method: 'PUT', body: JSON.stringify({ timezone: draft, expected_revision: loaded.revision }) });
      setWorkspaceTimezone(updated); setLoaded(updated); setOpened(false);
    } catch (reason) { setConflict(reason instanceof ApiError && reason.status === 409); setError(errorMessage(reason)); }
    finally { lock.current = false; setBusy(false); }
  }
  const choices = (loaded?.choices || []).filter(zone => zone.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <>
    <div className="preference-row"><div><strong>{t('工作台时区')}</strong><p>{t('所有设备共用；影响时间显示与新记录的发布日期，历史日期保持原样。')}</p></div><button type="button" className="text-action timezone-current" onClick={() => { setQuery(''); setConfirmClose(false); setOpened(true); }}><Clock3 size={15} />{saved.timezone}</button></div>
    {opened && <dialog {...backdrop} className="timezone-dialog" ref={dialog} aria-labelledby="workspace-timezone-title" onCancel={event => { event.preventDefault(); close(); }}>
      <header><div><h2 id="workspace-timezone-title">{t('工作台时区')}</h2><p>{t('选择一个时区，所有设备同步使用。')}</p></div><button type="button" className="icon-button" aria-label={t('关闭时区选择')} disabled={busy} onClick={close}><X size={19} /></button></header>
      <div className="timezone-dialog-body">
        {confirmClose && <div className="discard-confirm" role="alert"><p>{t('时区修改尚未保存')}</p><button type="button" className="button small" onClick={() => setConfirmClose(false)}>{t('继续编辑')}</button><button type="button" className="button small" onClick={() => setOpened(false)}>{t('放弃修改并关闭')}</button></div>}
        <label className="search-field"><Search size={16} /><span className="sr-only">{t('搜索时区')}</span><input ref={search} type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder={t('搜索城市或时区名称…')} /></label>
        <p className="help-text">{t('当前选择')} · {draft}</p>
        {error && <div className="notice error" role="alert"><span>{t(error)}</span><button type="button" className="button small" disabled={loading || busy} onClick={() => setRetry(value => value + 1)}>{t(conflict ? '放弃选择并读取最新时区' : '重新读取时区')}</button></div>}
        {loading ? <p className="help-text" role="status"><LoaderCircle className="spin" size={15} />{t('正在读取时区…')}</p> : <div className="timezone-options" role="group" aria-label={t('可选时区')}>{choices.map(zone => <button type="button" key={zone} aria-pressed={zone === draft} disabled={busy || conflict} onClick={() => setDraft(zone)}><span>{zone}</span>{zone === draft && <Check size={15} />}</button>)}{!choices.length && <p className="help-text">{t('没有匹配的时区')}</p>}</div>}
      </div><footer><span>{t('历史日期不会移动')}</span><button type="button" className="button primary" disabled={loading || busy || !dirty || conflict} onClick={() => void save()}>{busy ? <LoaderCircle size={15} className="spin" /> : <Check size={15} />}{t(busy ? '正在保存' : '保存时区')}</button></footer>
    </dialog>}
  </>;
}

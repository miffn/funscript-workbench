import { useEffect, useRef, useState } from 'react';
import { Check, CircleAlert, Folder, LoaderCircle, RefreshCw } from 'lucide-react';
import { ApiError, errorMessage, request } from './api';
import type { Settings } from './api';

interface RootOption { path: string; windowsPath: string; label: string; available: boolean | null; enabled: boolean }
function rootOptions(settings: Settings): RootOption[] {
  return settings.roots.map((root, index) => {
    if (typeof root === 'string') return { path: root, windowsPath: root, label: root.split(/[\\/]/).filter(Boolean).at(-1) || `目录 ${index + 1}`, available: null, enabled: true };
    const path = typeof root.path === 'string' ? root.path : '';
    return { path, windowsPath: typeof root.windows_path === 'string' ? root.windows_path : path, label: typeof root.label === 'string' ? root.label : `目录 ${index + 1}`, available: typeof root.available === 'boolean' ? root.available : null, enabled: root.enabled !== false };
  });
}
const enabledPaths = (settings: Settings) => rootOptions(settings).filter(root => root.enabled).map(root => root.path);
const samePaths = (left: string[], right: string[]) => [...left].sort().join('\n') === [...right].sort().join('\n');

export function ScanRootsEditor({ settings }: { settings: Settings }) {
  const [baseline, setBaseline] = useState(settings);
  const [draft, setDraft] = useState(() => enabledPaths(settings));
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [conflict, setConflict] = useState(false);
  const baselineRef = useRef(settings);
  const dirtyRef = useRef(false);
  const operation = useRef(false);
  const conflictRef = useRef(false);
  const alive = useRef(true);
  const roots = rootOptions(baseline);
  const dirty = !samePaths(draft, enabledPaths(baseline));
  const editable = typeof baseline.scan_roots_revision === 'number' && roots.every(root => !!root.path);
  const accept = (next: Settings) => {
    baselineRef.current = next; dirtyRef.current = false; conflictRef.current = false;
    setBaseline(next); setDraft(enabledPaths(next)); setConflict(false);
  };
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (dirtyRef.current || operation.current || conflictRef.current) return;
    const currentVersion = baselineRef.current.scan_roots_revision;
    if (typeof currentVersion === 'number' && typeof settings.scan_roots_revision === 'number' && settings.scan_roots_revision < currentVersion) return;
    accept(settings);
  }, [settings]);
  const toggle = (path: string) => {
    if (operation.current) return;
    const next = draft.includes(path) ? draft.filter(value => value !== path) : [...draft, path];
    dirtyRef.current = !samePaths(next, enabledPaths(baselineRef.current));
    setDraft(next); setSuccess(''); if (!conflictRef.current) setError('');
  };
  const save = async () => {
    if (operation.current || !dirty || !draft.length || !editable || conflictRef.current) return;
    operation.current = true; setSaving(true); setError(''); setSuccess('');
    try {
      const result = await request<Settings>('/api/settings/scan-roots', { method: 'PUT', body: JSON.stringify({ enabled_paths: draft, expected_revision: baselineRef.current.scan_roots_revision }) });
      if (alive.current) { accept(result); setSuccess('扫描目录已保存；尚未执行扫描。需要更新库存时请点击“立即扫描”。'); }
    } catch (error) {
      if (alive.current) {
        const stale = error instanceof ApiError && error.status === 409;
        conflictRef.current = stale; setConflict(stale);
        setError(stale ? '扫描目录已被其他客户端修改。本次选择仍保留，请加载最新设置后重新确认。' : `扫描目录未保存：${errorMessage(error)}`);
      }
    } finally { operation.current = false; if (alive.current) setSaving(false); }
  };
  const loadLatest = async () => {
    if (operation.current) return;
    operation.current = true; setRefreshing(true); setSuccess('');
    try { const latest = await request<Settings>('/api/settings'); if (alive.current) { accept(latest); setError(''); setSuccess('已加载最新设置，请重新确认扫描目录。'); } }
    catch (error) { if (alive.current) setError(`无法加载最新设置：${errorMessage(error)}`); }
    finally { operation.current = false; if (alive.current) setRefreshing(false); }
  };
  return <section className="settings-card scan-roots-editor" aria-labelledby="scan-roots-title">
    <div className="section-heading"><Folder size={19} /><h2 id="scan-roots-title">扫描目录</h2>{dirty && <span className="unsaved-label">未保存</span>}</div>
    <p className="help-text">勾选下一次手动扫描要读取的目录。取消勾选不会删除已有库存或标签，保存也不会触发扫描。</p>
    <fieldset className="scan-root-choices" disabled={saving || refreshing || !editable}><legend className="sr-only">启用的扫描目录</legend>{roots.map((root, index) => <label className={`root-item scan-root-option ${draft.includes(root.path) ? 'selected' : ''}`} key={root.path || index}><input type="checkbox" checked={draft.includes(root.path)} onChange={() => toggle(root.path)} aria-label={`扫描 ${root.label}`} /><div><strong>{root.label}</strong><code>{root.windowsPath}</code></div><span className={`badge ${root.available === false ? 'warning' : 'neutral'}`}>{root.available === false ? '暂不可用' : root.available === true ? '可读取' : '已配置'}</span></label>)}</fieldset>
    {!editable && <p className="help-text">当前服务尚未提供可保存的目录版本，请更新服务后重试。</p>}
    {!draft.length && <p className="inline-error scan-root-feedback" role="alert">至少保留一个扫描目录。</p>}
    {error && <p className="inline-error scan-root-feedback" role="alert"><CircleAlert size={15} />{error}</p>}
    {success && <p className="scan-root-success" role="status"><Check size={16} />{success}</p>}
    <div className="scan-root-actions"><button className="button primary" onClick={() => void save()} disabled={!dirty || !draft.length || !editable || saving || refreshing || conflict}>{saving ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}{saving ? '正在保存扫描目录' : '保存扫描目录'}</button>{(dirty || conflict) && <button className="button" onClick={() => void loadLatest()} disabled={saving || refreshing}>{refreshing ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}{refreshing ? '正在加载最新设置' : '放弃选择并加载最新设置'}</button>}</div>
  </section>;
}

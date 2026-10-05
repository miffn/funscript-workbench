import { SettingsSection } from './SettingsSection';
import { useI18n, translate } from './i18n';
import { useEffect, useRef, useState } from 'react';
import { Check, CircleAlert, Folder, LoaderCircle, Plus, RefreshCw, Trash2 } from 'lucide-react';
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
const catalog = (roots: RootOption[]) => roots.map(({ path, label, enabled }) => ({ path, label, enabled }));
const sameRoots = (left: RootOption[], right: RootOption[]) => JSON.stringify(catalog(left)) === JSON.stringify(catalog(right));

export function ScanRootsEditor({ settings, collapsible = false }: { settings: Settings; collapsible?: boolean }) {
  useI18n();
  const [baseline, setBaseline] = useState(settings);
  const [draft, setDraft] = useState(() => rootOptions(settings));
  const [newPath, setNewPath] = useState('');
  const [newLabel, setNewLabel] = useState('');
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
  const dirty = !sameRoots(draft, rootOptions(baseline));
  const editable = typeof baseline.scan_roots_revision === 'number' && draft.every(root => !!root.path);
  const accept = (next: Settings) => {
    baselineRef.current = next; dirtyRef.current = false; conflictRef.current = false;
    setBaseline(next); setDraft(rootOptions(next)); setConflict(false);
  };
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (dirtyRef.current || operation.current || conflictRef.current) return;
    const currentVersion = baselineRef.current.scan_roots_revision;
    if (typeof currentVersion === 'number' && typeof settings.scan_roots_revision === 'number' && settings.scan_roots_revision < currentVersion) return;
    accept(settings);
  }, [settings]);
  const updateDraft = (next: RootOption[]) => {
    if (operation.current) return;
    dirtyRef.current = !sameRoots(next, rootOptions(baselineRef.current));
    setDraft(next); setSuccess(''); if (!conflictRef.current) setError('');
  };
  const toggle = (path: string) => updateDraft(draft.map(root => root.path === path ? { ...root, enabled: !root.enabled } : root));
  const add = () => {
    const path = newPath.trim();
    if (!/^(?:[a-zA-Z]:[\\/]|\/(?!\/))/.test(path)) { setError(translate('请输入 Windows 盘符绝对路径或 WSL 绝对路径。')); return; }
    const comparable = (value: string) => value.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
    if (draft.some(root => [root.path, root.windowsPath].some(value => comparable(value) === comparable(path)))) { setError(translate('该目录已在列表中。')); return; }
    updateDraft([...draft, { path, windowsPath: path, label: newLabel.trim() || path.split(/[\\/]/).filter(Boolean).at(-1) || path, enabled: true, available: null }]);
    setNewPath(''); setNewLabel('');
  };
  const save = async () => {
    if (operation.current || !dirty || !editable || conflictRef.current) return;
    operation.current = true; setSaving(true); setError(''); setSuccess('');
    try {
      const result = await request<Settings>('/api/settings/scan-roots', { method: 'PUT', body: JSON.stringify({ roots: catalog(draft), expected_revision: baselineRef.current.scan_roots_revision }) });
      if (alive.current) { accept(result); setSuccess(translate('扫描目录已保存；尚未执行扫描。需要更新库存时请点击“立即扫描”。')); }
    } catch (error) {
      if (alive.current) {
        const stale = error instanceof ApiError && error.status === 409 && /客户端|版本|revision|其他.*修改/i.test(error.message);
        conflictRef.current = stale; setConflict(stale);
        setError(stale ? translate('扫描目录已被其他客户端修改。本次选择仍保留，请加载最新设置后重新确认。') : translate('扫描目录未保存：{error}', { error: errorMessage(error) }));
      }
    } finally { operation.current = false; if (alive.current) setSaving(false); }
  };
  const loadLatest = async () => {
    if (operation.current) return;
    operation.current = true; setRefreshing(true); setSuccess('');
    try { const latest = await request<Settings>('/api/settings'); if (alive.current) { accept(latest); setError(''); setSuccess(translate('已加载最新设置，请重新确认扫描目录。')); } }
    catch (error) { if (alive.current) setError(translate('无法加载最新设置：{error}', { error: errorMessage(error) })); }
    finally { operation.current = false; if (alive.current) setRefreshing(false); }
  };
  return <SettingsSection className="scan-roots-editor" headingId="scan-roots-title" title={translate('扫描目录')} icon={<Folder size={19} aria-hidden="true" />} collapsible={collapsible} headingExtra={dirty && <span className="unsaved-label">{translate('未保存')}</span>}>
    <p className="help-text">{translate("添加、删除或勾选下一次手动扫描要读取的目录。取消勾选不会删除已有库存或标签，保存也不会触发扫描。删除目录仅取消扫描配置，原文件与作品资料均保留。")}</p>
    <fieldset className="scan-root-choices" disabled={saving || refreshing || !editable}><legend className="sr-only">{translate("启用的扫描目录")}</legend>{draft.map(root => <div className={`root-item scan-root-option ${root.enabled ? 'selected' : ''}`} key={root.path}><label className="scan-root-select"><input type="checkbox" checked={root.enabled} onChange={() => toggle(root.path)} aria-label={translate('扫描 {name}', { name: root.label })} /><span className="scan-root-info"><strong>{root.label}</strong><code>{root.windowsPath}</code></span></label><span className={`badge ${root.available === false ? 'warning' : 'neutral'}`}>{root.available === false ? translate('暂不可用') : root.available === true ? translate('可读取') : translate('待保存')}</span><button className="scan-root-remove" onClick={() => updateDraft(draft.filter(value => value.path !== root.path))} aria-label={translate('删除目录 {name}', { name: root.label })} title={translate("只移除配置，不删除文件")}><Trash2 size={18} /></button></div>)}</fieldset>
    {!draft.length && <p className="help-text">{translate("尚未配置扫描目录，请添加素材所在的目录。")}</p>}
    <form className="scan-root-add" onSubmit={event => { event.preventDefault(); if (!operation.current && editable) add(); }}><label>{translate("目录路径")}<input value={newPath} onChange={event => setNewPath(event.target.value)} placeholder={translate('例如 E:\\\\素材 或 /mnt/e/素材')} disabled={saving || refreshing || !editable} /></label><label>{translate("名称（可选）")}<input value={newLabel} onChange={event => setNewLabel(event.target.value)} placeholder={translate("便于识别的名称")} disabled={saving || refreshing || !editable} /></label><button className="button" type="submit" disabled={!newPath.trim() || saving || refreshing || !editable}><Plus size={16} />{translate("添加目录")}</button></form>
    {!editable && <p className="help-text">{translate("当前服务尚未提供可保存的目录版本，请更新服务后重试。")}</p>}
    {!draft.some(root => root.enabled) && <p className="help-text">{translate("没有启用目录，立即扫描暂不可用。")}</p>}
    {error && <p className="inline-error scan-root-feedback" role="alert"><CircleAlert size={15} />{translate(error)}</p>}
    {success && <p className="scan-root-success" role="status"><Check size={16} />{translate(success)}</p>}
    <div className="scan-root-actions"><button className="button primary" onClick={() => void save()} disabled={!dirty || !editable || saving || refreshing || conflict}>{saving ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}{saving ? translate('正在保存扫描目录') : translate('保存扫描目录')}</button>{(dirty || conflict) && <button className="button" onClick={() => void loadLatest()} disabled={saving || refreshing}>{refreshing ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}{refreshing ? translate('正在加载最新设置') : translate('放弃选择并加载最新设置')}</button>}</div>
  </SettingsSection>;
}

import { useI18n, translate } from './i18n';
import { useEffect, useRef, useState } from 'react';
import { Archive, Check, ImagePlus, LoaderCircle, Trash2, UserRound } from 'lucide-react';

export type ProfileData = { name: string; bio: string; avatar: string | null; revision: number };
const MAX_AVATAR_BYTES = 300_000;
const AVATAR_MIMES = ['image/png', 'image/jpeg', 'image/webp'];

async function requestProfile(init?: RequestInit): Promise<ProfileData> {
  const response = await fetch('/api/profile', init);
  const body = await response.json();
  if (!response.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : translate('资料保存失败，请检查姓名、简介及头像图片');
    throw new Error(detail);
  }
  return body;
}

export function ProfileSettings({ profile, onSaved }: { profile: ProfileData; onSaved: (profile: ProfileData) => void }) {
  useI18n();
  const [baseline, setBaseline] = useState(profile);
  const [draft, setDraft] = useState(profile);
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [conflict, setConflict] = useState(false);
  const uploadRef = useRef<HTMLInputElement>(null);
  const dirty = draft.name !== baseline.name || draft.bio !== baseline.bio || draft.avatar !== baseline.avatar;
  useEffect(() => {
    if (!dirty && profile.revision > baseline.revision) { setBaseline(profile); setDraft(profile); }
  }, [profile, baseline.revision, dirty]);

  function edit(patch: Partial<ProfileData>) {
    setDraft(value => ({ ...value, ...patch })); setError(''); setMessage('');
  }
  async function upload(file: File | undefined) {
    if (!file) return;
    setError(''); setMessage('');
    if (!AVATAR_MIMES.includes(file.type)) { setError(translate('头像仅支持 PNG、JPEG 或 WebP 图片')); return; }
    if (file.size > MAX_AVATAR_BYTES) { setError(translate('头像不能超过 300 KB，请先缩小图片')); return; }
    setReading(true);
    try {
      const avatar = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => typeof reader.result === 'string' ? resolve(reader.result) : reject(new Error(translate('头像读取失败')));
        reader.onerror = () => reject(new Error(translate('头像读取失败，请重新选择图片')));
        reader.readAsDataURL(file);
      });
      edit({ avatar });
    } catch (reason) { setError(reason instanceof Error ? reason.message : translate('头像读取失败')); }
    finally { setReading(false); }
  }
  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setMessage('');
    try {
      const saved = await requestProfile({ method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: draft.name.trim(), bio: draft.bio.trim(), avatar: draft.avatar, expected_revision: baseline.revision }) });
      setBaseline(saved); setDraft(saved); setConflict(false); onSaved(saved); setMessage(translate('工作台资料已保存'));
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : translate('资料保存失败');
      setError(detail); setConflict(detail.includes('其他页面') || detail.includes('重新加载'));
    } finally { setBusy(false); }
  }
  async function reload() {
    setBusy(true); setError('');
    try {
      const latest = await requestProfile(); setBaseline(latest); setDraft(latest); setConflict(false); onSaved(latest); setMessage(translate('已加载最新资料，可继续修改'));
    } catch (reason) { setError(reason instanceof Error ? reason.message : translate('资料加载失败')); }
    finally { setBusy(false); }
  }

  return <section className="settings-card profile-settings" aria-labelledby="profile-settings-title">
    <h2 id="profile-settings-title"><UserRound size={20} aria-hidden="true" /> {translate("工作台资料")}</h2>
    <p className="profile-help">{translate("修改侧边栏的头像、姓名和简介，保存后所有设备都会使用这份资料。")}</p>
    <form onSubmit={save}>
      <div className="profile-avatar-editor">
        <div className="profile-avatar" aria-label={translate("头像预览")}>{draft.avatar ? <img src={draft.avatar} alt={translate('{name}头像', { name: draft.name || translate('工作台') })} width={64} height={64} /> : <Archive size={28} aria-hidden="true" />}</div>
        <div className="profile-avatar-controls">
          <div className="profile-avatar-actions">
            <button type="button" className="button" onClick={() => uploadRef.current?.click()} disabled={busy || reading}><ImagePlus size={16} aria-hidden="true" />{reading ? translate('正在读取头像') : translate('选择头像')}</button>
            <button type="button" className="button" disabled={busy || reading || !draft.avatar} onClick={() => edit({ avatar: null })}><Trash2 size={16} aria-hidden="true" />{translate("移除头像")}</button>
          </div>
          <input ref={uploadRef} className="sr-only" type="file" accept="image/png,image/jpeg,image/webp" aria-label={translate("上传工作台头像")} disabled={busy || reading} onChange={event => { void upload(event.target.files?.[0]); event.target.value = ''; }} />
          <p className="profile-help">{translate("PNG、JPEG 或 WebP，最多 300 KB，建议使用方形图片。")}</p>
        </div>
      </div>
      <div className="profile-text-fields">
        <label>{translate("姓名")}<input value={draft.name} maxLength={80} required disabled={busy} onChange={event => edit({ name: event.target.value })} /></label>
        <label>{translate("简介")}<textarea value={draft.bio} maxLength={160} rows={2} disabled={busy} onChange={event => edit({ bio: event.target.value })} /><span className="profile-help">{draft.bio.length} / 160</span></label>
      </div>
      {error && <p className="profile-error" role="alert">{translate(error)}</p>}
      {message && <p className="profile-success" role="status">{translate(message)}</p>}
      <div className="profile-save-actions">
        <button className="button primary" type="submit" disabled={busy || reading || !dirty || !draft.name.trim()}>{busy ? <LoaderCircle size={16} aria-hidden="true" /> : <Check size={16} aria-hidden="true" />}{busy ? translate('正在保存') : translate('保存工作台资料')}</button>
        {conflict && <button type="button" className="button" disabled={busy || reading} onClick={() => void reload()}>{translate("重新加载资料（替换当前输入）")}</button>}
      </div>
    </form>
  </section>;
}

import { useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { Archive, ArrowRight, Check, CheckCheck, CircleAlert, Clock3, FileText, Film, Folder, FolderOpen, Image, LoaderCircle, RefreshCw, X } from 'lucide-react';
import { displayValue, errorMessage, formatDate, formatSize, historyEntries, isActiveJob, isPublished, jobLabel, request, safeLink } from './api';
import type { Asset, Capabilities, Issue, Job, PublicationPlatform, Settings, Work } from './api';
import type { Notice } from './App';
import { PreviewSection } from './PreviewSection';
import { TagChips, WorkTagEditor } from './Tags';
import { ScanRootsEditor } from './ScanRootsEditor';
import { ReleaseDates, workDisplayTitle } from './ReleaseDates';
import { ReleasePostEditor } from './ReleasePosts';

export function StatusBadge({ status }: { status: 'pending' | 'published' }) { return <span className={`badge ${status}`}><span className="status-dot" />{status === 'published' ? '已发布' : '待发布'}</span>; }
export function PublicationBadges({ work }: { work: Work }) { return <span className="publication-badges">{(['es', 'patreon'] as const).map(platform => <span key={platform} className={`badge ${isPublished(work, platform) ? 'published' : 'pending'}`}><span className="status-dot" />{platform === 'es' ? 'ES' : 'Patreon'} {isPublished(work, platform) ? '已发布' : '待发布'}</span>)}</span>; }

export function Cover({ work, large = false }: { work: Work; large?: boolean }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => { setFailed(false); }, [work.cover_url]);
  return <div className={`cover ${large ? 'cover-large' : ''}`}>
    {work.cover_url && !failed ? <img src={work.cover_url} alt={`${work.title || work.script_id} 的视频封面`} loading={large ? 'eager' : 'lazy'} decoding="async" onError={() => setFailed(true)} /> : <div className="cover-placeholder"><Image size={large ? 32 : 26} strokeWidth={1.4} aria-hidden="true" /><span>{failed ? '封面暂不可用' : '暂无视频封面'}</span><small>{work.script_id}</small></div>}
  </div>;
}
export function EmptyState({ title, description, children, icon = <Archive size={30} strokeWidth={1.4} /> }: { title: string; description: string; children?: ReactNode; icon?: ReactNode }) {
  return <div className="empty-state"><div className="empty-icon" aria-hidden="true">{icon}</div><h3>{title}</h3><p>{description}</p>{children}</div>;
}
export function Loading({ label = '正在读取库存' }: { label?: string }) { return <div className="loading-state" role="status"><LoaderCircle size={20} className="spin" aria-hidden="true" /><span>{label}</span></div>; }
function useResource<T>(url: string, revision: number, interval = 10000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const hasData = useRef(false);
  useEffect(() => {
    let alive = true;
    let controller: AbortController | null = null;
    let inFlight = false;
    setLoading(!hasData.current);
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      controller = new AbortController();
      try { const result = await request<T>(url, { signal: controller.signal }); if (alive) { setData(result); hasData.current = true; setError(''); } }
      catch (error) { if (alive && !controller.signal.aborted) setError(errorMessage(error)); }
      finally { inFlight = false; if (alive) setLoading(false); }
    };
    void load();
    const timer = interval ? setInterval(() => void load(), interval) : null;
    return () => { alive = false; controller?.abort(); if (timer) clearInterval(timer); };
  }, [url, revision, interval, retry]);
  return { data, error, loading, retry: () => setRetry(value => value + 1) };
}
function ResourceError({ message, retry }: { message: string; retry: () => void }) { return <div className="notice error" role="alert"><span><CircleAlert size={18} />读取失败：{message}</span><button className="button small" onClick={retry}>重试</button></div>; }
export function IssuesPage({ revision, onSelect }: { revision: number; onSelect: (id: number) => void }) {
  const { data, error, loading, retry } = useResource<{ items: Issue[]; total: number }>('/api/issues', revision);
  return <>{error && <ResourceError message={error} retry={retry} />}{loading ? <Loading label="正在检查待处理项目" /> : data && !data.items.length ? <EmptyState icon={<CheckCheck size={30} />} title="目前没有待处理项" description="编号和素材检查通过，新的异常会在扫描后出现在这里。" /> : <div className="issue-list">{data?.items.map((issue, index) => <article key={`${issue.work_id}-${issue.type}-${index}`} className="issue-item"><div className="issue-symbol"><CircleAlert size={19} /></div><div className="issue-content"><div className="issue-heading">{issue.script_id && <span className="script-id">{issue.script_id}</span>}<h2>{issue.message}</h2></div>{issue.paths?.length ? <ul className="path-list">{issue.paths.map(path => <li key={path}><Folder size={14} /><code>{path}</code></li>)}</ul> : null}<p className="issue-help">{issue.type.includes('duplicate') || issue.type.includes('conflict') ? '请检查这些目录的编号，修改文件夹后重新扫描。' : issue.type.includes('history') || issue.type.includes('unmatched') ? '历史资料尚未关联到完整编号的作品文件夹。' : '检查原目录与素材，修复后重新扫描。'}</p></div>{issue.work_id != null && <button className="button small" onClick={() => onSelect(issue.work_id!)}>查看作品 <ArrowRight size={14} /></button>}</article>)}</div>}</>;
}
export function JobsPage({ revision }: { revision: number }) {
  const { data, error, loading, retry } = useResource<{ items: Job[] }>('/api/jobs', revision, 2500);
  const labels: Record<string, string> = { works: '库存', directories: '编号目录', assets: '素材', unnumbered: '未编号素材', unavailable_roots: '不可用目录', covers_generated: '更新封面', covers_failed: '封面失败', refresh_covers: '重新生成封面', script_id: '作品编号', file_count: '生成文件', clip_count: '片段' };
  return <>{error && <ResourceError message={error} retry={retry} />}{loading ? <Loading label="正在读取任务记录" /> : data && !data.items.length ? <EmptyState icon={<RefreshCw size={30} />} title="尚无任务记录" description="执行库存扫描或生成作品预览后，会在这里留下记录。" /> : <div className="job-list">{data?.items.map(job => <article className="job-card" key={job.id}><div className="job-head"><div className={`job-icon ${job.status === 'failed' ? 'error' : ''}`}>{isActiveJob(job) ? <LoaderCircle size={20} className="spin" /> : job.status === 'failed' ? <CircleAlert size={20} /> : <Check size={20} />}</div><div><h2>{job.type === 'preview' ? '预览生成' : job.type === 'rematch' ? '文件重新匹配' : '库存扫描'} <span className="job-number">#{job.id}</span></h2><p>{formatDate(job.started_at || job.created_at)}{job.finished_at && <> · 完成于 {formatDate(job.finished_at)}</>}</p></div><span className={`badge ${job.status === 'failed' ? 'warning' : isActiveJob(job) ? 'pending' : 'published'}`}>{jobLabel(job.status, job.type)}</span></div>{job.result && <div className="job-results">{Object.entries(job.result).filter(([key, value]) => value != null && (job.type !== 'preview' || ['script_id', 'file_count', 'clip_count'].includes(key))).map(([key, value]) => <span key={key}><span>{labels[key] || key}</span><strong>{displayValue(value)}</strong></span>)}</div>}{job.error && <p className="inline-error" role="alert">{job.error}</p>}{isActiveJob(job) && <p className="help-text">{job.type === 'preview' ? job.message || '正在后台生成预览，关闭网页不会中断任务。' : job.type === 'rematch' ? job.message || '正在重新匹配作品素材，关闭网页不会中断任务。' : '正在读取文件与生成封面，关闭网页不会中断扫描。'}</p>}</article>)}</div>}</>;
}
export function SettingsPage({ capabilities, revision }: { capabilities: Capabilities; revision: number }) {
  const { data, error, loading, retry } = useResource<Settings>('/api/settings', revision);
  return <>{error && <ResourceError message={error} retry={retry} />}{loading ? <Loading label="正在读取运行设置" /> : data && <div className="settings-layout"><ScanRootsEditor settings={data} /><section className="settings-card"><div className="section-heading"><RefreshCw size={19} /><h2>扫描与库存规则</h2></div><dl className="settings-details"><dt>扫描方式</dt><dd>手动扫描，点击“立即扫描”更新库存</dd><dt>编号来源</dt><dd>文件夹编号，保留完整子编号</dd><dt>发布状态</dt><dd>ES 与 Patreon 分别维护待发布 / 已发布</dd><dt>未编号素材</dt><dd>不计入完成库存</dd><dt>冲突处理</dt><dd>保留全部路径并提醒，不自动覆盖</dd></dl></section><section className="settings-card"><div className="section-heading"><FolderOpen size={19} /><h2>打开文件夹</h2></div><p className="host-ability">{capabilities.can_open_folder ? '当前访问端支持打开素材主机的文件夹。' : capabilities.reason || '不支持打开，仅素材所在主机可用。'}</p><p className="help-text">其他客户端可以查看封面、管理库存与发布状态。</p></section></div>}</>;
}

export function WorkDetail({ id, capabilities, onClose, onSaved, notify }: { id: number; capabilities: Capabilities; onClose: () => void; onSaved: () => void; notify: (notice: Notice) => void }) {
  const [work, setWork] = useState<Work | null>(null);
  const [title, setTitle] = useState('');
  const [notes, setNotes] = useState('');
  const [esDate, setEsDate] = useState('');
  const [patreonDate, setPatreonDate] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saveError, setSaveError] = useState('');
  const [saving, setSaving] = useState(false);
  const [changingStatus, setChangingStatus] = useState(false);
  const [opening, setOpening] = useState(false);
  const [directoryId, setDirectoryId] = useState<number | null>(null);
  const [tab, setTab] = useState<'assets' | 'metadata'>('assets');
  const [editingTags, setEditingTags] = useState(false);
  const [editingPost, setEditingPost] = useState(false);
  const [matchingDirty, setMatchingDirty] = useState(false);
  const [retry, setRetry] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const continueEditing = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const dirty = !!work && (title !== work.title || notes !== (work.notes || '') || esDate !== (work.es_published_date || '') || patreonDate !== (work.patreon_published_date || ''));
  const close = () => { if (saving || changingStatus || opening) return; if (dirty || matchingDirty) { setConfirmClose(true); return; } onClose(); };
  useEffect(() => { if (confirmClose) continueEditing.current?.focus(); }, [confirmClose]);
  useEffect(() => {
    dialog.current?.showModal();
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previous; };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    request<Work>(`/api/works/${id}`, { signal: controller.signal }).then(result => { setWork(result); setTitle(result.title); setNotes(result.notes || ''); setEsDate(result.es_published_date || ''); setPatreonDate(result.patreon_published_date || ''); setDirectoryId(result.directories.length === 1 ? result.directories[0].id : null); }).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [id, retry]);
  const changeStatus = async (platform: PublicationPlatform) => {
    if (!work || changingStatus || saving) return;
    setChangingStatus(true); setSaveError('');
    try { const updated = await request<Work>(`/api/works/${id}`, { method: 'PATCH', body: JSON.stringify({ [`${platform}_published`]: !isPublished(work, platform) }) }); setWork(updated); onSaved(); notify({ kind: 'success', message: `${work.script_id} 的 ${platform === 'es' ? 'ES' : 'Patreon'} 已设为${isPublished(updated, platform) ? '已发布' : '待发布'}` }); }
    catch (error) { setSaveError(`发布状态未更新：${errorMessage(error)}`); }
    finally { setChangingStatus(false); }
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!work || !dirty) return;
    if (!title.trim()) { setSaveError('请填写作品标题。'); return; }
    setSaving(true); setSaveError('');
    try { const updated = await request<Work>(`/api/works/${id}`, { method: 'PATCH', body: JSON.stringify({ title: title.trim(), notes, ...(esDate !== (work.es_published_date || '') ? { es_published_date: esDate || null } : {}), ...(patreonDate !== (work.patreon_published_date || '') ? { patreon_published_date: patreonDate || null } : {}) }) }); setWork(updated); setTitle(updated.title); setNotes(updated.notes || ''); setEsDate(updated.es_published_date || ''); setPatreonDate(updated.patreon_published_date || ''); onSaved(); notify({ kind: 'success', message: `${updated.script_id} 的作品信息已保存` }); }
    catch (error) { setSaveError(`修改未保存：${errorMessage(error)}`); }
    finally { setSaving(false); }
  };
  const open = async () => {
    if (!work || !capabilities.can_open_folder || !directoryId) return;
    setOpening(true); setSaveError('');
    try { const result = await request<{ message: string }>(`/api/works/${id}/open-folder`, { method: 'POST', body: JSON.stringify({ directory_id: directoryId }) }); notify({ kind: 'success', message: result.message }); }
    catch (error) { setSaveError(`无法打开文件夹：${errorMessage(error)}`); }
    finally { setOpening(false); }
  };
  const currentDir = work?.directories.find(directory => directory.id === directoryId);
  const assets = work?.assets || [];
  const history = historyEntries(work?.metadata);
  const assetGroups: [string, Asset[]][] = [['视频', assets.filter(asset => asset.kind === 'video')], ['脚本', assets.filter(asset => asset.kind === 'script' || asset.kind === 'funscript')], ['辅助素材', assets.filter(asset => !['video', 'script', 'funscript'].includes(asset.kind))]];
  return <dialog className="detail-dialog" ref={dialog} aria-labelledby="detail-title" onCancel={event => { event.preventDefault(); close(); }} onClick={event => { if (event.target === event.currentTarget) { const rect = event.currentTarget.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close(); } }}>
    <div className="detail-header"><div><span className="section-label">作品详情</span><h2 id="detail-title">{work?.script_id || '正在读取'}</h2></div><button className="icon-button" ref={closeButton} onClick={close} aria-label="关闭作品详情" disabled={saving || changingStatus || opening} autoFocus><X size={21} /></button></div>
    {confirmClose && <div className="discard-confirm" role="alert"><strong>有尚未保存的修改</strong><p>关闭后将放弃尚未保存的标题、备注、发布日期或脚本对应关系。</p><div><button className="button small" ref={continueEditing} onClick={() => { setConfirmClose(false); closeButton.current?.focus(); }}>继续编辑</button><button className="button small" onClick={onClose}>放弃更改并关闭</button></div></div>}
    {loading ? <Loading label="正在读取作品详情" /> : error ? <div className="detail-body"><ResourceError message={error} retry={() => setRetry(value => value + 1)} /></div> : work && <>
      <div className="detail-body"><Cover work={work} large /><div className="detail-heading">{workDisplayTitle(work) && <h3>{workDisplayTitle(work)}</h3>}<PublicationBadges work={work} /></div><ReleaseDates work={work} /><div className="detail-summary"><span><Film size={15} />{work.video_count} 个视频</span><span><FileText size={15} />{work.script_count} 个脚本</span>{work.axis_type && <span>{work.axis_type}</span>}</div>
        {work.issues.length > 0 && <div className="detail-issues">{work.issues.map((issue, index) => <p key={`${issue.type}-${index}`}><CircleAlert size={16} /><span>{issue.message}</span></p>)}</div>}
        <section className="detail-section"><div className="section-heading"><Folder size={17} /><h3>作品目录</h3></div><div className="directory-options">{currentDir && <div className="directory-option selected"><Folder size={17} /><span><code>{currentDir.windows_path || currentDir.path}</code>{!currentDir.available && <small>目录暂不可用</small>}</span></div>}</div>{!currentDir && <p className="help-text">{work.issues.some(issue => issue.type === 'duplicate_identifier') ? '编号存在目录冲突，请处理冲突后重新匹配文件。' : '尚无可关联的本地目录，请重新匹配文件。'}</p>}
          <button className="button open-folder" onClick={() => void open()} disabled={!capabilities.can_open_folder || !directoryId || !currentDir?.available || opening}>{opening ? <LoaderCircle className="spin" size={17} /> : <FolderOpen size={17} />}{opening ? '正在发送打开请求' : '打开文件夹'}</button><p className="host-note">{!capabilities.can_open_folder ? capabilities.reason || '不支持打开，仅素材所在主机可用' : !currentDir ? '暂无唯一的关联目录。' : !currentDir.available ? '当前目录暂不可用，请检查原文件夹。' : '在素材所在 Windows 主机的资源管理器中打开。'}</p></section>
        <section className="detail-section"><div className="section-heading"><h3>作品标签</h3><button className="button small detail-edit-tags" onClick={() => setEditingTags(true)}>编辑标签</button></div><TagChips tags={work.tags} durationStatus={work.duration_status} durationError={work.duration_error} /></section>
        <section className="detail-section"><div className="section-heading"><FileText size={17} /><h3>ES 贴文</h3></div><p className="help-text">根据作品标签和发布链接套用模板，近期作品预览每次生成时自动更新。上传 Markdown 和生成稿保存在数据库中。</p><button className="button primary" onClick={() => setEditingPost(true)}><FileText size={17} />生成贴文</button>{dirty && <p className="help-text">贴文使用已保存的作品信息；修改标题后请先保存。</p>}</section>
        <PreviewSection work={work} capabilities={capabilities} onDirtyChange={setMatchingDirty} onSourcesChanged={async () => {
          const updated = await request<Work>(`/api/works/${id}`);
          // Refresh discovered source data while retaining locally edited text and tags.
          setWork(previous => previous ? { ...previous, assets: updated.assets, directories: updated.directories, video_count: updated.video_count, script_count: updated.script_count, cover_url: updated.cover_url, issues: updated.issues, axis_type: updated.axis_type,
            duration_seconds: updated.duration_seconds, duration_minutes: updated.duration_minutes,
            duration_status: updated.duration_status, duration_error: updated.duration_error,
            duration_last_known_seconds: updated.duration_last_known_seconds, duration_last_known_minutes: updated.duration_last_known_minutes,
            tags: [...(previous.tags || []).filter(tag => !['axis_type', 'duration'].includes(tag.category)), ...(updated.tags || []).filter(tag => ['axis_type', 'duration'].includes(tag.category))],
            tags_revision: updated.tags_revision } : previous);
          setDirectoryId(updated.directories.length === 1 ? updated.directories[0].id : null);
          onSaved();
        }} />
        <div className="detail-tabs" role="tablist" aria-label="作品关联资料"><button id="assets-tab" role="tab" aria-selected={tab === 'assets'} aria-controls="assets-panel" onClick={() => setTab('assets')}>素材清单 <span>{assets.length}</span></button><button id="metadata-tab" role="tab" aria-selected={tab === 'metadata'} aria-controls="metadata-panel" onClick={() => setTab('metadata')}>历史资料</button></div>
        {tab === 'assets' ? <section id="assets-panel" role="tabpanel" aria-labelledby="assets-tab" className="detail-section assets-panel">{!assets.length && <p className="help-text">该编号目录尚未发现关联素材。</p>}{assetGroups.filter(([, values]) => values.length).map(([label, values]) => <div className="asset-group" key={label}><h4>{label}<span>{values.length}</span></h4><ul>{values.map(asset => <li key={asset.id}>{label === '视频' ? <Film size={17} /> : <FileText size={17} />}<div><strong>{asset.name}</strong><small>{asset.relative_path}{work.directories.length > 1 ? ` · 目录 ${asset.directory_id}` : ''}</small></div><span className="asset-size">{asset.axis && <b>{asset.axis}</b>}{formatSize(asset.size)}</span></li>)}</ul></div>)}</section> : <section id="metadata-panel" role="tabpanel" aria-labelledby="metadata-tab" className="detail-section">{history.length ? <><dl className="metadata-list">{history.map(([label, value]) => { const link = safeLink(value); return <div key={label}><dt>{label}</dt><dd>{link ? <a href={link} target="_blank" rel="noreferrer">{displayValue(value)}</a> : displayValue(value)}</dd></div>; })}</dl><p className="help-text">来自首次导入的历史资料，计划日期不代表实际发布日期。</p></> : <p className="help-text">暂无关联的历史资料。</p>}</section>}
        <form id="work-edit-form" className="detail-section edit-form" onSubmit={event => void save(event)}><div className="section-heading"><FileText size={17} /><h3>作品信息</h3>{dirty && <span className="unsaved-label">未保存</span>}</div><label htmlFor="work-title">标题</label><input id="work-title" type="text" maxLength={500} value={title} onChange={event => setTitle(event.target.value)} disabled={saving} /><div className="release-date-fields"><div><label htmlFor="work-patreon-date">Patreon 发布日期</label><input id="work-patreon-date" type="date" value={patreonDate} onChange={event => setPatreonDate(event.target.value)} disabled={saving || changingStatus} /></div><div><label htmlFor="work-es-date">ES 发布日期</label><input id="work-es-date" type="date" value={esDate} onChange={event => setEsDate(event.target.value)} disabled={saving || changingStatus} /></div></div><p className="help-text">保存对应的帖子链接时，未记录的日期默认填入当天（北京时间）；已有日期保留，也可以在这里修改或清空。</p><label htmlFor="work-notes">备注</label><textarea id="work-notes" rows={4} maxLength={10000} placeholder="记录发布安排、素材说明…" value={notes} onChange={event => setNotes(event.target.value)} disabled={saving} /><p className="help-text">扫描不会覆盖你维护的标题、备注、发布日期和发布状态。</p></form>
      </div>
      <div className="detail-actions">{saveError && <p className="inline-error" role="alert">{saveError}</p>}<div><div className="publication-controls">{(['es', 'patreon'] as const).map(platform => <button key={platform} className="button" disabled={changingStatus || saving} onClick={() => void changeStatus(platform)}>{changingStatus ? <LoaderCircle size={16} className="spin" /> : isPublished(work, platform) ? <Clock3 size={16} /> : <CheckCheck size={16} />}{isPublished(work, platform) ? `将 ${platform === 'es' ? 'ES' : 'Patreon'} 改为待发布` : `标记 ${platform === 'es' ? 'ES' : 'Patreon'} 已发布`}</button>)}</div><button className="button primary" type="submit" form="work-edit-form" disabled={!dirty || saving || changingStatus}>{saving ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}{saving ? '正在保存' : '保存信息'}</button></div></div>
    </>}
    {work && editingTags && <WorkTagEditor work={work} onClose={() => setEditingTags(false)} onSaved={value => { setWork(previous => previous ? { ...previous, tags: value.tags, tags_revision: value.tags_revision } : previous); onSaved(); }} />}
    {work && editingPost && <ReleasePostEditor workId={id} onClose={() => setEditingPost(false)} />}
  </dialog>;
}


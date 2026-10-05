import { translate as t, useI18n, tagName, getLanguage } from './i18n';
import { useEffect, useImperativeHandle, useRef, useState } from 'react';
import type { FormEvent, ReactNode, Ref } from 'react';
import { Archive, ArrowLeft, ArrowRight, Check, CheckCheck, CircleAlert, Clock3, FileText, Film, Folder, FolderOpen, Image, LoaderCircle, RefreshCw, X } from 'lucide-react';
import { displayValue, errorMessage, formatDate, formatSize, historyEntries, isActiveJob, isPublished, jobLabel, request, safeLink } from './api';
import type { Asset, Capabilities, Issue, Job, PublicationPlatform, Settings, Work } from './api';
import type { Notice } from './App';
import { PreviewSection } from './PreviewSection';
import { TagChips, WorkTagEditor } from './Tags';
import { ScanRootsEditor } from './ScanRootsEditor';
import { McpSettings } from './McpSettings';
import { ReleaseDates, workDisplayTitle } from './ReleaseDates';
import { ReleasePostEditor } from './ReleasePosts';
import { SHOW_ES_POSTS } from './features';
import { SettingsSection } from './SettingsSection';

export function StatusBadge({ status }: { status: 'pending' | 'published' }) {
  useI18n(); return <span className={`badge ${status}`}><span className="status-dot" />{status === 'published' ? t("已发布") : t("待发布")}</span>; }
export function PublicationBadges({ work }: { work: Work }) {
  useI18n(); return <span className="publication-badges">{(['es', 'patreon'] as const).map(platform => <span key={platform} className={`badge ${isPublished(work, platform) ? 'published' : 'pending'}`}><span className="status-dot" />{platform === 'es' ? 'ES' : 'Patreon'} {isPublished(work, platform) ? t("已发布") : t("待发布")}</span>)}</span>; }

export function Cover({ work, large = false }: { work: Work; large?: boolean }) {
  useI18n();
  const [failed, setFailed] = useState(false);
  useEffect(() => { setFailed(false); }, [work.cover_url]);
  return <div className={`cover ${large ? 'cover-large' : ''}`}>
    {work.cover_url && !failed ? <img src={work.cover_url} alt={t("{0} 的视频封面", {"0": work.title || work.script_id})} loading={large ? 'eager' : 'lazy'} decoding="async" onError={() => setFailed(true)} /> : <div className="cover-placeholder"><Image size={large ? 32 : 26} strokeWidth={1.4} aria-hidden="true" /><span>{failed ? t("封面暂不可用") : t("暂无视频封面")}</span><small>{work.script_id}</small></div>}
  </div>;
}
export function EmptyState({ title, description, children, icon = <Archive size={30} strokeWidth={1.4} /> }: { title: string; description: string; children?: ReactNode; icon?: ReactNode }) {
  useI18n();
  return <div className="empty-state"><div className="empty-icon" aria-hidden="true">{icon}</div><h3>{title}</h3><p>{description}</p>{children}</div>;
}
export function Loading({ label = t("正在读取库存") }: { label?: string }) {
  useI18n(); return <div className="loading-state" role="status"><LoaderCircle size={20} className="spin" aria-hidden="true" /><span>{label}</span></div>; }
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
function ResourceError({ message, retry }: { message: string; retry: () => void }) {
  useI18n(); return <div className="notice error" role="alert"><span><CircleAlert size={18} />{t("读取失败：")}{t(message)}</span><button className="button small" onClick={retry}>{t("重试")}</button></div>; }
export function IssuesPage({ revision, onSelect }: { revision: number; onSelect: (id: number) => void }) {
  useI18n();
  const { data, error, loading, retry } = useResource<{ items: Issue[]; total: number }>('/api/issues', revision);
  return <>{error && <ResourceError message={error} retry={retry} />}{loading ? <Loading label={t("正在检查待处理项目")} /> : data && !data.items.length ? <EmptyState icon={<CheckCheck size={30} />} title={t("目前没有待处理项")} description={t("编号和素材检查通过，新的异常会在扫描后出现在这里。")} /> : <div className="issue-list">{data?.items.map((issue, index) => <article key={`${issue.work_id}-${issue.type}-${index}`} className="issue-item"><div className="issue-symbol"><CircleAlert size={19} /></div><div className="issue-content"><div className="issue-heading">{issue.script_id && <span className="script-id">{issue.script_id}</span>}<h2>{t(issue.message)}</h2></div>{issue.paths?.length ? <ul className="path-list">{issue.paths.map(path => <li key={path}><Folder size={14} /><code>{path}</code></li>)}</ul> : null}<p className="issue-help">{issue.type.includes('duplicate') || issue.type.includes('conflict') ? t("请检查这些目录的编号，修改文件夹后重新扫描。") : issue.type.includes('history') || issue.type.includes('unmatched') ? t("历史资料尚未关联到完整编号的作品文件夹。") : t("检查原目录与素材，修复后重新扫描。")}</p></div>{issue.work_id != null && <button className="button small" onClick={() => onSelect(issue.work_id!)}>{t("查看作品")}<ArrowRight size={14} /></button>}</article>)}</div>}</>;
}
export function JobsPage({ revision }: { revision: number }) {
  useI18n();
  const { data, error, loading, retry } = useResource<{ items: Job[] }>('/api/jobs', revision, 2500);
  const labels: Record<string, string> = { works: t("库存"), directories: t("编号目录"), assets: t("素材"), unnumbered: t("未编号素材"), unavailable_roots: t("不可用目录"), covers_generated: t("更新封面"), covers_failed: t("封面失败"), refresh_covers: t("重新生成封面"), script_id: t("作品编号"), file_count: t("生成文件"), clip_count: t("片段") };
  return <>{error && <ResourceError message={error} retry={retry} />}{loading ? <Loading label={t("正在读取任务记录")} /> : data && !data.items.length ? <EmptyState icon={<RefreshCw size={30} />} title={t("尚无任务记录")} description={t("执行库存扫描或生成作品预览后，会在这里留下记录。")} /> : <div className="job-list">{data?.items.map(job => <article className="job-card" key={job.id}><div className="job-head"><div className={`job-icon ${job.status === 'failed' ? 'error' : ''}`}>{isActiveJob(job) ? <LoaderCircle size={20} className="spin" /> : job.status === 'failed' ? <CircleAlert size={20} /> : <Check size={20} />}</div><div><h2>{job.type === 'preview' ? t("预览生成") : job.type === 'rematch' ? t("文件重新匹配") : t("库存扫描")} <span className="job-number">#{job.id}</span></h2><p>{formatDate(job.started_at || job.created_at)}{job.finished_at && <> {t("· 完成于")}{formatDate(job.finished_at)}</>}</p></div><span className={`badge ${job.status === 'failed' ? 'warning' : isActiveJob(job) ? 'pending' : 'published'}`}>{jobLabel(job.status, job.type)}</span></div>{job.result && <div className="job-results">{Object.entries(job.result).filter(([key, value]) => value != null && (job.type !== 'preview' || ['script_id', 'file_count', 'clip_count'].includes(key))).map(([key, value]) => <span key={key}><span>{labels[key] || key}</span><strong>{displayValue(value)}</strong></span>)}</div>}{job.error && <p className="inline-error" role="alert">{t(job.error)}</p>}{isActiveJob(job) && <p className="help-text">{job.type === 'preview' ? (job.message ? t(job.message) : '') || t("正在后台生成预览，关闭网页不会中断任务。") : job.type === 'rematch' ? (job.message ? t(job.message) : '') || t("正在重新匹配作品素材，关闭网页不会中断任务。") : t("正在读取文件与生成封面，关闭网页不会中断扫描。")}</p>}</article>)}</div>}</>;
}
export function SettingsPage({ capabilities, revision, collapsible = false }: { capabilities: Capabilities; revision: number; collapsible?: boolean }) {
  useI18n();
  const { data, error, loading, retry } = useResource<Settings>('/api/settings', revision);
  return <>{error && <ResourceError message={error} retry={retry} />}{loading ? <Loading label={t("正在读取运行设置")} /> : data && <div className="settings-layout">
    <ScanRootsEditor settings={data} collapsible={collapsible} /><McpSettings collapsible={collapsible} />
    <SettingsSection title={t('扫描与库存规则')} icon={<RefreshCw size={19} aria-hidden="true" />} collapsible={collapsible}>
      <dl className="settings-details"><dt>{t('扫描方式')}</dt><dd>{t('手动扫描，点击“立即扫描”更新库存')}</dd><dt>{t('编号来源')}</dt><dd>{t('文件夹编号，保留完整子编号')}</dd><dt>{t('发布状态')}</dt><dd>{t('ES 与 Patreon 分别维护待发布 / 已发布')}</dd><dt>{t('未编号素材')}</dt><dd>{t('不计入完成库存')}</dd><dt>{t('冲突处理')}</dt><dd>{t('保留全部路径并提醒，不自动覆盖')}</dd></dl>
    </SettingsSection>
    <SettingsSection title={t('打开文件夹')} icon={<FolderOpen size={19} aria-hidden="true" />} collapsible={collapsible}>
      <p className="host-ability">{capabilities.can_open_folder ? t('当前访问端支持打开素材主机的文件夹。') : (capabilities.reason ? t(capabilities.reason) : '') || t('不支持打开，仅素材所在主机可用。')}</p><p className="help-text">{t('其他客户端可以查看封面、管理库存与发布状态。')}</p>
    </SettingsSection>
  </div>}</>;
}

export interface WorkDetailHandle { requestLeave(next: () => void): void }
export function WorkDetail({ id, capabilities, onClose, onSaved, notify, presentation = 'dialog', ref }: { id: number; capabilities: Capabilities; onClose: () => void; onSaved: () => void; notify: (notice: Notice) => void; presentation?: 'dialog' | 'page'; ref?: Ref<WorkDetailHandle> }) {
  const { locale } = useI18n();
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
  const [confirmingProduction, setConfirmingProduction] = useState(false);
  const [productionError, setProductionError] = useState('');
  const [productionTasks, setProductionTasks] = useState({ loading: true, active: false, error: '' });
  const productionLock = useRef(false);
  const alive = useRef(true);
  const [retry, setRetry] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const pendingLeave = useRef<(() => void) | null>(null);
  const continueEditing = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const dirty = !!work && (title !== work.title || notes !== (work.notes || '') || esDate !== (work.es_published_date || '') || patreonDate !== (work.patreon_published_date || ''));
  useEffect(() => {
    if (presentation !== 'page' || !(dirty || matchingDirty)) return;
    const protectDraft = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', protectDraft);
    return () => window.removeEventListener('beforeunload', protectDraft);
  }, [presentation, dirty, matchingDirty]);
  useEffect(() => {
    if (presentation !== 'page') return;
    const pageTitle = `${!loading && !error && work ? work.script_id : t('作品详情')} · ${t('Funscript 工作台')}`;
    document.title = pageTitle;
    return () => {
      if (document.title === pageTitle) document.title = t('Funscript 工作台', {}, getLanguage().language);
    };
  }, [presentation, locale, loading, error, work?.script_id]);
  const leaveBlocked = saving || changingStatus || opening || confirmingProduction || editingTags || editingPost;
  const requestLeave = (next: () => void) => {
    if (leaveBlocked) return;
    if (dirty || matchingDirty) { pendingLeave.current = next; setConfirmClose(true); return; }
    pendingLeave.current = null; next();
  };
  useImperativeHandle(ref, () => ({ requestLeave }));
  const close = () => requestLeave(onClose);
  const discard = () => {
    if (leaveBlocked) return;
    const next = pendingLeave.current || onClose;
    pendingLeave.current = null; setConfirmClose(false); next();
  };
  useEffect(() => { if (confirmClose) continueEditing.current?.focus(); }, [confirmClose]);
  useEffect(() => {
    if (presentation === 'page') return;
    dialog.current?.showModal();
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previous; };
  }, [presentation]);
  useEffect(() => {
    if (presentation === 'page' && !loading) heading.current?.focus({ preventScroll: true });
  }, [presentation, loading, work?.script_id]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    request<Work>(`/api/works/${id}`, { signal: controller.signal }).then(result => { setWork(result); setTitle(result.title); setNotes(result.notes || ''); setEsDate(result.es_published_date || ''); setPatreonDate(result.patreon_published_date || ''); setDirectoryId(result.directories.length === 1 ? result.directories[0].id : null); }).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [id, retry]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!work?.production_required) return;
    let disposed = false;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    setProductionTasks({ loading: true, active: false, error: '' });
    const load = async () => {
      controller = new AbortController();
      try {
        const result = await request<{ items: Job[] }>('/api/jobs', { signal: controller.signal });
        if (!disposed) setProductionTasks({ loading: false, active: (result.items || []).some(job => ['scan', 'rematch', 'preview'].includes(job.type) && isActiveJob(job)), error: '' });
      } catch (error) {
        if (!disposed && !controller.signal.aborted) setProductionTasks({ loading: false, active: false, error: errorMessage(error) });
      } finally { if (!disposed) timer = setTimeout(() => void load(), 2500); }
    };
    void load();
    return () => { disposed = true; controller?.abort(); if (timer) clearTimeout(timer); };
  }, [id, work?.production_required]);
  const confirmProduction = async () => {
    if (!work?.production_required || productionLock.current || saving || changingStatus || matchingDirty || productionTasks.loading || productionTasks.active || productionTasks.error || !work.script_count || !work.directories.some(directory => directory.id === directoryId && directory.available)) return;
    productionLock.current = true; setConfirmingProduction(true); setProductionError('');
    try {
      const updated = await request<Work>(`/api/works/${id}/production/confirm`, { method: 'POST', body: JSON.stringify({ expected_revision: work.production_revision ?? 0 }) });
      if (alive.current) { setWork(updated); setDirectoryId(updated.directories.length === 1 ? updated.directories[0].id : null); onSaved(); notify({ kind: 'success', message: t('已确认 {id} 制作完成', { id: updated.script_id }) }); }
    } catch (error) { if (alive.current) setProductionError(errorMessage(error)); }
    finally { productionLock.current = false; if (alive.current) setConfirmingProduction(false); }
  };
  const changeStatus = async (platform: PublicationPlatform) => {
    if (!work || changingStatus || saving || productionLock.current) return;
    setChangingStatus(true); setSaveError('');
    try { const updated = await request<Work>(`/api/works/${id}`, { method: 'PATCH', body: JSON.stringify({ [`${platform}_published`]: !isPublished(work, platform) }) }); setWork(updated); onSaved(); notify({ kind: 'success', message: t("{0} 的 {1} 已设为{2}", {"0": work.script_id, "1": platform === 'es' ? 'ES' : 'Patreon', "2": isPublished(updated, platform) ? t("已发布") : t("待发布")}) }); }
    catch (error) { setSaveError(t("发布状态未更新：{0}", {"0": errorMessage(error)})); }
    finally { setChangingStatus(false); }
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!work || !dirty || productionLock.current || saving || changingStatus) return;
    if (!title.trim()) { setSaveError(t("请填写作品标题。")); return; }
    setSaving(true); setSaveError('');
    try { const updated = await request<Work>(`/api/works/${id}`, { method: 'PATCH', body: JSON.stringify({ title: title.trim(), notes, ...(esDate !== (work.es_published_date || '') ? { es_published_date: esDate || null } : {}), ...(patreonDate !== (work.patreon_published_date || '') ? { patreon_published_date: patreonDate || null } : {}) }) }); setWork(updated); setTitle(updated.title); setNotes(updated.notes || ''); setEsDate(updated.es_published_date || ''); setPatreonDate(updated.patreon_published_date || ''); onSaved(); notify({ kind: 'success', message: t("{0} 的作品信息已保存", {"0": updated.script_id}) }); }
    catch (error) { setSaveError(t("修改未保存：{0}", {"0": errorMessage(error)})); }
    finally { setSaving(false); }
  };
  const open = async () => {
    if (!work || !capabilities.can_open_folder || !directoryId) return;
    setOpening(true); setSaveError('');
    try { const result = await request<{ message: string }>(`/api/works/${id}/open-folder`, { method: 'POST', body: JSON.stringify({ directory_id: directoryId }) }); notify({ kind: 'success', message: result.message }); }
    catch (error) { setSaveError(t("无法打开文件夹：{0}", {"0": errorMessage(error)})); }
    finally { setOpening(false); }
  };
  const currentDir = work?.directories.find(directory => directory.id === directoryId);
  const assets = work?.assets || [];
  const history = historyEntries(work?.metadata);
  const assetGroups: [string, Asset[]][] = [[t("视频"), assets.filter(asset => asset.kind === 'video')], [t("脚本"), assets.filter(asset => asset.kind === 'script' || asset.kind === 'funscript')], [t("辅助素材"), assets.filter(asset => !['video', 'script', 'funscript'].includes(asset.kind))]];
  const content = <>
    <div className="detail-header"><div><span className="section-label">{t("作品详情")}</span>{presentation === 'page' ? <h1 id="detail-title" ref={heading} tabIndex={-1}>{work?.script_id || t("正在读取")}</h1> : <h2 id="detail-title">{work?.script_id || t("正在读取")}</h2>}</div>{presentation === 'page' ? <button className="button detail-back" ref={closeButton} onClick={close} disabled={leaveBlocked}><ArrowLeft size={17} />{t('返回库存')}</button> : <button className="icon-button" ref={closeButton} onClick={close} aria-label={t("关闭作品详情")} disabled={leaveBlocked} autoFocus><X size={21} /></button>}</div>
    {confirmClose && <div className="discard-confirm" role="alert"><strong>{t("有尚未保存的修改")}</strong><p>{t(presentation === 'page' ? '离开后将放弃尚未保存的标题、备注、发布日期或脚本对应关系。' : "关闭后将放弃尚未保存的标题、备注、发布日期或脚本对应关系。")}</p><div><button className="button small" ref={continueEditing} onClick={() => { pendingLeave.current = null; setConfirmClose(false); closeButton.current?.focus(); }}>{t("继续编辑")}</button><button className="button small" onClick={discard} disabled={leaveBlocked}>{t(presentation === 'page' ? '放弃更改并离开' : "放弃更改并关闭")}</button></div></div>}
    {loading ? <Loading label={t("正在读取作品详情")} /> : error ? <div className="detail-body"><ResourceError message={error} retry={() => setRetry(value => value + 1)} /></div> : work && <>
      <div className="detail-body"><Cover work={work} large /><div className="detail-heading">{workDisplayTitle(work) && <h3>{workDisplayTitle(work)}</h3>}<PublicationBadges work={work} /></div><ReleaseDates work={work} /><div className="detail-summary"><span><Film size={15} />{work.video_count} {t("个视频", { count: work.video_count })}</span><span><FileText size={15} />{work.script_count} {t("个脚本", { count: work.script_count })}</span>{work.axis_type && <span>{tagName({ category: 'axis_type', name: work.axis_type })}</span>}</div>
        {work.issues.length > 0 && <div className="detail-issues">{work.issues.map((issue, index) => <p key={`${issue.type}-${index}`}><CircleAlert size={16} /><span>{t(issue.message)}</span></p>)}</div>}
        {work.production_required && <section className="detail-section" aria-labelledby="production-confirmation-title">
          <div className="section-heading"><CheckCheck size={17} /><h3 id="production-confirmation-title">{t('制作确认')}</h3><span className="badge pending">{t('待确认制作完成')}</span></div>
          <p className="help-text">{t('发现脚本不会自动确认完成；请检查当前素材后手动确认。')}</p>
          {!work.script_count ? <p className="help-text">{t('尚无可用脚本，请添加脚本后扫描或重新匹配文件。')}</p> : !currentDir?.available ? <p className="help-text">{t('当前作品目录不可用，请恢复目录后扫描或重新匹配文件。')}</p> : matchingDirty ? <p className="help-text">{t('请先保存或放弃尚未保存的脚本对应关系。')}</p> : productionTasks.active ? <p className="help-text">{t('后台任务正在运行，请等待扫描、匹配或预览完成后确认。')}</p> : null}
          {productionTasks.error && <p className="inline-error" role="alert">{t('无法检查后台任务：{error}', { error: t(productionTasks.error) })}</p>}
          {productionError && <p className="inline-error" role="alert">{t('制作完成未确认：{error}', { error: t(productionError) })}</p>}
          <button type="button" className="button primary" disabled={confirmingProduction || saving || changingStatus || matchingDirty || productionTasks.loading || productionTasks.active || !!productionTasks.error || !work.script_count || !currentDir?.available} onClick={() => void confirmProduction()}>
            {confirmingProduction || productionTasks.loading ? <LoaderCircle size={17} className="spin" /> : <CheckCheck size={17} />}{confirmingProduction ? t('正在确认制作完成') : t('确认制作完成')}
          </button>
        </section>}
        <section className="detail-section"><div className="section-heading"><Folder size={17} /><h3>{t("作品目录")}</h3></div><div className="directory-options">{currentDir && <div className="directory-option selected"><Folder size={17} /><span><code>{currentDir.windows_path || currentDir.path}</code>{!currentDir.available && <small>{t("目录暂不可用")}</small>}</span></div>}</div>{!currentDir && <p className="help-text">{work.issues.some(issue => issue.type === 'duplicate_identifier') ? t("编号存在目录冲突，请处理冲突后重新匹配文件。") : t("尚无可关联的本地目录，请重新匹配文件。")}</p>}
          <button className="button open-folder" onClick={() => void open()} disabled={!capabilities.can_open_folder || !directoryId || !currentDir?.available || opening}>{opening ? <LoaderCircle className="spin" size={17} /> : <FolderOpen size={17} />}{opening ? t("正在发送打开请求") : t("打开文件夹")}</button><p className="host-note">{!capabilities.can_open_folder ? capabilities.reason || t("不支持打开，仅素材所在主机可用") : !currentDir ? t("暂无唯一的关联目录。") : !currentDir.available ? t("当前目录暂不可用，请检查原文件夹。") : t("在素材所在 Windows 主机的资源管理器中打开。")}</p></section>
        <section className="detail-section"><div className="section-heading"><h3>{t("作品标签")}</h3><button className="button small detail-edit-tags" onClick={() => setEditingTags(true)}>{t("编辑标签")}</button></div><TagChips tags={work.tags} durationStatus={work.duration_status} durationError={work.duration_error} /></section>
        {SHOW_ES_POSTS && <section className="detail-section"><div className="section-heading"><FileText size={17} /><h3>{t("ES 贴文")}</h3></div><p className="help-text">{t("根据作品标签和发布链接套用模板，近期作品预览每次生成时自动更新。上传 Markdown 和生成稿保存在数据库中。")}</p><button className="button primary" onClick={() => setEditingPost(true)}><FileText size={17} />{t("生成贴文")}</button>{dirty && <p className="help-text">{t("贴文使用已保存的作品信息；修改标题后请先保存。")}</p>}</section>}
        <PreviewSection work={work} capabilities={capabilities} onDirtyChange={setMatchingDirty} onSourcesChanged={async () => {
          const updated = await request<Work>(`/api/works/${id}`);
          // Refresh discovered source data while retaining locally edited text and tags.
          setWork(previous => previous ? { ...previous, production_required: updated.production_required, production_confirmed_at: updated.production_confirmed_at, production_revision: updated.production_revision, assets: updated.assets, directories: updated.directories, video_count: updated.video_count, script_count: updated.script_count, cover_url: updated.cover_url, issues: updated.issues, axis_type: updated.axis_type,
            duration_seconds: updated.duration_seconds, duration_minutes: updated.duration_minutes,
            duration_status: updated.duration_status, duration_error: updated.duration_error,
            duration_last_known_seconds: updated.duration_last_known_seconds, duration_last_known_minutes: updated.duration_last_known_minutes,
            tags: [...(previous.tags || []).filter(tag => !['axis_type', 'duration'].includes(tag.category)), ...(updated.tags || []).filter(tag => ['axis_type', 'duration'].includes(tag.category))],
            tags_revision: updated.tags_revision } : previous);
          setDirectoryId(updated.directories.length === 1 ? updated.directories[0].id : null);
          onSaved();
        }} />
        <div className="detail-tabs" role="tablist" aria-label={t("作品关联资料")}><button id="assets-tab" role="tab" aria-selected={tab === 'assets'} aria-controls="assets-panel" onClick={() => setTab('assets')}>{t("素材清单")}<span>{assets.length}</span></button><button id="metadata-tab" role="tab" aria-selected={tab === 'metadata'} aria-controls="metadata-panel" onClick={() => setTab('metadata')}>{t("历史资料")}</button></div>
        {tab === 'assets' ? <section id="assets-panel" role="tabpanel" aria-labelledby="assets-tab" className="detail-section assets-panel">{!assets.length && <p className="help-text">{t("该编号目录尚未发现关联素材。")}</p>}{assetGroups.filter(([, values]) => values.length).map(([label, values]) => <div className="asset-group" key={label}><h4>{label}<span>{values.length}</span></h4><ul>{values.map(asset => <li key={asset.id}>{label === t("视频") ? <Film size={17} /> : <FileText size={17} />}<div><strong>{asset.name}</strong><small>{asset.relative_path}{work.directories.length > 1 ? t(" · 目录 {0}", {"0": asset.directory_id}) : ''}</small></div><span className="asset-size">{asset.axis && <b>{asset.axis}</b>}{formatSize(asset.size)}</span></li>)}</ul></div>)}</section> : <section id="metadata-panel" role="tabpanel" aria-labelledby="metadata-tab" className="detail-section">{history.length ? <><dl className="metadata-list">{history.map(([label, value]) => { const link = safeLink(value); return <div key={label}><dt>{label}</dt><dd>{link ? <a href={link} target="_blank" rel="noreferrer">{displayValue(value)}</a> : displayValue(value)}</dd></div>; })}</dl><p className="help-text">{t("来自首次导入的历史资料，计划日期不代表实际发布日期。")}</p></> : <p className="help-text">{t("暂无关联的历史资料。")}</p>}</section>}
        <form id="work-edit-form" className="detail-section edit-form" onSubmit={event => void save(event)}><div className="section-heading"><FileText size={17} /><h3>{t("作品信息")}</h3>{dirty && <span className="unsaved-label">{t("未保存")}</span>}</div><label htmlFor="work-title">{t("标题")}</label><input id="work-title" type="text" maxLength={500} value={title} onChange={event => setTitle(event.target.value)} disabled={saving} /><div className="release-date-fields"><div><label htmlFor="work-patreon-date">{t("Patreon 发布日期")}</label><input id="work-patreon-date" type="date" value={patreonDate} onChange={event => setPatreonDate(event.target.value)} disabled={saving || changingStatus} /></div><div><label htmlFor="work-es-date">{t("ES 发布日期")}</label><input id="work-es-date" type="date" value={esDate} onChange={event => setEsDate(event.target.value)} disabled={saving || changingStatus} /></div></div><p className="help-text">{t("保存对应的帖子链接时，未记录的日期默认填入当天（北京时间）；已有日期保留，也可以在这里修改或清空。")}</p><label htmlFor="work-notes">{t("备注")}</label><textarea id="work-notes" rows={4} maxLength={10000} placeholder={t("记录发布安排、素材说明…")} value={notes} onChange={event => setNotes(event.target.value)} disabled={saving} /><p className="help-text">{t("扫描不会覆盖你维护的标题、备注、发布日期和发布状态。")}</p></form>
      </div>
      <div className="detail-actions">{saveError && <p className="inline-error" role="alert">{t(saveError)}</p>}<div><div className="publication-controls">{(['es', 'patreon'] as const).map(platform => <button key={platform} className="button" disabled={changingStatus || saving || confirmingProduction} onClick={() => void changeStatus(platform)}>{changingStatus ? <LoaderCircle size={16} className="spin" /> : isPublished(work, platform) ? <Clock3 size={16} /> : <CheckCheck size={16} />}{isPublished(work, platform) ? t("将 {0} 改为待发布", {"0": platform === 'es' ? 'ES' : 'Patreon'}) : t("标记 {0} 已发布", {"0": platform === 'es' ? 'ES' : 'Patreon'})}</button>)}</div><button className="button primary" type="submit" form="work-edit-form" disabled={!dirty || saving || changingStatus || confirmingProduction}>{saving ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}{saving ? t("正在保存") : t("保存信息")}</button></div></div>
    </>}
    {work && editingTags && <WorkTagEditor work={work} onClose={() => setEditingTags(false)} onSaved={value => { setWork(previous => previous ? { ...previous, tags: value.tags, tags_revision: value.tags_revision } : previous); onSaved(); }} />}
    {SHOW_ES_POSTS && work && editingPost && <ReleasePostEditor workId={id} onClose={() => setEditingPost(false)} />}
  </>;
  return presentation === 'page' ? <article className="detail-page" aria-labelledby="detail-title">{content}</article> : <dialog className="detail-dialog" ref={dialog} aria-labelledby="detail-title" onCancel={event => { event.preventDefault(); close(); }} onClick={event => { if (event.target === event.currentTarget) { const rect = event.currentTarget.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close(); } }}>{content}</dialog>;
}


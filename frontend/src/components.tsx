import { workIdentity } from './api';
import { translate as t, useI18n, tagName, getLanguage } from './i18n';
import { useEffect, useImperativeHandle, useRef, useState } from 'react';
import type { FormEvent, ReactNode, Ref } from 'react';
import { Archive, ArrowLeft, ArrowRight, Check, CheckCheck, CircleAlert, ChevronDown, Search, SlidersHorizontal, Clock3, FileText, Film, Folder, FolderOpen, Image, LoaderCircle, RefreshCw, X } from 'lucide-react';
import { displayValue, errorMessage, formatDate, formatSize, historyEntries, isActiveJob, isPublished, jobLabel, request, safeLink } from './api';
import type { Asset, Capabilities, Issue, Job, PublicationPlatform, Settings, Work } from './api';
import type { Notice } from './App';
import { PreviewSection } from './PreviewSection';
import { TagChips, WorkTagEditor } from './Tags';
import { ScanRootsEditor } from './ScanRootsEditor';
import { McpSettings } from './McpSettings';
import { workDisplayTitle } from './ReleaseDates';
import { ReleasePostEditor } from './ReleasePosts';
import { SHOW_ES_POSTS } from './features';
import { SettingsSection } from './SettingsSection';
import { motionIsReduced } from './Preferences';
import { ScanCandidates } from './ScanCandidates';
import { WorkLinkButtons, WorkLinkEditor } from './WorkLinks';
import type { WorkLinkKind } from './api';
import './DetailPanels.css';

export function StatusBadge({ status }: { status: 'pending' | 'published' }) {
  useI18n(); return <span className={`badge ${status}`}><span className="status-dot" />{status === 'published' ? t("已发布") : t("待发布")}</span>; }
export function PublicationBadges({ work }: { work: Work }) {
  useI18n();
  return <span className="publication-badges">{(['es', 'patreon'] as const).map(platform => {
    const date = work[platform + '_published_date' as 'es_published_date' | 'patreon_published_date'];
    const label = platform === 'es' ? 'ES' : 'Patreon';
    const description = label + ' · ' + t(isPublished(work, platform) ? '已发布' : '待发布') + (date ? ' · ' + date : '');
    return <span key={platform} className={'badge ' + (isPublished(work, platform) ? 'published' : 'pending')} aria-label={description} title={description}>{label}{date && <> · <time dateTime={date}>{date.slice(5).replace('-', '/')}</time></>}</span>;
  })}</span>;
}

export function Cover({ work, large = false }: { work: Work; large?: boolean }) {
  useI18n();
  const [failed, setFailed] = useState(false);
  useEffect(() => { setFailed(false); }, [work.cover_url]);
  const seconds = work.duration_seconds;
  const duration = typeof seconds === 'number' && Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds / 60) + ':' + String(Math.floor(seconds % 60)).padStart(2, '0') : null;
  return <div className={'cover ' + (large ? 'cover-large' : '')}>
    {work.cover_url && !failed ? <img src={work.cover_url} alt={t("{0} 的视频封面", {"0": work.title || workIdentity(work)})} loading={large ? 'eager' : 'lazy'} decoding="async" onError={() => setFailed(true)} /> : <div className="cover-placeholder"><Image size={large ? 32 : 26} strokeWidth={1.4} aria-hidden="true" /><span>{failed ? t("封面暂不可用") : t("暂无视频封面")}</span><small>{workIdentity(work)}</small></div>}
    {duration && <span className="cover-duration" aria-label={t('时长')}>{duration}</span>}
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
export function IssuesPage({ revision, onSelect, onChanged = () => {} }: { revision: number; onSelect: (id: number) => void; onChanged?: () => void }) {
  useI18n();
  const [candidateCount, setCandidateCount] = useState<number | null>(null);
  const { data, error, loading, retry } = useResource<{ items: Issue[]; total: number }>('/api/issues', revision);
  return <><ScanCandidates revision={revision} onChanged={onChanged} onSelect={onSelect} onCount={setCandidateCount} />{error && <ResourceError message={error} retry={retry} />}{loading ? <Loading label={t("正在检查待处理项目")} /> : data && !data.items.length ? candidateCount === 0 && <EmptyState icon={<CheckCheck size={30} />} title={t("目前没有待处理项")} description={t("编号和素材检查通过，新的异常会在扫描后出现在这里。")} /> : <div className="issue-list">{data?.items.map((issue, index) => <article key={`${issue.work_id}-${issue.type}-${index}`} className="issue-item"><div className="issue-symbol"><CircleAlert size={19} /></div><div className="issue-content"><div className="issue-heading">{(issue.script_id || issue.work_id) && <span className="script-id">{workIdentity({ work_id: issue.work_id, script_id: issue.script_id })}</span>}<h2>{t(issue.message)}</h2></div>{issue.paths?.length ? <ul className="path-list">{issue.paths.map(path => <li key={path}><Folder size={14} /><code>{path}</code></li>)}</ul> : null}<p className="issue-help">{issue.type.includes('duplicate') || issue.type.includes('conflict') ? t("请检查这些目录的编号，修改文件夹后重新扫描。") : issue.type.includes('history') || issue.type.includes('unmatched') ? t("历史资料尚未关联到完整编号的作品文件夹。") : t("检查原目录与素材，修复后重新扫描。")}</p></div>{issue.work_id != null && <button className="button small" onClick={() => onSelect(issue.work_id!)}>{t("查看作品")}<ArrowRight size={14} /></button>}</article>)}</div>}</>;
}
export function JobsPage({ revision }: { revision: number }) {
  useI18n();
  const { data, error, loading, retry } = useResource<{ items: Job[] }>('/api/jobs', revision, 2500);
  const [status, setStatus] = useState('all');
  const [kind, setKind] = useState('all');
  const [query, setQuery] = useState('');
  const [expanded, setExpanded] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const labels: Record<string, string> = { works: t("库存"), directories: t("编号目录"), assets: t("素材"), unnumbered: t("未编号素材"), unavailable_roots: t("不可用目录"), covers_generated: t("更新封面"), covers_failed: t("封面失败"), refresh_covers: t("重新生成封面"), script_id: t("作品编号"), file_count: t("生成文件"), clip_count: t("片段") };
  const typeName = (type: string) => t(type === 'preview' ? '预览生成' : type === 'rematch' ? '文件重新匹配' : type === 'scan' ? '库存扫描' : type);
  const group = (job: Job) => isActiveJob(job) ? 'active' : ['completed', 'succeeded'].includes(job.status) ? 'completed' : job.status === 'failed' ? 'failed' : 'other';
  const jobs = (data?.items || []).filter(job => (status === 'all' || group(job) === status) && (kind === 'all' || job.type === kind) && [job.id, typeName(job.type), jobWorkIdentity(job), job.message, job.error].join(' ').toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())).sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id);
  const pages = Math.max(1, Math.ceil(jobs.length / 15));
  const currentPage = Math.min(page, pages);
  return <div className="jobs-workbench">{error && <ResourceError message={error} retry={retry} />}
    <div className="jobs-toolbar"><label className="search-field"><Search size={17} aria-hidden="true" /><input value={query} aria-label={t('搜索任务')} placeholder={t('搜索任务、编号或错误…')} onChange={event => { setQuery(event.target.value); setPage(1); }} /></label><label className="job-type-filter"><SlidersHorizontal size={16} aria-hidden="true" /><span className="sr-only">{t('任务类型')}</span><select aria-label={t('任务类型')} value={kind} onChange={event => { setKind(event.target.value); setPage(1); }}><option value="all">{t('全部类型')}</option>{[...new Set((data?.items || []).map(job => job.type))].map(type => <option key={type} value={type}>{typeName(type)}</option>)}</select></label><button className="button small" onClick={retry}><RefreshCw size={16} aria-hidden="true" />{t('刷新')}</button></div>
    <div className="jobs-status-filter" role="group" aria-label={t('任务状态')}>{([['all', '全部'], ['active', '进行中'], ['completed', '已完成'], ['failed', '失败'], ['other', '其他']] as const).map(([value, label]) => <button type="button" key={value} className={status === value ? 'selected' : ''} aria-pressed={status === value} onClick={() => { setStatus(value); setPage(1); }}>{t(label)}<span>{(data?.items || []).filter(job => value === 'all' || group(job) === value).length}</span></button>)}</div>
    {loading ? <Loading label={t("正在读取任务记录")} /> : !jobs.length ? <EmptyState icon={<RefreshCw size={30} />} title={t(data?.items.length ? '没有匹配的任务' : "尚无任务记录")} description={t(data?.items.length ? '调整筛选或搜索后再试。' : "执行库存扫描或生成作品预览后，会在这里留下记录。")} /> : <div className="job-list compact-job-list">{jobs.slice((currentPage - 1) * 15, currentPage * 15).map(job => {
      const isOpen = expanded === job.id;
      const progress = typeof job.progress === 'number' && Number.isFinite(job.progress) ? Math.min(100, Math.max(0, job.progress)) : null;
      return <article className={'job-card compact-job ' + (isOpen ? 'expanded' : '')} key={job.id}>
        <button className="job-summary" type="button" aria-expanded={isOpen} aria-controls={'job-' + job.id} onClick={() => setExpanded(isOpen ? null : job.id)}>
          <span className={'job-icon ' + (job.status === 'failed' ? 'error' : '')}>{isActiveJob(job) ? <LoaderCircle size={19} className="spin" aria-hidden="true" /> : job.status === 'failed' ? <CircleAlert size={19} aria-hidden="true" /> : <Check size={19} aria-hidden="true" />}</span>
          <span className="job-summary-name"><strong>{typeName(job.type)} <span className="job-number">#{job.id}</span>{jobWorkIdentity(job) && <span className="job-work-name"> · {jobWorkIdentity(job)}</span>}</strong><small>{formatDate(job.started_at || job.created_at)}{job.message && <> · {t(job.message)}</>}</small></span>
          <span className={'badge ' + (job.status === 'failed' ? 'warning' : isActiveJob(job) ? 'pending' : 'published')}>{jobLabel(job.status, job.type)}</span>
          {isActiveJob(job) && progress !== null && <span className="job-progress" role="progressbar" aria-label={typeName(job.type)} aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}><span style={{ width: progress + '%' }} /></span>}
          <ChevronDown size={17} className="job-expand-chevron" aria-hidden="true" />
        </button>
        {isOpen && <section className="job-expanded-content" id={'job-' + job.id} aria-label={typeName(job.type) + ' #' + job.id}>
          <dl className="job-facts"><div><dt>{t('创建时间')}</dt><dd>{formatDate(job.created_at)}</dd></div>{job.started_at && <div><dt>{t('开始时间')}</dt><dd>{formatDate(job.started_at)}</dd></div>}{job.finished_at && <div><dt>{t('完成时间')}</dt><dd>{formatDate(job.finished_at)}</dd></div>}</dl>
          {job.result && <div className="job-results">{Object.entries(job.result).filter(([, value]) => value != null).map(([key, value]) => <span key={key}><span>{labels[key] || key}</span><strong>{displayValue(value)}</strong></span>)}</div>}
          {job.error && <p className="inline-error" role="alert">{t(job.error)}</p>}
          {job.inputs && Object.keys(job.inputs).length > 0 && <details className="job-inputs"><summary>{t('任务参数')}</summary><pre>{JSON.stringify(job.inputs, null, 2)}</pre></details>}
          {isActiveJob(job) && <p className="help-text">{t(job.type === 'preview' ? "正在后台生成预览，关闭网页不会中断任务。" : job.type === 'rematch' ? "正在重新匹配作品素材，关闭网页不会中断任务。" : "正在读取文件与生成封面，关闭网页不会中断扫描。")}</p>}
        </section>}
      </article>;
    })}</div>}
    {jobs.length > 15 && <div className="jobs-pagination"><button className="button small" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}><ArrowLeft size={15} />{t('上一页')}</button><span>{currentPage} / {pages}</span><button className="button small" disabled={currentPage === pages} onClick={() => setPage(currentPage + 1)}>{t('下一页')}<ArrowRight size={15} /></button></div>}
  </div>;
}
function jobWorkIdentity(job: Job) {
  const data = { ...job.inputs, ...job.result };
  if (!data || ![data.script_id, data.title].some(value => typeof value === 'string' && value.trim()) && typeof data.work_id !== 'number') return null;
  return workIdentity({ work_id: typeof data.work_id === 'number' ? data.work_id : undefined, script_id: typeof data.script_id === 'string' ? data.script_id : null, title: typeof data.title === 'string' ? data.title : null });
}
export function SettingsPage({ capabilities, revision, collapsible = false, section = 'all' }: { capabilities: Capabilities; revision: number; collapsible?: boolean; section?: 'library' | 'processing' | 'integrations' | 'all' }) {
  useI18n();
  const { data, error, loading, retry } = useResource<Settings>('/api/settings', revision);
  return <>{error && <ResourceError message={error} retry={retry} />}{loading ? <Loading label={t("正在读取运行设置")} /> : data && <div className="settings-layout">
    <div hidden={section !== 'all' && section !== 'library'}><ScanRootsEditor settings={data} collapsible={collapsible} /></div>
    <div hidden={section !== 'all' && section !== 'integrations'}><McpSettings collapsible={collapsible} /></div>
    <div hidden={section !== 'all' && section !== 'processing'}><SettingsSection title={t('扫描与库存规则')} icon={<RefreshCw size={19} aria-hidden="true" />} collapsible={collapsible}>
      <dl className="settings-details"><dt>{t('扫描方式')}</dt><dd>{t('手动扫描，点击“立即扫描”更新库存')}</dd><dt>{t('作品识别')}</dt><dd>{typeof data.folder_identifier_rule === 'string' ? t(data.folder_identifier_rule) : t('按扫描目录的编号或文件夹模式识别；无编号作品保持独立身份。')}</dd><dt>{t('发布状态')}</dt><dd>{t('ES 与 Patreon 分别维护待发布 / 已发布')}</dd><dt>{t('未编号素材')}</dt><dd>{t('文件夹模式支持无编号子目录；根目录散放文件不单独建档。')}</dd><dt>{t('冲突处理')}</dt><dd>{t('保留全部路径并提醒，不自动覆盖')}</dd></dl>
    </SettingsSection></div>
    <div hidden={section !== 'all' && section !== 'library'}><SettingsSection title={t('打开文件夹')} icon={<FolderOpen size={19} aria-hidden="true" />} collapsible={collapsible}>
      <p className="host-ability">{capabilities.can_open_folder ? t('当前访问端支持打开素材主机的文件夹。') : (capabilities.reason ? t(capabilities.reason) : '') || t('不支持打开，仅素材所在主机可用。')}</p><p className="help-text">{t('其他客户端可以查看封面、管理库存与发布状态。')}</p>
    </SettingsSection></div>
  </div>}</>;
}
export interface WorkDetailHandle { requestLeave(next: () => void): void }
export function WorkDetail({ id, capabilities, onClose, onSaved, notify, presentation = 'dialog', backLabel = '返回库存', ref }: { id: number; capabilities: Capabilities; onClose: () => void; onSaved: () => void; notify: (notice: Notice) => void; presentation?: 'dialog' | 'page'; backLabel?: string; ref?: Ref<WorkDetailHandle> }) {
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
  const [editingLink, setEditingLink] = useState<WorkLinkKind | null>(null);
  const [refreshingPublication, setRefreshingPublication] = useState(false);
  const [matchingDirty, setMatchingDirty] = useState(false);
  const [confirmingProduction, setConfirmingProduction] = useState(false);
  const [resettingProduction, setResettingProduction] = useState(false);
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
    const pageTitle = `${!loading && !error && work ? workIdentity(work) : t('作品详情')} · ${t('Funscript 工作台')}`;
    document.title = pageTitle;
    return () => {
      if (document.title === pageTitle) document.title = t('Funscript 工作台', {}, getLanguage().language);
    };
  }, [presentation, locale, loading, error, work?.script_id, work?.title]);
  const leaveBlocked = saving || changingStatus || opening || confirmingProduction || resettingProduction || editingTags || editingPost || editingLink !== null || refreshingPublication;
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
  }, [presentation, loading, work?.script_id, work?.title]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    request<Work>(`/api/works/${id}`, { signal: controller.signal }).then(result => { setWork(result); setTitle(result.title); setNotes(result.notes || ''); setEsDate(result.es_published_date || ''); setPatreonDate(result.patreon_published_date || ''); setDirectoryId(result.directories.length === 1 ? result.directories[0].id : null); }).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [id, retry]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!work) return;
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
  }, [id, work?.production_required, work?.id]);
  const confirmProduction = async () => {
    if (!work?.production_required || productionLock.current || saving || changingStatus || matchingDirty || productionTasks.loading || productionTasks.active || productionTasks.error || !work.script_count || work.association_status === 'missing' || work.association_status === 'unlinked' || !work.directories.some(directory => directory.id === directoryId && directory.available)) return;
    productionLock.current = true; setConfirmingProduction(true); setProductionError('');
    try {
      const updated = await request<Work>(`/api/works/${id}/production/confirm`, { method: 'POST', body: JSON.stringify({ expected_revision: work.production_revision ?? 0 }) });
      if (alive.current) { setWork(updated); setDirectoryId(updated.directories.length === 1 ? updated.directories[0].id : null); onSaved(); notify({ kind: 'success', message: t('已确认 {id} 制作完成', { id: workIdentity(updated) }) }); }
    } catch (error) { if (alive.current) setProductionError(errorMessage(error)); }
    finally { productionLock.current = false; if (alive.current) setConfirmingProduction(false); }
  };
  const resetProduction = async () => {
    if (!work || work.production_required || productionLock.current || saving || changingStatus || matchingDirty || productionTasks.loading || productionTasks.active || productionTasks.error) return;
    productionLock.current = true; setResettingProduction(true); setProductionError('');
    try {
      const updated = await request<Work>(`/api/works/${id}/production/reset`, { method: 'POST', body: JSON.stringify({ expected_revision: work.production_revision ?? 0 }) });
      if (alive.current) { setWork(updated); onSaved(); notify({ kind: 'success', message: t('已将 {id} 退回待制作', { id: workIdentity(updated) }) }); }
    } catch (error) { if (alive.current) setProductionError(errorMessage(error)); }
    finally { productionLock.current = false; if (alive.current) setResettingProduction(false); }
  };
  const changeStatus = async (platform: PublicationPlatform) => {
    if (!work || changingStatus || saving || productionLock.current) return;
    setChangingStatus(true); setSaveError('');
    try {
      const updated = await request<Work>(`/api/works/${id}`, { method: 'PATCH', body: JSON.stringify({ [`${platform}_published`]: !isPublished(work, platform) }) });
      if (platform === 'es') setEsDate(previous => previous === (work.es_published_date || '') ? updated.es_published_date || '' : previous);
      else setPatreonDate(previous => previous === (work.patreon_published_date || '') ? updated.patreon_published_date || '' : previous);
      setWork(updated); onSaved(); notify({ kind: 'success', message: t("{0} 的 {1} 已设为{2}", {"0": workIdentity(work), "1": platform === 'es' ? 'ES' : 'Patreon', "2": isPublished(updated, platform) ? t("已发布") : t("未发布")}) });
    }
    catch (error) { setSaveError(t("发布状态未更新：{0}", {"0": errorMessage(error)})); }
    finally { setChangingStatus(false); }
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!work || !dirty || productionLock.current || saving || changingStatus) return;
    if (!title.trim()) { setSaveError(t("请填写作品标题。")); return; }
    setSaving(true); setSaveError('');
    try { const updated = await request<Work>(`/api/works/${id}`, { method: 'PATCH', body: JSON.stringify({ title: title.trim(), notes, ...(esDate !== (work.es_published_date || '') ? { es_published_date: esDate || null } : {}), ...(patreonDate !== (work.patreon_published_date || '') ? { patreon_published_date: patreonDate || null } : {}) }) }); setWork(updated); setTitle(updated.title); setNotes(updated.notes || ''); setEsDate(updated.es_published_date || ''); setPatreonDate(updated.patreon_published_date || ''); onSaved(); notify({ kind: 'success', message: t("{0} 的作品信息已保存", {"0": workIdentity(updated)}) }); }
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
    <div className="detail-header"><div><span className="section-label">{t("作品详情")}</span>{presentation === 'page' ? <h1 id="detail-title" ref={heading} tabIndex={-1}>{work ? workIdentity(work) : t("正在读取")}</h1> : <h2 id="detail-title">{work ? workIdentity(work) : t("正在读取")}</h2>}</div>{presentation === 'page' ? <button className="button detail-back" ref={closeButton} onClick={close} disabled={leaveBlocked}><ArrowLeft size={17} />{t(backLabel)}</button> : <button className="icon-button" ref={closeButton} onClick={close} aria-label={t("关闭作品详情")} disabled={leaveBlocked} autoFocus><X size={21} /></button>}</div>
    {confirmClose && <div className="discard-confirm" role="alert"><strong>{t("有尚未保存的修改")}</strong><p>{t(presentation === 'page' ? '离开后将放弃尚未保存的标题、备注、发布日期或脚本对应关系。' : "关闭后将放弃尚未保存的标题、备注、发布日期或脚本对应关系。")}</p><div><button className="button small" ref={continueEditing} onClick={() => { pendingLeave.current = null; setConfirmClose(false); closeButton.current?.focus(); }}>{t("继续编辑")}</button><button className="button small" onClick={discard} disabled={leaveBlocked}>{t(presentation === 'page' ? '放弃更改并离开' : "放弃更改并关闭")}</button></div></div>}
    {loading ? <Loading label={t("正在读取作品详情")} /> : error ? <div className="detail-body"><ResourceError message={error} retry={() => setRetry(value => value + 1)} /></div> : work && <>
      <div className="detail-body"><div className="work-detail-hero"><Cover work={work} large /><div className="work-detail-hero-content"><div className="detail-heading">{workDisplayTitle(work) && <h3>{workDisplayTitle(work)}</h3>}<PublicationBadges work={work} /></div><div className="detail-summary"><span><Film size={15} />{work.video_count} {t("个视频", { count: work.video_count })}</span><span><FileText size={15} />{work.script_count} {t("个脚本", { count: work.script_count })}</span>{work.axis_type && <span>{tagName({ category: 'axis_type', name: work.axis_type })}</span>}</div></div></div><nav className="detail-section-nav" aria-label={t("作品详情分区")}>{[['detail-tags', '资料与标签'], ['detail-source-assets', '素材清单'], ['detail-publication', '发布信息'], ['work-edit-form', '作品信息']].map(([target, label]) => <button type="button" key={target} onClick={event => event.currentTarget.closest('.detail-page, .detail-dialog')?.querySelector('#' + target)?.scrollIntoView?.({ block: 'start', behavior: motionIsReduced() ? 'auto' : 'smooth' })}>{t(label)}</button>)}</nav>
        {(work.association_status === 'missing' || work.association_status === 'unlinked') && <div className="notice error" role="alert"><span><CircleAlert size={17} />{t('素材关联失败，请重新扫描。文件夹改名或迁移后，请在待处理的扫描候选里关联已有作品。')}</span></div>}
        {work.issues.length > 0 && <div className="detail-issues">{work.issues.map((issue, index) => <p key={`${issue.type}-${index}`}><CircleAlert size={16} /><span>{t(issue.message)}</span></p>)}</div>}
        <section className="detail-section directory-section"><div className="section-heading"><Folder size={17} /><h3>{t("文件概况")}</h3></div><div className="directory-options">{currentDir && <button type="button" className="directory-path-link" aria-label={t("打开文件夹")} onClick={() => void open()} disabled={!capabilities.can_open_folder || !currentDir.available || opening}><code>{currentDir.windows_path || currentDir.path}</code>{opening && <LoaderCircle className="spin" size={15} aria-hidden="true" />}{!currentDir.available && <small>{t("目录暂不可用")}</small>}</button>}</div>{!currentDir && <p className="help-text">{work.issues.some(issue => issue.type === 'duplicate_identifier') ? t("编号存在目录冲突，请处理冲突后重新匹配文件。") : t("尚无可关联的本地目录，请重新匹配文件。")}</p>}
          {!currentDir && <button type="button" className="directory-path-link" disabled aria-label={t("打开文件夹")}>{t("暂无唯一的关联目录。")}</button>}<p className="host-note">{!capabilities.can_open_folder ? capabilities.reason || t("不支持打开，仅素材所在主机可用") : !currentDir ? t("暂无唯一的关联目录。") : !currentDir.available ? t("当前目录暂不可用，请检查原文件夹。") : t("在素材所在 Windows 主机的资源管理器中打开。")}</p></section>
        <section id="detail-tags" className="detail-section"><div className="section-heading"><h3>{t("作品标签")}</h3><button className="button small detail-edit-tags" onClick={() => setEditingTags(true)}>{t("编辑标签")}</button></div><TagChips tags={work.tags?.filter(tag => tag.category !== 'duration')} durationStatus={work.duration_status} durationError={work.duration_error} /></section>
        <section className="detail-section work-state-manager" aria-labelledby="work-state-title"><div className="section-heading"><CheckCheck size={17} /><h3 id="work-state-title">{t('状态管理')}</h3></div>
          <div className="work-state-row"><div><h4>{t('制作状态')}</h4><span className={`badge ${work.production_required ? 'pending' : 'published'}`}>{t(work.production_required ? '待制作' : '已制作')}</span></div>{!work.production_required && <button className="button" disabled={saving || changingStatus || confirmingProduction || resettingProduction || matchingDirty || productionTasks.loading || productionTasks.active || !!productionTasks.error} onClick={() => void resetProduction()}>{resettingProduction ? <LoaderCircle size={16} className="spin" /> : <RefreshCw size={16} />}{t(resettingProduction ? '正在退回待制作' : '退回待制作')}</button>}</div>
          {!work.production_required && <p className="help-text">{t('退回待制作仅修改制作状态，保留 ES、Patreon 的发布状态、日期和链接。')}</p>}
          {!work.production_required && productionTasks.active && <p className="help-text">{t('后台任务正在运行，请等待扫描、匹配或预览完成后修改制作状态。')}</p>}
          {!work.production_required && productionTasks.error && <p className="inline-error" role="alert">{t('无法检查后台任务：{error}', { error: t(productionTasks.error) })}</p>}
          {work.production_required && <div className="production-confirmation" aria-labelledby="production-confirmation-title">
          <div className="section-heading"><CheckCheck size={17} /><h3 id="production-confirmation-title">{t('制作确认')}</h3><span className="badge pending">{t('待确认制作完成')}</span></div>
          <p className="help-text">{t('发现脚本不会自动确认完成；请检查当前素材后手动确认。')}</p>
          {!work.script_count ? <p className="help-text">{t('尚无可用脚本，请添加脚本后扫描或重新匹配文件。')}</p> : !currentDir?.available ? <p className="help-text">{t('当前作品目录不可用，请恢复目录后扫描或重新匹配文件。')}</p> : matchingDirty ? <p className="help-text">{t('请先保存或放弃尚未保存的脚本对应关系。')}</p> : productionTasks.active ? <p className="help-text">{t('后台任务正在运行，请等待扫描、匹配或预览完成后确认。')}</p> : null}
          {productionTasks.error && <p className="inline-error" role="alert">{t('无法检查后台任务：{error}', { error: t(productionTasks.error) })}</p>}
          {productionError && <p className="inline-error" role="alert">{t('制作完成未确认：{error}', { error: t(productionError) })}</p>}
          <button type="button" className="button primary" disabled={confirmingProduction || saving || changingStatus || matchingDirty || productionTasks.loading || productionTasks.active || !!productionTasks.error || !work.script_count || !currentDir?.available || work.association_status === 'missing' || work.association_status === 'unlinked'} onClick={() => void confirmProduction()}>
            {confirmingProduction || productionTasks.loading ? <LoaderCircle size={17} className="spin" /> : <CheckCheck size={17} />}{confirmingProduction ? t('正在确认制作完成') : t('确认制作完成')}
          </button>
          </div>}
          {!work.production_required && productionError && <p className="inline-error" role="alert">{t('制作状态未更新：{error}', { error: t(productionError) })}</p>}
          <p className="help-text">{t("平台未发布时，制作未确认的作品仍在待制作；完成制作后才进入待发布。")}</p>
        </section>
        <section id="detail-publication" className="detail-section detail-publication"><div className="section-heading"><h3>{t("发布信息")}</h3></div><div className="work-state-platforms"><div className="publication-controls">{(['es', 'patreon'] as const).map(platform => <button key={platform} className={'button publication-status-toggle ' + (isPublished(work, platform) ? 'published' : 'pending')} aria-label={isPublished(work, platform) ? t("将 {0} 改为未发布", {"0": platform === 'es' ? 'ES' : 'Patreon'}) : t("标记 {0} 已发布", {"0": platform === 'es' ? 'ES' : 'Patreon'})} aria-pressed={isPublished(work, platform)} disabled={changingStatus || saving || confirmingProduction || resettingProduction || refreshingPublication} onClick={() => void changeStatus(platform)}>{changingStatus ? <LoaderCircle size={16} className="spin" /> : isPublished(work, platform) ? <Clock3 size={16} /> : <CheckCheck size={16} />}{platform === 'es' ? 'ES' : 'Patreon'}</button>)}</div></div><div className="release-date-fields"><div><label htmlFor="work-patreon-date">{t("Patreon 发布日期")}</label><input id="work-patreon-date" type="date" form="work-edit-form" value={patreonDate} onChange={event => setPatreonDate(event.target.value)} disabled={saving || changingStatus} /></div><div><label htmlFor="work-es-date">{t("ES 发布日期")}</label><input id="work-es-date" type="date" form="work-edit-form" value={esDate} onChange={event => setEsDate(event.target.value)} disabled={saving || changingStatus} /></div></div><p className="help-text">{t("保存对应的帖子链接时，未记录的日期默认填入当天（北京时间）；已有日期保留，也可以在这里修改或清空。")}</p><div className="detail-planned-dates">{(['es', 'patreon'] as const).map(platform => {
  const date = work[platform + '_planned_date' as 'es_planned_date' | 'patreon_planned_date'];
  return date ? <span key={platform}>{platform === 'es' ? 'ES' : 'Patreon'} · {t('计划日期')} <time dateTime={date}>{date}</time></span> : null;
})}</div><WorkLinkButtons work={work} onEdit={kind => { if (!refreshingPublication) setEditingLink(kind); }} /><p className="help-text">{t("点击平台状态切换；点击链接按钮编辑发布链接、计划日期与发布日期。")}</p></section>
        {SHOW_ES_POSTS && <section className="detail-section"><div className="section-heading"><FileText size={17} /><h3>{t("ES 贴文")}</h3></div><p className="help-text">{t("根据作品标签和发布链接套用模板，近期作品预览每次生成时自动更新。上传 Markdown 和生成稿保存在数据库中。")}</p><button className="button primary" onClick={() => setEditingPost(true)}><FileText size={17} />{t("生成贴文")}</button>{dirty && <p className="help-text">{t("贴文使用已保存的作品信息；修改标题后请先保存。")}</p>}</section>}
        <div id="detail-source-assets" className="detail-source-assets"><PreviewSection work={work} capabilities={capabilities} onDirtyChange={setMatchingDirty} onSourcesChanged={async () => {
          const updated = await request<Work>(`/api/works/${id}`);
          // Refresh discovered source data while retaining locally edited text and tags.
          setWork(previous => previous ? { ...previous, production_required: updated.production_required, production_confirmed_at: updated.production_confirmed_at, production_revision: updated.production_revision, association_status: updated.association_status, association_revision: updated.association_revision, preview_key: updated.preview_key, preview_stale: updated.preview_stale, assets: updated.assets, directories: updated.directories, video_count: updated.video_count, script_count: updated.script_count, cover_url: updated.cover_url, issues: updated.issues, axis_type: updated.axis_type,
            duration_seconds: updated.duration_seconds, duration_minutes: updated.duration_minutes,
            duration_status: updated.duration_status, duration_error: updated.duration_error,
            duration_last_known_seconds: updated.duration_last_known_seconds, duration_last_known_minutes: updated.duration_last_known_minutes,
            tags: [...(previous.tags || []).filter(tag => !['axis_type', 'duration'].includes(tag.category)), ...(updated.tags || []).filter(tag => ['axis_type', 'duration'].includes(tag.category))],
            tags_revision: updated.tags_revision } : previous);
          setDirectoryId(updated.directories.length === 1 ? updated.directories[0].id : null);
          onSaved();
        }} /></div>
        <div className="detail-tabs" role="tablist" aria-label={t("作品关联资料")}><button id="assets-tab" role="tab" aria-selected={tab === 'assets'} aria-controls="assets-panel" onClick={() => setTab('assets')}>{t("素材清单")}<span>{assets.length}</span></button><button id="metadata-tab" role="tab" aria-selected={tab === 'metadata'} aria-controls="metadata-panel" onClick={() => setTab('metadata')}>{t("历史资料")}</button></div>
        {tab === 'assets' ? <section id="assets-panel" role="tabpanel" aria-labelledby="assets-tab" className="detail-section assets-panel">{!assets.length && <p className="help-text">{t("该编号目录尚未发现关联素材。")}</p>}{assetGroups.filter(([, values]) => values.length).map(([label, values]) => <div className="asset-group" key={label}><h4>{label}<span>{values.length}</span></h4><ul>{values.map(asset => <li key={asset.id}>{label === t("视频") ? <Film size={17} /> : <FileText size={17} />}<div><strong>{asset.name}</strong><small>{asset.relative_path}{work.directories.length > 1 ? t(" · 目录 {0}", {"0": asset.directory_id}) : ''}</small></div><span className="asset-size">{asset.axis && <b>{asset.axis}</b>}{formatSize(asset.size)}</span></li>)}</ul></div>)}</section> : <section id="metadata-panel" role="tabpanel" aria-labelledby="metadata-tab" className="detail-section">{history.length ? <><dl className="metadata-list">{history.map(([label, value]) => { const link = safeLink(value); return <div key={label}><dt>{label}</dt><dd>{link ? <a href={link} target="_blank" rel="noreferrer">{displayValue(value)}</a> : displayValue(value)}</dd></div>; })}</dl><p className="help-text">{t("来自首次导入的历史资料，计划日期不代表实际发布日期。")}</p></> : <p className="help-text">{t("暂无关联的历史资料。")}</p>}</section>}
        <form id="work-edit-form" className="detail-section edit-form" onSubmit={event => void save(event)}><div className="section-heading"><FileText size={17} /><h3>{t("作品信息")}</h3>{dirty && <span className="unsaved-label">{t("未保存")}</span>}</div><label htmlFor="work-title">{t("标题")}</label><input id="work-title" type="text" maxLength={500} value={title} onChange={event => setTitle(event.target.value)} disabled={saving} /><label htmlFor="work-notes">{t("备注")}</label><textarea id="work-notes" rows={4} maxLength={10000} placeholder={t("记录发布安排、素材说明…")} value={notes} onChange={event => setNotes(event.target.value)} disabled={saving} /><p className="help-text">{t("扫描不会覆盖你维护的标题、备注、发布日期和发布状态。")}</p></form>
      </div>
      <div className="detail-actions">{saveError && <p className="inline-error" role="alert">{t(saveError)}</p>}<div><button className="button primary" type="submit" form="work-edit-form" disabled={!dirty || saving || changingStatus || confirmingProduction || resettingProduction || refreshingPublication}>{saving ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}{saving ? t("正在保存") : t("保存信息")}</button></div></div>
    </>}
    {work && editingTags && <WorkTagEditor work={work} onClose={() => setEditingTags(false)} onSaved={value => { setWork(previous => previous ? { ...previous, tags: value.tags, tags_revision: value.tags_revision } : previous); onSaved(); }} />}
    {work && editingLink && <WorkLinkEditor work={work} initialKind={editingLink} onClose={() => setEditingLink(null)} onSaved={value => {
      setRefreshingPublication(true);
      const previousEsDate = work.es_published_date || '';
      const previousPatreonDate = work.patreon_published_date || '';
      if (value.es_published_date !== undefined) setEsDate(previous => previous === previousEsDate ? value.es_published_date || '' : previous);
      if (value.patreon_published_date !== undefined) setPatreonDate(previous => previous === previousPatreonDate ? value.patreon_published_date || '' : previous);
      // Only publication fields are refreshed; unfinished title/notes/matching edits remain local.
      setWork(previous => previous ? { ...previous, links: value.links, links_revision: value.links_revision,
        ...(value.es_published !== undefined ? { es_published: value.es_published } : {}),
        ...(value.patreon_published !== undefined ? { patreon_published: value.patreon_published } : {}),
        ...(value.es_planned_date !== undefined ? { es_planned_date: value.es_planned_date } : {}),
        ...(value.patreon_planned_date !== undefined ? { patreon_planned_date: value.patreon_planned_date } : {}),
        ...(value.es_published_date !== undefined ? { es_published_date: value.es_published_date } : {}),
        ...(value.patreon_published_date !== undefined ? { patreon_published_date: value.patreon_published_date } : {}) } : previous);
      void request<Work>('/api/works/' + id).then(updated => {
        if (!alive.current) return;
        setEsDate(previous => previous === previousEsDate ? updated.es_published_date || '' : previous);
        setPatreonDate(previous => previous === previousPatreonDate ? updated.patreon_published_date || '' : previous);
        setWork(previous => previous ? { ...previous, links: updated.links, links_revision: updated.links_revision,
          es_published: updated.es_published, patreon_published: updated.patreon_published,
          es_planned_date: updated.es_planned_date, patreon_planned_date: updated.patreon_planned_date,
          es_published_date: updated.es_published_date, patreon_published_date: updated.patreon_published_date } : previous);
        onSaved();
      }).catch(error => { if (alive.current) { setSaveError(t("发布资料已保存，但刷新失败：{error}", { error: errorMessage(error) })); onSaved(); } }).finally(() => { if (alive.current) setRefreshingPublication(false); });
    }} />}
    {SHOW_ES_POSTS && work && editingPost && <ReleasePostEditor workId={id} onClose={() => setEditingPost(false)} />}
  </>;
  return presentation === 'page' ? <article className="detail-page" aria-labelledby="detail-title">{content}</article> : <dialog className="detail-dialog" ref={dialog} aria-labelledby="detail-title" onCancel={event => { event.preventDefault(); close(); }} onClick={event => { if (event.target === event.currentTarget) { const rect = event.currentTarget.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close(); } }}>{content}</dialog>;
}

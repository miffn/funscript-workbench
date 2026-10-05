import { translate as t, useI18n, tagName } from './i18n';
import { LanguageSettings } from './LanguageSettings';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Archive, ArrowLeft, ArrowRight, Check, CheckCheck, CalendarDays, ChevronRight, CircleAlert, Clock3, FilePenLine, FileText, Film, LayoutGrid, List, LoaderCircle, Moon, RefreshCw, Search, Settings2, SlidersHorizontal, Sun, Tag as TagIcon, X } from 'lucide-react';
import { errorMessage, formatDate, isActiveJob, request } from './api';
import type { Capabilities, Filter, Inventory, Job, Work, WorkLinkKind, WorkLinks, WorkTags } from './api';
import { WorkLinkButtons, WorkLinkEditor } from './WorkLinks';
import { ReleaseDates, workDisplayTitle } from './ReleaseDates';
import { ProfileSettings } from './ProfileSettings';
import { EsTemplateSettings } from './ReleasePosts';
import { SHOW_ES_POSTS } from './features';
import { ReleaseCalendar } from './ReleaseCalendar';
import type { ProfileData } from './ProfileSettings';
import { TagChips, TagsPage, WorkTagEditor, useTagCatalog, tagCategories } from './Tags';
import { Cover, EmptyState, Loading, WorkDetail, IssuesPage, JobsPage, SettingsPage, PublicationBadges } from './components';

type Page = 'inventory' | 'issues' | 'jobs' | 'settings' | 'tags' | 'calendar';
export type Notice = { kind: 'success' | 'error' | 'info'; message: string };
const EMPTY_STATS = { total: 0, pending: 0, to_make: 0, published: 0, es_published: 0, patreon_published: 0, issues: 0 };
const PAGE_SIZE = 24;
function routeFromHash(hash: string): { page: Page; filter: Filter } {
  const route = hash.replace(/^#\/?/, '');
  if (route === 'to_make' || route === 'pending' || route === 'published' || route === 'es_published' || route === 'patreon_published') return { page: 'inventory', filter: route };
  if (route === 'issues' || route === 'jobs' || route === 'settings' || route === 'tags' || route === 'calendar') return { page: route, filter: 'all' };
  return { page: 'inventory', filter: 'all' };
}

export default function App() {
  useI18n();
  const [initialRoute] = useState(() => routeFromHash(window.location.hash));
  const [page, setPage] = useState<Page>(initialRoute.page);
  const [filter, setFilter] = useState<Filter>(initialRoute.filter);
  const [issuesOnly, setIssuesOnly] = useState(false);
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [number, setNumber] = useState(1);
  const [inventory, setInventory] = useState<Inventory | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [tagWork, setTagWork] = useState<Work | null>(null);
  const [linkWork, setLinkWork] = useState<{ work: Work; kind: WorkLinkKind } | null>(null);
  const [tagId, setTagId] = useState<number | null>(null);
  const [untaggedOnly, setUntaggedOnly] = useState(false);
  const [capabilities, setCapabilities] = useState<Capabilities>({ can_open_folder: false, reason: t("正在检查访问端能力") });
  const [notice, setNotice] = useState<Notice | null>(null);
  const [profile, setProfile] = useState<ProfileData>({ name: 'Funscript', bio: '脚本工作台', avatar: null, revision: 0 });
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileError, setProfileError] = useState('');
  const [profileRetry, setProfileRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setProfileLoading(true);
    request<ProfileData>('/api/profile', { signal: controller.signal }).then(value => {
      if (controller.signal.aborted) return;
      if (typeof value.name !== 'string' || typeof value.bio !== 'string' || !Number.isInteger(value.revision)) throw new Error(t("资料格式无效"));
      setProfile(value); setProfileError('');
    }).catch(error => { if (!controller.signal.aborted) setProfileError(t("无法读取工作台资料：{0}", {"0": errorMessage(error)})); })
      .finally(() => { if (!controller.signal.aborted) setProfileLoading(false); });
    return () => controller.abort();
  }, [profileRetry]);
  const [job, setJob] = useState<Job | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [theme, setTheme] = useState(document.documentElement.dataset.theme || 'light');
  const [view, setView] = useState<'gallery' | 'list' | 'tags'>(() => { try { const saved = localStorage.getItem('workbench-view'); return saved === 'list' || saved === 'tags' ? saved : 'gallery'; } catch { return 'gallery'; } });
  const { catalog, error: tagError, refresh: refreshTags } = useTagCatalog(revision);
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const readRoute = () => {
      const route = routeFromHash(window.location.hash);
      setPage(route.page); setFilter(route.filter); setNumber(1); setIssuesOnly(false);
    };
    window.addEventListener('hashchange', readRoute);
    return () => window.removeEventListener('hashchange', readRoute);
  }, []);
  const notify = useCallback((next: Notice) => {
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
    setNotice(next);
    if (next.kind === 'success') noticeTimer.current = setTimeout(() => setNotice(null), 6500);
  }, []);
  useEffect(() => () => { if (noticeTimer.current) clearTimeout(noticeTimer.current); }, []);
  useEffect(() => {
    const timer = setTimeout(() => { setSearch(query.trim()); setNumber(1); }, 300);
    return () => clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    const controller = new AbortController();
    request<Capabilities>('/api/capabilities', { signal: controller.signal }).then(setCapabilities).catch(error => { if (!controller.signal.aborted) setCapabilities({ can_open_folder: false, reason: t("无法确认主机能力：{0}", {"0": errorMessage(error)}) }); });
    return () => controller.abort();
  }, [revision]);
  useEffect(() => {
    let alive = true;
    let controller: AbortController | null = null;
    let inFlight = false;
    setLoading(true);
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      controller = new AbortController();
      const params = new URLSearchParams({ q: search, status: filter, issues_only: String(issuesOnly), page: String(number), page_size: String(PAGE_SIZE) });
      if (tagId !== null) params.set('tag_id', String(tagId));
      if (untaggedOnly) params.set('untagged_only', 'true');
      try {
        const result = await request<Inventory>(`/api/works?${params}`, { signal: controller.signal });
        if (alive) { setInventory(result); setError(''); if (number > 1 && !result.items.length && result.total > 0) setNumber(Math.ceil(result.total / PAGE_SIZE)); }
      } catch (error) { if (alive && !controller.signal.aborted) setError(errorMessage(error)); }
      finally { inFlight = false; if (alive) setLoading(false); }
    };
    void load();
    const timer = setInterval(() => void load(), 10000);
    return () => { alive = false; clearInterval(timer); controller?.abort(); };
  }, [search, filter, issuesOnly, number, revision, tagId, untaggedOnly]);
  useEffect(() => {
    let alive = true;
    let controller: AbortController | null = null;
    const detectScan = async () => {
      controller?.abort();
      controller = new AbortController();
      try {
        const result = await request<{ items: Job[] }>('/api/jobs', { signal: controller.signal });
        const active = result.items.find(job => job.type === 'scan' && isActiveJob(job));
        if (alive && active) setJob(previous => isActiveJob(previous) ? previous : active);
      } catch { /* Inventory requests provide connection errors; this only discovers automatic scans. */ }
    };
    void detectScan();
    const timer = setInterval(() => void detectScan(), 10000);
    return () => { alive = false; controller?.abort(); clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (!isActiveJob(job)) return;
    let alive = true;
    let controller: AbortController | null = null;
    let inFlight = false;
    const poll = async () => {
      if (inFlight || !job) return;
      inFlight = true;
      controller = new AbortController();
      try {
        const result = await request<Job>(`/api/jobs/${job.id}`, { signal: controller.signal });
        if (!alive) return;
        setJob(result);
        if (!isActiveJob(result)) {
          setRevision(value => value + 1);
          notify({ kind: result.status === 'failed' ? 'error' : 'success', message: result.status === 'failed' ? t("扫描失败：{0}", {"0": result.error || t("请在扫描记录中查看原因")}) : t("扫描完成，库存已更新") });
        }
      } catch (error) { if (alive && !controller.signal.aborted) notify({ kind: 'error', message: t("无法读取扫描进度：{0}", {"0": errorMessage(error)}) }); }
      finally { inFlight = false; }
    };
    const timer = setInterval(() => void poll(), 1800);
    return () => { alive = false; clearInterval(timer); controller?.abort(); };
  }, [job?.id, job?.status, notify]);
  useEffect(() => {
    try { localStorage.setItem('workbench-view', view); localStorage.setItem('workbench-theme', theme); } catch { /* Optional appearance preferences only. */ }
    document.documentElement.dataset.theme = theme;
  }, [theme, view]);

  const stats = inventory?.stats || EMPTY_STATS;
  const currentJob = isActiveJob(job) ? job : null;
  const busy = submitting || !!currentJob;
  const scan = async () => {
    setSubmitting(true);
    try { const next = await request<Job>('/api/scans', { method: 'POST' }); setJob(next); notify({ kind: 'info', message: t("扫描已提交，你可以继续浏览库存。") }); setRevision(value => value + 1); }
    catch (error) { notify({ kind: 'error', message: t("无法开始扫描：{0}", {"0": errorMessage(error)}) }); }
    finally { setSubmitting(false); }
  };
  const navigate = (target: Page, nextFilter: Filter = 'all') => {
    const route = target === 'inventory' ? nextFilter === 'all' ? 'inventory' : nextFilter : target;
    window.location.hash = `/${route}`;
    setPage(target); setFilter(nextFilter); setNumber(1); setIssuesOnly(false);
  };
  const title = page === 'inventory' ? filter === 'to_make' ? t('待制作库存') : filter === 'pending' ? t("待发布库存") : filter === 'es_published' ? t("ES 已发布作品") : filter === 'patreon_published' ? t("Patreon 已发布作品") : filter === 'published' ? t("全部平台已发布作品") : t("脚本库存") : page === 'issues' ? t("待处理") : page === 'jobs' ? t("任务记录") : page === 'tags' ? t("标签管理") : page === 'calendar' ? t("发布日历") : t("工作台设置");
  const subtitle = page === 'inventory' ? filter === 'to_make' ? t('添加脚本并扫描或重新匹配后，还需在作品详情手动确认制作完成；有脚本且尚未全部发布的作品才会进入待发布。') : t("每一个编号，都有自己的素材与发布进展。") : page === 'issues' ? t("查看编号冲突、缺失素材和历史资料的关联问题。") : page === 'jobs' ? t("库存扫描与预览生成的执行进度、结果和失败原因。") : page === 'tags' ? t("统一维护作者与分类，作品绑定标签后复用资料。") : page === 'calendar' ? t("安排发布计划，记录 ES 与 Patreon 的实际发布日期。") : t("当前扫描目录与工作台的运行规则。");
  const totalPages = Math.max(1, Math.ceil((inventory?.total || 0) / PAGE_SIZE));
  const tagsSaved = (value: WorkTags) => {
    setInventory(previous => previous ? { ...previous, items: previous.items.map(work => work.id === value.work_id ? { ...work, tags: value.tags, tags_revision: value.tags_revision } : work) } : previous);
    setRevision(value => value + 1); notify({ kind: 'success', message: t("作品标签已保存") });
  };
  const linksSaved = (value: WorkLinks) => {
    setInventory(previous => previous ? { ...previous, items: previous.items.map(work => work.id === value.work_id ? { ...work, links: value.links, links_revision: value.links_revision, es_published_date: value.es_published_date, patreon_published_date: value.patreon_published_date } : work) } : previous);
    setRevision(revision => revision + 1); notify({ kind: 'success', message: t("发布链接已保存") });
  };

  return <div className="app-shell">
    <a className="skip-link" href="#main-content">{t("跳到主要内容")}</a>
    <aside className="sidebar" aria-label={t("工作台导航")}>
      <a className="brand" href="#/settings" onClick={event => { event.preventDefault(); navigate('settings'); }} aria-label={t("编辑工作台资料")} title={t("修改头像、姓名和简介")}><div className="brand-mark">{profile.avatar ? <img src={profile.avatar} alt={t("{0}的头像", {"0": profile.name})} /> : <Archive size={21} aria-hidden="true" />}</div><div><strong>{profile.name}</strong><small>{profile.bio}</small></div></a>
      <div className="nav-section"><p className="section-label">{t("作品库")}</p><nav className="nav-list">
        <button className={page === 'inventory' && filter === 'all' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('inventory')} aria-current={page === 'inventory' && filter === 'all' ? 'page' : undefined}><LayoutGrid size={18} /><span>{t("全部库存")}</span>{inventory && <span className="nav-count">{stats.total}</span>}</button>
        <button className={page === 'inventory' && filter === 'to_make' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('inventory', 'to_make')} aria-current={page === 'inventory' && filter === 'to_make' ? 'page' : undefined}><FilePenLine size={18} /><span>{t('待制作')}</span>{inventory && <span className="nav-count">{stats.to_make ?? 0}</span>}</button>
        <button className={page === 'inventory' && filter === 'pending' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('inventory', 'pending')} aria-current={page === 'inventory' && filter === 'pending' ? 'page' : undefined}><Clock3 size={18} /><span>{t("待发布")}</span>{inventory && <span className="nav-count">{stats.pending}</span>}</button>
        <button className={page === 'calendar' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('calendar')} aria-current={page === 'calendar' ? 'page' : undefined}><CalendarDays size={18} /><span>{t("发布日历")}</span></button>
        {(['es_published', 'patreon_published'] as const).map(platformFilter => <button key={platformFilter} className={page === 'inventory' && filter === platformFilter ? 'nav-item active' : 'nav-item'} onClick={() => navigate('inventory', platformFilter)} aria-current={page === 'inventory' && filter === platformFilter ? 'page' : undefined}><CheckCheck size={18} /><span>{platformFilter === 'es_published' ? t("ES 已发布") : t("Patreon 已发布")}</span>{inventory && <span className="nav-count">{stats[platformFilter] ?? stats.published}</span>}</button>)}
      </nav></div>
      <div className="nav-section"><p className="section-label">{t("工作流")}</p><nav className="nav-list">
        <button className={page === 'issues' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('issues')} aria-current={page === 'issues' ? 'page' : undefined}><CircleAlert size={18} /><span>{t("待处理")}</span>{inventory && stats.issues > 0 && <span className="nav-count issue-count">{stats.issues}</span>}</button>
        <button className={page === 'jobs' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('jobs')} aria-current={page === 'jobs' ? 'page' : undefined}><RefreshCw size={18} /><span>{t("任务记录")}</span>{currentJob && <span className="activity-dot" aria-label={t("正在扫描")} />}</button>
        <button className={page === 'tags' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('tags')} aria-current={page === 'tags' ? 'page' : undefined}><TagIcon size={18} /><span>{t("标签管理")}</span></button>
        <button className={page === 'settings' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('settings')} aria-current={page === 'settings' ? 'page' : undefined}><Settings2 size={18} /><span>{t("设置")}</span></button>
      </nav></div>
      <div className="sidebar-footer"><div className="host-state"><span className={`connection-dot ${error ? 'offline' : ''}`} /><span>{error ? t("连接待恢复") : inventory ? t("库存手动扫描") : t("正在连接")}</span></div><p>{t("文件夹编号是库存依据")}<br />{t("发布状态由你维护")}</p><button className="theme-toggle" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-label={theme === 'dark' ? t("切换浅色主题") : t("切换深色主题")}>{theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}<span>{theme === 'dark' ? t("浅色外观") : t("深色外观")}</span></button></div>
    </aside>
    <main id="main-content" className="main-content">
      <div className="page-kicker"><span>{t("我的工作空间")}<ChevronRight size={13} /> {page === 'inventory' ? t("作品库") : t("工作流")}</span><span className="last-updated">{t("最近扫描 ·")}{formatDate(inventory?.last_scan?.at)}</span></div>
      <header className="page-header"><div><h1>{title}</h1><p>{subtitle}</p></div><button className="button primary" onClick={() => void scan()} disabled={busy}>{busy ? <LoaderCircle size={17} className="spin" /> : <RefreshCw size={17} />}<span>{busy ? t("正在扫描") : t("立即扫描")}</span></button></header>
      {notice && <div className={`notice ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}><span>{notice.kind === 'error' ? <CircleAlert size={18} /> : notice.kind === 'success' ? <Check size={18} /> : <RefreshCw size={18} />}{t(notice.message)}</span><button className="icon-button" aria-label={t("关闭提示")} onClick={() => setNotice(null)}><X size={17} /></button></div>}
      {currentJob && <div className="scan-banner" role="status"><LoaderCircle className="spin" size={16} /><span>{t("后台正在扫描素材并更新封面")}</span><button onClick={() => navigate('jobs')}>{t("查看进度")}<ArrowRight size={14} /></button></div>}
      {page === 'inventory' && <>
        <div className="inventory-overview"><span><strong>{inventory ? inventory.total : '—'}</strong> {t("个库存", { count: inventory?.total || 0 })}{issuesOnly ? ` · ${t("异常")}` : filter === 'to_make' ? ` · ${t('待制作')}` : filter === 'pending' ? ` · ${t("待发布")}` : filter === 'es_published' ? ` · ${t("ES 已发布")}` : filter === 'patreon_published' ? ` · ${t("Patreon 已发布")}` : filter === 'published' ? ` · ${t("全部平台已发布")}` : ''}</span><span>{t("按完整编号关联素材")}</span></div>
        <div className="toolbar"><label className="search-field"><Search size={18} aria-hidden="true" /><span className="sr-only">{t("搜索编号、标题或标签")}</span><input type="search" placeholder={t("搜索编号、标题、标签…")} value={query} onChange={event => setQuery(event.target.value)} /></label><div className="toolbar-options"><button className={`button filter-button ${issuesOnly ? 'selected' : ''}`} aria-pressed={issuesOnly} onClick={() => { setIssuesOnly(!issuesOnly); setNumber(1); }}><SlidersHorizontal size={16} />{t("仅看异常")}</button><div className="view-switch" aria-label={t("库存显示方式")}><button className="icon-button" aria-pressed={view === 'gallery'} onClick={() => setView('gallery')} aria-label={t("封面画廊")}><LayoutGrid size={18} /></button><button className="icon-button" aria-pressed={view === 'list'} onClick={() => setView('list')} aria-label={t("紧凑目录")}><List size={19} /></button><button className="icon-button" aria-pressed={view === 'tags'} onClick={() => setView('tags')} aria-label={t("标签列表")}><TagIcon size={18} /></button></div></div></div>
        <div className="tag-inventory-filters"><label className="tag-filter"><span>{t("按标签筛选")}</span><select value={tagId || ''} onChange={event => { setTagId(event.target.value ? Number(event.target.value) : null); setUntaggedOnly(false); setNumber(1); }}><option value="">{t("全部标签")}</option>{tagCategories.map(([category, label]) => { const options = (catalog?.items || []).filter(tag => tag.category === category); return options.length ? <optgroup key={category} label={t(label)}>{options.map(tag => <option key={tag.id} value={tag.id}>{t(label)} · {tagName(tag)}</option>)}</optgroup> : null; })}</select></label><label className="untagged-filter"><input type="checkbox" checked={untaggedOnly} onChange={event => { setUntaggedOnly(event.target.checked); setTagId(null); setNumber(1); }} />{t("仅看未标注")}</label>{view === 'tags' && <span className="help-text">{t("不加载封面，快速维护作品标签")}</span>}<div className="tag-color-legend" aria-label={t("标签颜色分类")}>{tagCategories.map(([category, label]) => <span className={`tag-chip ${category}`} key={category}>{t(label)}</span>)}</div></div>
        {tagError && <div className="notice error" role="alert"><span>{t("无法读取标签筛选：")}{t(tagError)}</span><button className="button small" onClick={refreshTags}>{t("重试")}</button></div>}
        {error && <div className="notice error" role="alert"><span><CircleAlert size={18} />{t("无法更新库存：")}{t(error)}. {inventory ? t("当前显示上次读取的数据。") : ''}</span><button className="button small" onClick={() => setRevision(value => value + 1)}>{t("重试")}</button></div>}
        {loading ? <Loading /> : !inventory ? <EmptyState title={t("暂时无法读取库存")} description={t("确认工作台服务已启动，然后重新连接。")}><button className="button" onClick={() => setRevision(value => value + 1)}><RefreshCw size={16} />{t("重新连接")}</button></EmptyState> : !inventory.items.length ? <EmptyState title={search || issuesOnly || tagId || untaggedOnly ? t("没有符合条件的作品") : filter === 'to_make' ? t('暂无待制作作品') : filter === 'pending' ? t('暂无待发布作品') : filter !== 'all' ? t("没有符合条件的作品") : t("这里还没有库存")} description={search || issuesOnly || tagId || untaggedOnly ? t("换一个编号、标题或标签，或取消筛选。") : filter === 'to_make' ? t('无脚本的编号目录会在扫描后显示；补齐脚本后仍需手动确认制作完成。') : filter === 'pending' ? t('已有脚本、制作已确认且尚未全部发布的作品会显示在这里。') : t("在扫描目录建立编号文件夹后，点击“立即扫描”更新库存。")}>{(search || issuesOnly || tagId || untaggedOnly) && <button className="button" onClick={() => { setQuery(''); setSearch(''); setIssuesOnly(false); setTagId(null); setUntaggedOnly(false); }}>{t("清除筛选")}</button>}</EmptyState> : view === 'tags' ? <div className="work-tag-list" aria-label={t("快速标签库存列表")}>{inventory.items.map(work => <article className="work-tag-row" key={work.id}><div className="work-tag-identity"><span className="script-id">{work.script_id}</span><PublicationBadges work={work} /><ReleaseDates work={work} />{workDisplayTitle(work) && <h2>{workDisplayTitle(work)}</h2>}</div><TagChips tags={work.tags} durationStatus={work.duration_status} durationError={work.duration_error} /><WorkLinkButtons work={work} onEdit={kind => setLinkWork({ work, kind })} /><button className="button small" onClick={() => setTagWork(work)} aria-label={t("编辑标签 {0}", {"0": work.script_id})}><TagIcon size={15} />{t("编辑标签")}</button></article>)}</div> : <div className={`work-collection ${view}`} aria-label={t("库存作品")}>
          {inventory.items.map(work => <article key={work.id} className="work-card"><button type="button" className="work-card-target" onClick={() => setSelected(work.id)} aria-label={t("查看 {0} {1}", {"0": work.script_id, "1": work.title})} /><Cover work={work} /><div className="work-info"><div className="work-topline"><span className="script-id">{work.script_id}</span><PublicationBadges work={work} /></div><ReleaseDates work={work} />{workDisplayTitle(work) && <h2>{workDisplayTitle(work)}</h2>}<TagChips tags={work.tags} durationStatus={work.duration_status} durationError={work.duration_error} /><div className="work-foot"><div className="work-foot-tools"><span className="asset-count"><Film size={14} />{work.video_count}<span className="separator" /><FileText size={14} />{work.script_count}</span><WorkLinkButtons work={work} onEdit={kind => setLinkWork({ work, kind })} /></div>{work.issues.length ? <span className="issue-label"><CircleAlert size={14} />{work.issues.length} {t("项异常")}</span> : <span className="work-type">{(work.axis_type ? tagName({ category: 'axis_type', name: work.axis_type }) : work.video_type) || t("素材已关联")}</span>}</div></div></article>)}
        </div>}
        {!!inventory?.total && <div className="pagination"><span>{t('第 {page} / {pages} 页 · 每页 {size} 个', { page: Math.min(number, totalPages), pages: totalPages, size: PAGE_SIZE })}</span><div><button className="icon-button" aria-label={t("上一页")} onClick={() => { setNumber(value => value - 1); window.scrollTo({ top: 0, behavior: 'auto' }); }} disabled={number <= 1 || loading}><ArrowLeft size={17} /></button><span aria-live="polite">{number}</span><button className="icon-button" aria-label={t("下一页")} onClick={() => { setNumber(value => value + 1); window.scrollTo({ top: 0, behavior: 'auto' }); }} disabled={number >= totalPages || loading}><ArrowRight size={17} /></button></div></div>}
      </>}
      {page === 'calendar' && <ReleaseCalendar revision={revision} onSelect={setSelected} onChanged={() => setRevision(value => value + 1)} />}
      {page === 'issues' && <IssuesPage revision={revision} onSelect={setSelected} />}
      {page === 'jobs' && <JobsPage revision={revision} />}
      {page === 'settings' && <><LanguageSettings />{profileLoading ? <p className="loading-state" role="status">{t("正在读取工作台资料…")}</p> : profileError ? <div className="notice error" role="alert"><span>{t(profileError)}</span><button className="button small" onClick={() => setProfileRetry(value => value + 1)}>{t("重试读取资料")}</button></div> : <ProfileSettings profile={profile} onSaved={value => { setProfile(value); notify({ kind: 'success', message: t("工作台资料已保存") }); }} />}{SHOW_ES_POSTS && <EsTemplateSettings />}<SettingsPage capabilities={capabilities} revision={revision} /></>}
      {page === 'tags' && <TagsPage revision={revision} onChanged={() => setRevision(value => value + 1)} />}
      <footer className="page-footer"><span>{t("Funscript 工作台")}</span><span>{t("库存与状态保存在服务端")}</span></footer>
    </main>
    {selected !== null && <WorkDetail id={selected} capabilities={capabilities} onClose={() => setSelected(null)} onSaved={() => setRevision(value => value + 1)} notify={notify} />}
    {tagWork && <WorkTagEditor work={tagWork} onClose={() => setTagWork(null)} onSaved={tagsSaved} />}
    {linkWork && <WorkLinkEditor work={linkWork.work} initialKind={linkWork.kind} onClose={() => setLinkWork(null)} onSaved={linksSaved} />}
  </div>;
}

import { LanguageSettings } from './LanguageSettings';
import { workIdentity } from './api';
import { translate as t, useI18n } from './i18n';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUp, ArrowRight, ArrowDownWideNarrow, ArrowUpWideNarrow, Check, CheckCheck, CalendarDays, CircleAlert, Clock3, FilePenLine, FileText, Film, LayoutGrid, List, LoaderCircle, Moon, PanelLeftClose, PanelLeftOpen, RefreshCw, Search, Settings2, SlidersHorizontal, Sun, Tag as TagIcon, X } from 'lucide-react';
import { errorMessage, formatDate, isActiveJob, request } from './api';
import type { Capabilities, Filter, Job, Work, WorkLinkKind, WorkLinks, WorkTags } from './api';
import { WorkLinkButtons, WorkLinkEditor } from './WorkLinks';
import { workDisplayTitle } from './ReleaseDates';
import { PersonalPage } from './PersonalPage';
import { SettingsWorkbench } from './SettingsWorkbench';
import { storedChoice, storedFlag, systemIsDark } from './Preferences';
import type { Theme } from './Preferences';
import { InventoryFilters, filterSummary } from './InventoryFilters';
import { ReleaseCalendar } from './ReleaseCalendar';
import type { ProfileData } from './ProfileSettings';
import { TagChips, TagsPage, WorkTagEditor, useTagCatalog } from './Tags';
import { Cover, EmptyState, Loading, WorkDetail, IssuesPage, JobsPage, PublicationBadges } from './components';
import type { WorkDetailHandle } from './components';
import { initialNavigation, useWorkbenchNavigation } from './useWorkbenchNavigation';
import type { InventoryPosition, Page } from './useWorkbenchNavigation';
import { useInventoryFeed } from './useInventoryFeed';
import { useWorkspaceTimezoneBootstrap } from './WorkspaceTimezone';
import { motionIsReduced } from './Preferences';

export type Notice = { kind: 'success' | 'error' | 'info'; message: string };
const EMPTY_STATS = { total: 0, pending: 0, to_make: 0, published: 0, es_published: 0, patreon_published: 0, issues: 0 };
const PAGE_SIZE = 24;
export default function App() {
  useI18n();
  useWorkspaceTimezoneBootstrap();
  const [initial] = useState(initialNavigation);
  const [page, setPage] = useState<Page>(initial.route.page);
  const [detailId, setDetailId] = useState<number | null>(initial.route.workId);
  const [filter, setFilter] = useState<Filter>(initial.inventory?.filter || initial.route.filter);
  const [issuesOnly, setIssuesOnly] = useState(initial.inventory?.issuesOnly || false);
  const [query, setQuery] = useState(initial.inventory?.query || '');
  const [search, setSearch] = useState(initial.inventory?.search || '');
  const [number, setNumber] = useState(initial.inventory?.number || 1);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [tagWork, setTagWork] = useState<Work | null>(null);
  const [linkWork, setLinkWork] = useState<{ work: Work; kind: WorkLinkKind } | null>(null);
  const [tagIds, setTagIds] = useState<number[]>(initial.inventory?.tagIds || (initial.inventory?.tagId ? [initial.inventory.tagId] : []));
  const tagId = tagIds.length === 1 ? tagIds[0] : null;
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [sortPlatform, setSortPlatform] = useState<'es' | 'patreon'>(initial.inventory?.sortPlatform || storedChoice<'es' | 'patreon'>('workbench-sort-platform', ['es', 'patreon'], 'es'));
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>(initial.inventory?.sortDirection || storedChoice<'asc' | 'desc'>('workbench-sort-direction', ['asc', 'desc'], 'desc'));
  const [untaggedOnly, setUntaggedOnly] = useState(initial.inventory?.untaggedOnly || false);
  const [capabilities, setCapabilities] = useState<Capabilities>({ can_open_folder: false, reason: t("正在检查访问端能力") });
  const [notice, setNotice] = useState<Notice | null>(null);
  const [profile, setProfile] = useState<ProfileData>({ name: 'Funscript', bio: '脚本工作台', avatar: null, revision: 0 });
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileError, setProfileError] = useState('');
  const [profileRetry, setProfileRetry] = useState(0);
  useEffect(() => {
    const icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (!icon) return;
    const avatarType = profile.avatar?.match(/^data:(image\/(?:png|jpeg|webp));base64,/);
    icon.href = avatarType ? profile.avatar! : '/favicon.svg';
    icon.type = avatarType?.[1] || 'image/svg+xml';
  }, [profile.avatar]);
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
  const [theme, setTheme] = useState<Theme>(() => storedChoice('workbench-theme', ['auto', 'light', 'dark'], 'auto'));
  const [dark, setDark] = useState(theme === 'dark' || theme === 'auto' && systemIsDark());
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => storedFlag('workbench-sidebar-collapsed'));
  const [spring, setSpring] = useState(() => storedFlag('workbench-spring', true));
  const [reduceMotion, setReduceMotion] = useState(() => storedFlag('workbench-reduce-motion'));
  const [view, setView] = useState<'gallery' | 'list' | 'tags'>(() => { if (initial.inventory) return initial.inventory.view; try { const saved = localStorage.getItem('workbench-view'); return saved === 'list' || saved === 'tags' ? saved : 'gallery'; } catch { return 'gallery'; } });
  const { catalog, error: tagError, refresh: refreshTags } = useTagCatalog(revision);
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const detailGuard = useRef<WorkDetailHandle>(null);
  const restorePosition = useRef<InventoryPosition | undefined>(initial.route.page === 'inventory' ? initial.inventory : undefined);
  const params = new URLSearchParams({ q: search, status: filter, issues_only: String(issuesOnly), sort_platform: sortPlatform, sort_direction: sortDirection });
  if (tagIds.length) params.set('tag_ids', [...tagIds].sort((a, b) => a - b).join(','));
  if (untaggedOnly) params.set('untagged_only', 'true');
  const inventoryKey = params.toString();
  const feed = useInventoryFeed(inventoryKey, number, revision, page === 'inventory', initial.inventory?.snapshotId, !!job && job.type === 'scan' && isActiveJob(job));
  const { inventory, setInventory, error, loading } = feed;
  const bottom = useRef<HTMLDivElement>(null);
  const appendPending = useRef(false);
  const [showBackToTop, setShowBackToTop] = useState(false);
  const loadMore = useCallback(() => { if (feed.hasMore && !feed.loadingMore && !feed.appendError && !appendPending.current) { appendPending.current = true; setNumber(value => value + 1); } }, [feed.hasMore, feed.loadingMore, feed.appendError]);
  useEffect(() => { if (!feed.loadingMore && feed.loadedPages >= Math.min(number, Math.max(1, Math.ceil((inventory?.total || 0) / PAGE_SIZE)))) appendPending.current = false; }, [feed.loadingMore, feed.loadedPages, inventory?.total, number]);
  useEffect(() => {
    if (page !== 'inventory' || !bottom.current || !feed.hasMore || !('IntersectionObserver' in window)) return;
    const observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) loadMore(); }, { rootMargin: '0px 0px 160px 0px' });
    observer.observe(bottom.current); return () => observer.disconnect();
  }, [page, feed.hasMore, loadMore]);
  useEffect(() => {
    const update = () => setShowBackToTop(window.scrollY > window.innerHeight);
    update(); window.addEventListener('scroll', update, { passive: true });
    return () => window.removeEventListener('scroll', update);
  }, []);
  const restoreInventory = (saved: InventoryPosition) => {
    setFilter(saved.filter); setQuery(saved.query); setSearch(saved.search); setNumber(saved.number);
    setIssuesOnly(saved.issuesOnly); setTagIds(saved.tagIds || (saved.tagId ? [saved.tagId] : [])); setUntaggedOnly(saved.untaggedOnly); setView(saved.view);
    if (saved.sortPlatform) setSortPlatform(saved.sortPlatform); if (saved.sortDirection) setSortDirection(saved.sortDirection);
  };
  const { navigate, openWork, backToInventory, returnPage } = useWorkbenchNavigation({
    detailRef: detailGuard,
    getInventory: () => ({ filter, query, search, number, issuesOnly, tagId, tagIds, untaggedOnly, sortPlatform, sortDirection, view, scrollY: window.scrollY, snapshotId: inventory?.snapshot_id }),
    onRoute: (route, saved) => {
      restorePosition.current = undefined;
      setPage(route.page); setDetailId(route.workId);
      if (route.page === 'inventory' && saved.inventory) {
        restoreInventory(saved.inventory); restorePosition.current = saved.inventory;
      } else if (route.page === 'detail' && saved.returnInventory) restoreInventory(saved.returnInventory);
      else { setFilter(route.filter); setNumber(1); setIssuesOnly(false); }
    },
  });
  useEffect(() => {
    if (page !== 'inventory' || loading || feed.loadingMore || !inventory || !restorePosition.current
        || (feed.loadedKey !== inventoryKey && !error) || feed.loadedPages < Math.min(number, Math.max(1, Math.ceil(inventory.total / PAGE_SIZE)))) return;
    const saved = restorePosition.current;
    const frame = requestAnimationFrame(() => {
      if (restorePosition.current !== saved) return;
      restorePosition.current = undefined;
      const target = saved.focusWorkId ? document.querySelector<HTMLButtonElement>(`button[data-work-id="${saved.focusWorkId}"]`) : null;
      (target || document.querySelector<HTMLElement>('.page-header h1'))?.focus({ preventScroll: true });
      window.scrollTo({ top: saved.scrollY, behavior: 'auto' });
    });
    return () => cancelAnimationFrame(frame);
  }, [page, loading, feed.loadingMore, feed.loadedKey, feed.loadedPages, number, inventory, inventoryKey, error]);
  const notify = useCallback((next: Notice) => {
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
    setNotice(next);
    if (next.kind === 'success') noticeTimer.current = setTimeout(() => setNotice(null), 6500);
  }, []);
  useEffect(() => () => { if (noticeTimer.current) clearTimeout(noticeTimer.current); }, []);
  useEffect(() => {
    if (page !== 'inventory' || query.trim() === search) return;
    const timer = setTimeout(() => { setSearch(query.trim()); setNumber(1); }, 300);
    return () => clearTimeout(timer);
  }, [query, search, page]);
  useEffect(() => {
    const controller = new AbortController();
    request<Capabilities>('/api/capabilities', { signal: controller.signal }).then(setCapabilities).catch(error => { if (!controller.signal.aborted) setCapabilities({ can_open_folder: false, reason: t("无法确认主机能力：{0}", {"0": errorMessage(error)}) }); });
    return () => controller.abort();
  }, [revision]);
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
          notify({ kind: result.status === 'failed' ? 'error' : 'success', message: result.status === 'failed' ? t("扫描失败：{0}", {"0": result.error || t("请在扫描记录中查看原因")}) : Number(result.result?.candidates || 0) > 0 ? t("扫描完成，库存已更新。发现 {count} 个扫描候选，请前往待处理确认。", { count: Number(result.result?.candidates) }) : t("扫描完成，库存已更新") });
        }
      } catch (error) { if (alive && !controller.signal.aborted) notify({ kind: 'error', message: t("无法读取扫描进度：{0}", {"0": errorMessage(error)}) }); }
      finally { inFlight = false; }
    };
    const timer = setInterval(() => void poll(), 1800);
    return () => { alive = false; clearInterval(timer); controller?.abort(); };
  }, [job?.id, job?.status, notify]);
  useEffect(() => {
    try {
      localStorage.setItem('workbench-view', view); localStorage.setItem('workbench-theme', theme);
      localStorage.setItem('workbench-sidebar-collapsed', String(sidebarCollapsed));
      localStorage.setItem('workbench-spring', String(spring)); localStorage.setItem('workbench-reduce-motion', String(reduceMotion));
      localStorage.setItem('workbench-sort-platform', sortPlatform); localStorage.setItem('workbench-sort-direction', sortDirection);
    } catch { /* Optional browser preferences only. */ }
    const applyTheme = () => { const next = theme === 'dark' || theme === 'auto' && systemIsDark(); setDark(next); document.documentElement.dataset.theme = next ? 'dark' : 'light'; };
    applyTheme(); document.documentElement.dataset.motion = reduceMotion ? 'reduced' : spring ? 'spring' : 'standard';
    const media = typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: dark)') : null;
    media?.addEventListener('change', applyTheme);
    return () => media?.removeEventListener('change', applyTheme);
  }, [theme, view, sidebarCollapsed, spring, reduceMotion, sortPlatform, sortDirection]);

  const stats = inventory?.stats || EMPTY_STATS;
  const currentJob = isActiveJob(job) ? job : null;
  const busy = submitting || !!currentJob;
  const scan = async () => {
    setSubmitting(true);
    try { const next = await request<Job>('/api/scans', { method: 'POST' }); setJob(next); notify({ kind: 'info', message: t("扫描已提交，你可以继续浏览库存。") }); setRevision(value => value + 1); }
    catch (error) { notify({ kind: 'error', message: t("无法开始扫描：{0}", {"0": errorMessage(error)}) }); }
    finally { setSubmitting(false); }
  };
  const title = page === 'inventory' ? filter === 'to_make' ? t('待制作库存') : filter === 'pending' ? t("待发布库存") : filter === 'es_published' ? t("ES 已发布作品") : filter === 'patreon_published' ? t("Patreon 已发布作品") : filter === 'published' ? t("全部平台已发布作品") : t("脚本库存") : page === 'issues' ? t("待处理") : page === 'jobs' ? t("任务记录") : page === 'tags' ? t("标签管理") : page === 'calendar' ? t("发布日历") : page === 'profile' ? t('个人中心') : t("工作台设置");
  const subtitle = page === 'inventory' ? filter === 'to_make' ? t('添加脚本并扫描或重新匹配后，还需在作品详情手动确认制作完成；有脚本且尚未全部发布的作品才会进入待发布。') : t('查找素材，整理标签，继续你的创作。') : page === 'issues' ? t("查看编号冲突、缺失素材和历史资料的关联问题。") : page === 'jobs' ? t("库存扫描与预览生成的执行进度、结果和失败原因。") : page === 'tags' ? t("统一维护作者与分类，作品绑定标签后复用资料。") : page === 'calendar' ? t("安排发布计划，记录 ES 与 Patreon 的实际发布日期。") : page === 'profile' ? t('你的资料与个人工作台。') : t("当前扫描目录与工作台的运行规则。");
  const tagsSaved = (value: WorkTags) => {
    setInventory(previous => previous ? { ...previous, items: previous.items.map(work => work.id === value.work_id ? { ...work, tags: value.tags, tags_revision: value.tags_revision } : work) } : previous);
    setRevision(value => value + 1); notify({ kind: 'success', message: t("作品标签已保存") });
  };
  const linksSaved = (value: WorkLinks) => {
    setInventory(previous => previous ? { ...previous, items: previous.items.map(work => work.id === value.work_id ? { ...work, ...value, id: work.id } : work) } : previous);
    setRevision(revision => revision + 1); notify({ kind: 'success', message: t("发布链接已保存") });
  };

  const clearFilters = () => { setQuery(''); setSearch(''); setIssuesOnly(false); setTagIds([]); setUntaggedOnly(false); setNumber(1); };
  const selectedSummary = [filterSummary(catalog, tagIds), issuesOnly ? t('仅看异常') : '', untaggedOnly ? t('仅看未标注') : ''].filter(Boolean).join(' · ');
  const changeSortPlatform = (value: 'es' | 'patreon') => { setSortPlatform(value); setNumber(1); };
  const changeSortDirection = (value: 'asc' | 'desc') => { setSortDirection(value); setNumber(1); };
  return <div className={`app-shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
    <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); const main = document.getElementById('main-content'); main?.focus({ preventScroll: true }); main?.scrollIntoView({ block: 'start', behavior: 'auto' }); }}>{t("跳到主要内容")}</a>
    <aside className="sidebar" aria-label={t("工作台导航")}>
      <a className={`brand ${page === 'profile' ? 'active' : ''}`} href="#/profile" onClick={event => { event.preventDefault(); navigate('profile'); }} aria-label={t('个人中心')} title={`${profile.name} · ${t('个人中心')}`} aria-current={page === 'profile' ? 'page' : undefined}><div className="brand-mark">{profile.avatar ? <img src={profile.avatar} alt={t("{0}的头像", {"0": profile.name})} /> : <span>{profile.name.slice(0, 1).toUpperCase()}</span>}</div><div className="brand-text"><strong>{profile.name}</strong><small>{t('个人工作台')}</small></div></a>
      <div className="nav-section"><p className="section-label">{t("作品库")}</p><nav className="nav-list">
        <button title={t("全部库存")} className={(page === 'inventory' || page === 'detail') && filter === 'all' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('inventory')} aria-current={(page === 'inventory' || page === 'detail') && filter === 'all' ? 'page' : undefined}><LayoutGrid size={18} /><span>{t("全部库存")}</span>{inventory && <span className="nav-count">{stats.total}</span>}</button>
        <button title={t("待制作")} className={(page === 'inventory' || page === 'detail') && filter === 'to_make' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('inventory', 'to_make')} aria-current={(page === 'inventory' || page === 'detail') && filter === 'to_make' ? 'page' : undefined}><FilePenLine size={18} /><span>{t('待制作')}</span>{inventory && <span className="nav-count">{stats.to_make ?? 0}</span>}</button>
        <button title={t("待发布")} className={(page === 'inventory' || page === 'detail') && filter === 'pending' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('inventory', 'pending')} aria-current={(page === 'inventory' || page === 'detail') && filter === 'pending' ? 'page' : undefined}><Clock3 size={18} /><span>{t("待发布")}</span>{inventory && <span className="nav-count">{stats.pending}</span>}</button>
        <button title={t("发布日历")} className={page === 'calendar' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('calendar')} aria-current={page === 'calendar' ? 'page' : undefined}><CalendarDays size={18} /><span>{t("发布日历")}</span></button>
        {(['es_published', 'patreon_published'] as const).map(platformFilter => <button key={platformFilter} title={platformFilter === 'es_published' ? t("ES 已发布") : t("Patreon 已发布")} className={(page === 'inventory' || page === 'detail') && filter === platformFilter ? 'nav-item active' : 'nav-item'} onClick={() => navigate('inventory', platformFilter)} aria-current={(page === 'inventory' || page === 'detail') && filter === platformFilter ? 'page' : undefined}><CheckCheck size={18} /><span>{platformFilter === 'es_published' ? t("ES 已发布") : t("Patreon 已发布")}</span>{inventory && <span className="nav-count">{stats[platformFilter] ?? stats.published}</span>}</button>)}
      </nav></div>
      <div className="nav-section"><p className="section-label">{t("工作流")}</p><nav className="nav-list">
        <button title={t("待处理")} className={page === 'issues' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('issues')} aria-current={page === 'issues' ? 'page' : undefined}><CircleAlert size={18} /><span>{t("待处理")}</span>{inventory && stats.issues > 0 && <span className="nav-count issue-count">{stats.issues}</span>}</button>
        <button title={t("任务记录")} className={page === 'jobs' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('jobs')} aria-current={page === 'jobs' ? 'page' : undefined}><RefreshCw size={18} /><span>{t("任务记录")}</span>{currentJob && <span className="activity-dot" aria-label={t("正在扫描")} />}</button>
        <button title={t("标签管理")} className={page === 'tags' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('tags')} aria-current={page === 'tags' ? 'page' : undefined}><TagIcon size={18} /><span>{t("标签管理")}</span></button>
        <button title={t("设置")} className={page === 'settings' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('settings')} aria-current={page === 'settings' ? 'page' : undefined}><Settings2 size={18} /><span>{t("设置")}</span></button>
      </nav></div>
      <div className="sidebar-footer"><LanguageSettings /><button className="theme-toggle" title={dark ? t('切换浅色主题') : t('切换深色主题')} onClick={() => setTheme(dark ? 'light' : 'dark')} aria-label={dark ? t("切换浅色主题") : t("切换深色主题")}>{dark ? <Sun size={17} /> : <Moon size={17} />}<span>{dark ? t('切换浅色') : t('切换深色')}</span></button><button className="theme-toggle sidebar-toggle" aria-expanded={!sidebarCollapsed} aria-label={t(sidebarCollapsed ? '展开侧栏' : '收起侧栏')} title={t(sidebarCollapsed ? '展开侧栏' : '收起侧栏')} onClick={() => setSidebarCollapsed(!sidebarCollapsed)}>{sidebarCollapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}<span>{t(sidebarCollapsed ? '展开侧栏' : '收起侧栏')}</span></button></div>
    </aside>
    <main id="main-content" className="main-content" tabIndex={-1}>
      <div className="page-kicker"><span className="last-updated">{t("最近扫描 ·")}{formatDate(inventory?.last_scan?.at)}</span></div>
      {page !== 'detail' && <header className="page-header"><div><h1 tabIndex={-1}>{title}</h1><p>{subtitle}</p></div>{page === 'inventory' && <button className="button primary" onClick={() => void scan()} disabled={busy}>{busy ? <LoaderCircle size={17} className="spin" /> : <RefreshCw size={17} />}<span>{busy ? t("正在扫描") : t("立即扫描")}</span></button>}</header>}
      {notice && <div className={`notice ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}><span>{notice.kind === 'error' ? <CircleAlert size={18} /> : notice.kind === 'success' ? <Check size={18} /> : <RefreshCw size={18} />}{t(notice.message)}</span><button className="icon-button" aria-label={t("关闭提示")} onClick={() => setNotice(null)}><X size={17} /></button></div>}
      {currentJob && <div className="scan-banner" role="status"><LoaderCircle className="spin" size={16} /><span>{t("后台正在扫描素材并更新封面")}</span><button onClick={() => navigate('jobs')}>{t("查看进度")}<ArrowRight size={14} /></button></div>}
      {page === 'detail' && detailId !== null && <WorkDetail key={detailId} ref={detailGuard} presentation="page" backLabel={({ calendar: '返回发布日历', profile: '返回个人中心', issues: '返回待处理', jobs: '返回任务记录', tags: '返回标签管理' } as Partial<Record<Page, string>>)[returnPage] || '返回库存'} id={detailId} capabilities={capabilities} onClose={backToInventory} onSaved={() => setRevision(value => value + 1)} notify={notify} />}
      {page === 'inventory' && <>
        <div className="toolbar"><label className="search-field"><Search size={18} aria-hidden="true" /><span className="sr-only">{t("搜索编号、标题或标签")}</span><input type="search" placeholder={t("搜索编号、标题、标签…")} value={query} onChange={event => setQuery(event.target.value)} /></label><div className="toolbar-options"><button className={`button filter-button ${selectedSummary ? 'selected' : ''}`} aria-label={t('组合筛选')} aria-expanded={filtersOpen} onClick={() => setFiltersOpen(true)}><SlidersHorizontal size={16} />{t('组合筛选')}{tagIds.length > 0 && <span>{tagIds.length}</span>}</button><div className="view-switch" aria-label={t("库存显示方式")}><button className="icon-button" aria-pressed={view === 'gallery'} onClick={() => setView('gallery')} aria-label={t("封面画廊")}><LayoutGrid size={18} /><span>{t('画廊')}</span></button><button className="icon-button" aria-pressed={view === 'list'} onClick={() => setView('list')} aria-label={t("紧凑目录")}><List size={19} /><span>{t('紧凑目录')}</span></button><button className="icon-button" aria-pressed={view === 'tags'} onClick={() => setView('tags')} aria-label={t("标签列表")}><TagIcon size={18} /><span>{t('标签列表')}</span></button></div></div></div>
        {selectedSummary && <div className="inventory-filter-summary"><button title={selectedSummary} onClick={() => setFiltersOpen(true)}><SlidersHorizontal size={14} /><span>{selectedSummary}</span></button><button className="text-action" onClick={clearFilters}>{t('清除筛选')}</button></div>}
        <div className="inventory-overview"><span><strong>{inventory ? inventory.total : '—'}</strong> {t("个库存", { count: inventory?.total || 0 })}</span><div className="inventory-sort"><span>{t('发布日期')}</span><div className="view-switch" aria-label={t('按平台发布日期排序')}>{(['es', 'patreon'] as const).map(platform => <button key={platform} className="icon-button" aria-pressed={sortPlatform === platform} onClick={() => changeSortPlatform(platform)}>{platform === 'es' ? 'ES' : 'Patreon'}</button>)}</div><button className="icon-button" aria-label={t(sortDirection === 'desc' ? '当前从新到旧，点击切换从旧到新' : '当前从旧到新，点击切换从新到旧')} onClick={() => changeSortDirection(sortDirection === 'desc' ? 'asc' : 'desc')}>{sortDirection === 'desc' ? <ArrowDownWideNarrow size={18} /> : <ArrowUpWideNarrow size={18} />}</button></div></div>
        {tagError && <div className="notice error" role="alert"><span>{t("无法读取标签筛选：")}{t(tagError)}</span><button className="button small" onClick={refreshTags}>{t("重试")}</button></div>}
        {error && <div className="notice error" role="alert"><span><CircleAlert size={18} />{t("无法更新库存：")}{t(error)}. {inventory ? t("当前显示上次读取的数据。") : ''}</span><button className="button small" onClick={() => setRevision(value => value + 1)}>{t("重试")}</button></div>}
        {loading ? <Loading /> : !inventory ? <EmptyState title={t("暂时无法读取库存")} description={t("确认工作台服务已启动，然后重新连接。")}><button className="button" onClick={() => setRevision(value => value + 1)}><RefreshCw size={16} />{t("重新连接")}</button></EmptyState> : !inventory.items.length ? <EmptyState title={search || issuesOnly || tagIds.length || untaggedOnly ? t("没有符合条件的作品") : filter === 'to_make' ? t('暂无待制作作品') : filter === 'pending' ? t('暂无待发布作品') : filter !== 'all' ? t("没有符合条件的作品") : t("这里还没有库存")} description={search || issuesOnly || tagIds.length || untaggedOnly ? t("换一个编号、标题或标签，或取消筛选。") : filter === 'to_make' ? t('无脚本的编号目录会在扫描后显示；补齐脚本后仍需手动确认制作完成。') : filter === 'pending' ? t('已有脚本、制作已确认且尚未全部发布的作品会显示在这里。') : t("将作品文件夹放入配置的扫描目录后，点击“立即扫描”更新库存。")}>{(search || issuesOnly || tagIds.length > 0 || untaggedOnly) && <button className="button" onClick={clearFilters}>{t("清除筛选")}</button>}</EmptyState> : view === 'tags' ? <div className="work-tag-list" aria-label={t("快速标签库存列表")}>{inventory.items.map(work => <article className="work-tag-row" key={work.id} data-inventory-work={work.id}><div className="work-tag-identity"><span className="script-id">{workIdentity(work)}</span><PublicationBadges work={work} />{workDisplayTitle(work) && <h2>{workDisplayTitle(work)}</h2>}</div><TagChips tags={work.tags} durationStatus={work.duration_status} durationError={work.duration_error} /><WorkLinkButtons work={work} onEdit={kind => setLinkWork({ work, kind })} /><button className="button small" onClick={() => setTagWork(work)} aria-label={t("编辑标签 {0}", {"0": workIdentity(work)})}><TagIcon size={15} />{t("编辑标签")}</button></article>)}</div> : <div className={`work-collection ${view}`} aria-label={t("库存作品")}>
          {inventory.items.map(work => <article key={work.id} className="work-card inventory-work-card" data-inventory-work={work.id}><button type="button" className="work-card-target" data-work-id={work.id} onClick={() => view === 'gallery' ? openWork(work.id) : setSelected(work.id)} aria-label={t("查看 {0} {1}", {"0": workIdentity(work), "1": workDisplayTitle(work) || ""}).trim()} /><Cover work={work} /><div className="work-info"><div className="work-topline"><span className="script-id">{workIdentity(work)}</span>{workDisplayTitle(work) && <h2>{workDisplayTitle(work)}</h2>}</div><PublicationBadges work={work} /><TagChips tags={work.tags} durationStatus={work.duration_status} durationError={work.duration_error} /><div className="work-foot"><span className="asset-count"><Film size={15} strokeWidth={1.5} aria-hidden="true" />{work.video_count} {t('视频')}<FileText size={15} strokeWidth={1.5} aria-hidden="true" />{work.script_count} {t('脚本')}</span><button className="card-edit-tags text-action" onClick={() => setTagWork(work)} aria-label={t("编辑标签 {0}", {"0": workIdentity(work)})}><TagIcon size={15} strokeWidth={1.5} aria-hidden="true" />{t('编辑标签')}</button></div><WorkLinkButtons work={work} onEdit={kind => setLinkWork({ work, kind })} />{!!work.issues.length && <span className="issue-label"><CircleAlert size={14} />{work.issues.length} {t("项异常")}</span>}</div></article>)}
        </div>}
        {!!inventory?.total && <div ref={bottom} className="inventory-loadbar" role="status"><span>{t('已加载 {count} / {total} 个库存', { count: inventory.items.length, total: inventory.total })}</span>{feed.loadingMore ? <span><LoaderCircle size={17} className="spin" />{t('正在加载更多库存…')}</span> : feed.appendError ? <span className="feed-error">{t(feed.appendError)}<button className="text-action" onClick={feed.retry}>{t('重试加载')}</button></span> : feed.hasMore ? <button className="text-action" onClick={loadMore}>{t('加载更多')}</button> : <span>{t('已展示全部库存')}</span>}</div>}
        {showBackToTop && <button className="back-to-top" aria-label={t('回到顶部')} onClick={() => window.scrollTo({ top: 0, behavior: motionIsReduced() ? 'auto' : 'smooth' })}><ArrowUp size={19} /><span>{t('回顶')}</span></button>}
      </>}
      {page === 'calendar' && <ReleaseCalendar revision={revision} onSelect={openWork} onChanged={() => setRevision(value => value + 1)} />}
      {page === 'issues' && <IssuesPage revision={revision} onSelect={openWork} onChanged={() => setRevision(value => value + 1)} />}
      {page === 'jobs' && <JobsPage revision={revision} onSelect={openWork} />}
      {page === 'settings' && <SettingsWorkbench capabilities={capabilities} revision={revision} theme={theme} onTheme={setTheme} view={view} onView={setView} spring={spring} onSpring={setSpring} reduceMotion={reduceMotion} onReduceMotion={setReduceMotion} sortPlatform={sortPlatform} onSortPlatform={changeSortPlatform} sortDirection={sortDirection} onSortDirection={changeSortDirection} onScan={() => void scan()} scanning={busy} />}
      {page === 'profile' && <PersonalPage profile={profile} loading={profileLoading} error={profileError} onRetry={() => setProfileRetry(value => value + 1)} onSaved={value => { setProfile(value); notify({ kind: 'success', message: t('工作台资料已保存') }); }} inventory={inventory} onNavigate={navigate} onSelect={openWork} />}
      {page === 'tags' && <TagsPage revision={revision} onChanged={() => setRevision(value => value + 1)} onOpenWork={openWork} />}
      <footer className="page-footer"><span>{t("Funscript 工作台")}</span><span>{t("库存与状态保存在服务端")}</span></footer>
    </main>
    {selected !== null && <WorkDetail id={selected} capabilities={capabilities} onClose={() => setSelected(null)} onSaved={() => setRevision(value => value + 1)} notify={notify} />}
    {tagWork && <WorkTagEditor work={tagWork} onClose={() => setTagWork(null)} onSaved={tagsSaved} />}
    {linkWork && <WorkLinkEditor work={linkWork.work} initialKind={linkWork.kind} onClose={() => setLinkWork(null)} onSaved={linksSaved} />}
    {filtersOpen && <InventoryFilters catalog={catalog} resultCount={inventory?.total} selected={tagIds} onChange={ids => { setTagIds(ids); setNumber(1); }} issuesOnly={issuesOnly} untaggedOnly={untaggedOnly} onIssuesChange={value => { setIssuesOnly(value); setNumber(1); }} onUntaggedChange={value => { setUntaggedOnly(value); setNumber(1); }} onClose={() => setFiltersOpen(false)} />}
  </div>;
}

import { useState } from 'react';
import { ArrowUpRight, CalendarDays, ChevronRight, Palette, Pencil, UserRound, ListChecks } from 'lucide-react';
import { workIdentity } from './api';
import type { Filter, Inventory, Work } from './api';
import { ProfileSettings } from './ProfileSettings';
import type { ProfileData } from './ProfileSettings';
import { Cover } from './components';
import { workDisplayTitle } from './ReleaseDates';
import type { Page } from './useWorkbenchNavigation';
import { translate as t } from './i18n';

export function PersonalPage({ profile, loading, error, onRetry, onSaved, inventory, onNavigate, onSelect }: {
  profile: ProfileData; loading: boolean; error: string; onRetry: () => void; onSaved: (value: ProfileData) => void;
  inventory: Inventory | null; onNavigate: (page: Exclude<Page, 'detail'>, filter?: Filter) => void;
  onSelect: (id: number) => void;
}) {
  const [editing, setEditing] = useState(false);
  const stats = inventory?.stats;
  const counts: [Filter, string, number | undefined][] = [
    ['all', '全部库存', stats?.total], ['pending', '待发布', stats?.pending],
    ['es_published', 'ES 已发布', stats?.es_published], ['patreon_published', 'Patreon 已发布', stats?.patreon_published],
  ];
  const works: Work[] = inventory?.items.slice(0, 3) || [];
  return <div className="personal-page">
    {error && <div className="notice error" role="alert"><span>{t(error)}</span><button className="button small" onClick={onRetry}>{t('重试读取资料')}</button></div>}
    <section className="personal-hero" aria-label={t('个人资料')}>
      <div className="personal-avatar">{profile.avatar ? <img src={profile.avatar} alt={t('{0}的头像', { 0: profile.name })} /> : <span>{profile.name.slice(0, 1).toUpperCase()}</span>}</div>
      <div className="personal-identity"><h2>{profile.name}</h2><span>{t('个人工作台')}</span><p>{profile.bio}</p></div>
      <button className="text-action" disabled={loading || !!error} aria-expanded={editing} aria-controls="personal-editor" onClick={() => setEditing(!editing)}><Pencil size={16} />{t(editing ? '收起编辑' : '编辑资料')}</button>
    </section>
    <div id="personal-editor" hidden={!editing}>
      {!loading && !error && <ProfileSettings profile={profile} onSaved={onSaved} onCancel={() => setEditing(false)} />}
    </div>
    <section className="personal-counts" aria-label={t('我的资料库')}>
      {counts.map(([filter, label, count]) => <button key={filter} onClick={() => onNavigate('inventory', filter)}><strong>{count ?? '—'}</strong><span>{t(label)}</span><ArrowUpRight size={15} aria-hidden="true" /></button>)}
    </section>
    <div className="personal-panels">
      <section className="personal-panel"><h3><UserRound size={17} />{t('个人资料')}</h3><dl><div><dt>{t('姓名')}</dt><dd>{profile.name}</dd></div><div><dt>{t('简介')}</dt><dd>{profile.bio || '—'}</dd></div><div><dt>{t('工作空间')}</dt><dd>{t('个人资料库')}<button className="text-action" onClick={() => onNavigate('settings')}>{t('管理')}<ChevronRight size={14} /></button></dd></div></dl></section>
      <section className="personal-panel"><h3>{t('常用入口')}</h3>
        {([['calendar', '发布日历', CalendarDays], ['jobs', '后台任务', ListChecks], ['settings', '工作台设置', Palette]] as const).map(([page, label, Icon]) => <button className="personal-shortcut" key={page} onClick={() => onNavigate(page)}><span><Icon size={18} />{t(label)}</span><ChevronRight size={15} /></button>)}
      </section>
    </div>
    {works.length > 0 && <section className="personal-current"><header><h3>{t('继续整理')}</h3><button className="text-action" onClick={() => onNavigate('inventory')}>{t('全部库存')}<ArrowUpRight size={15} /></button></header><div className="personal-works">{works.map(work => <button className="personal-work" key={work.id} aria-label={t('查看 {0} {1}', { 0: workIdentity(work), 1: workDisplayTitle(work) || '' }).trim()} onClick={() => onSelect(work.id)}><Cover work={work} /><span><strong>{workIdentity(work)}{work.script_id && work.title && work.title !== work.script_id ? ` · ${work.title}` : ''}</strong><small>{work.tags?.filter(tag => ['author', 'video_type'].includes(tag.category)).map(tag => tag.name).join(' · ')}</small></span></button>)}</div></section>}
  </div>;
}

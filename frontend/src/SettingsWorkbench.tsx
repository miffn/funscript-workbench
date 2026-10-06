import { useState } from 'react';
import { Folder, RefreshCw, Send, Palette, Plug, Sun, Moon, Monitor, LayoutGrid, List, Tags } from 'lucide-react';
import { SettingsPage } from './components';
import { LanguageSettings } from './LanguageSettings';
import { EsTemplateSettings } from './ReleasePosts';
import { SHOW_ES_POSTS } from './features';
import type { Capabilities } from './api';
import type { Theme, InventoryView } from './Preferences';
import { storedChoice } from './Preferences';
import { translate as t } from './i18n';

const sections = [
  ['library', '本地资料库', Folder], ['processing', '扫描与处理', RefreshCw],
  ['publishing', '发布偏好', Send], ['appearance', '外观与交互', Palette], ['integrations', 'MCP 接入', Plug],
] as const;
type Section = typeof sections[number][0];
export function SettingsWorkbench({ capabilities, revision, theme, onTheme, view, onView, spring, onSpring, reduceMotion, onReduceMotion, sortPlatform, onSortPlatform, sortDirection, onSortDirection, onScan, scanning }: {
  capabilities: Capabilities; revision: number; theme: Theme; onTheme: (theme: Theme) => void;
  view: InventoryView; onView: (view: InventoryView) => void;
  spring: boolean; onSpring: (value: boolean) => void; reduceMotion: boolean; onReduceMotion: (value: boolean) => void;
  sortPlatform: 'es' | 'patreon'; onSortPlatform: (value: 'es' | 'patreon') => void;
  sortDirection: 'asc' | 'desc'; onSortDirection: (value: 'asc' | 'desc') => void;
  onScan?: () => void; scanning?: boolean;
}) {
  const [section, setSection] = useState<Section>(() => storedChoice('workbench-settings-section', sections.map(item => item[0]), 'library'));
  const choose = (next: Section) => { setSection(next); try { localStorage.setItem('workbench-settings-section', next); } catch { /* Local preference is optional. */ } };
  const activeSection = sections.find(item => item[0] === section)!;
  return <div className="settings-workbench">
    <nav className="settings-category-nav" aria-label={t('设置分类')}>{sections.map(([key, label, Icon]) => <button key={key} aria-pressed={key === section} onClick={() => choose(key)}><Icon size={17} />{t(label)}</button>)}</nav>
    <div className="settings-workbench-content">
      <header className="settings-section-title"><h2>{t(activeSection[1])}</h2>{section === 'processing' && onScan && <button className="text-action" onClick={onScan} disabled={scanning}><RefreshCw size={15} />{t(scanning ? '正在扫描' : '立即扫描')}</button>}</header>
      <div hidden={!['library', 'processing', 'integrations'].includes(section)}><SettingsPage capabilities={capabilities} revision={revision} section={['library', 'processing', 'integrations'].includes(section) ? section as 'library' | 'processing' | 'integrations' : 'library'} /></div>
      <div hidden={section !== 'publishing'}>
        <section className="settings-card"><div className="section-heading"><h3>{t('发布偏好')}</h3></div><div className="preference-row"><div><strong>{t('默认发布日期')}</strong><p>{t('库存按对应平台的实际发布日期排序，未填写日期置后。')}</p></div><div className="view-switch">{(['es', 'patreon'] as const).map(platform => <button key={platform} className="button small" aria-pressed={sortPlatform === platform} onClick={() => onSortPlatform(platform)}>{platform === 'es' ? 'ES' : 'Patreon'}</button>)}</div></div><div className="preference-row"><strong>{t('排序方向')}</strong><div className="view-switch">{(['desc', 'asc'] as const).map(direction => <button key={direction} className="button small" aria-pressed={sortDirection === direction} onClick={() => onSortDirection(direction)}>{t(direction === 'desc' ? '从新到旧' : '从旧到新')}</button>)}</div></div><div className="preference-row"><div><strong>{t('链接保存')}</strong><p>{t('新增 ES 或 Patreon 帖子链接时，记录该平台已发布及当天日期；计划日期单独维护。')}</p></div></div><div className="preference-row"><strong>{t('日期时区')}</strong><span className="settings-row-value">Asia/Shanghai</span></div></section>
        {SHOW_ES_POSTS && <EsTemplateSettings />}
      </div>
      <div hidden={section !== 'appearance'}>
        <section className="settings-card"><div className="section-heading"><h3>{t('主题外观')}</h3></div><div className="preference-choices">{([['auto', '跟随系统', Monitor, '随系统切换'], ['light', '浅色', Sun, '明亮清晰'], ['dark', '深色', Moon, '降低环境亮度']] as const).map(([value, label, Icon, hint]) => <button key={value} aria-pressed={theme === value} onClick={() => onTheme(value)}><Icon size={22} /><span>{t(label)}</span><small>{t(hint)}</small></button>)}</div></section>
        <section className="settings-card"><div className="section-heading"><h2>{t('库存显示')}</h2></div><div className="preference-choices">{([['gallery', '封面画廊', LayoutGrid], ['list', '紧凑目录', List], ['tags', '标签列表', Tags]] as const).map(([value, label, Icon]) => <button key={value} aria-pressed={view === value} onClick={() => onView(value)}><Icon size={22} /><span>{t(label)}</span></button>)}</div></section>
        <section className="settings-card"><div className="section-heading"><h2>{t('交互与动效')}</h2></div><label className="preference-row preference-toggle"><div><strong>{t('轻柔弹性')}</strong><p>{t('对话框与交互反馈使用轻柔的弹性过渡。')}</p></div><input type="checkbox" checked={spring} disabled={reduceMotion} onChange={event => onSpring(event.target.checked)} /></label><label className="preference-row preference-toggle"><div><strong>{t('减少动态效果')}</strong><p>{t('简化过渡；同时遵循系统的减少动态效果设置。')}</p></div><input type="checkbox" checked={reduceMotion} onChange={event => onReduceMotion(event.target.checked)} /></label><p className="help-text">{t('外观和库存显示仅保存在当前浏览器。')}</p></section>
        <LanguageSettings />
      </div>
    </div>
  </div>;
}

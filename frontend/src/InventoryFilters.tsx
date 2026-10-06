import { useState } from 'react';
import { Check, ChevronLeft, ChevronRight, Search } from 'lucide-react';
import type { TagCatalog, TagCategory } from './api';
import { TagDialog } from './Tags';
import { categoryChoices, categoryName } from './TagCategories';
import { tagColorStyle } from './TagAppearance';
import { tagName, translate, useI18n } from './i18n';
import './TagWorkbench.css';

export interface InventoryFiltersProps {
  catalog: TagCatalog | null;
  selected: number[];
  onChange: (ids: number[]) => void;
  issuesOnly: boolean;
  untaggedOnly: boolean;
  onIssuesChange: (value: boolean) => void;
  onUntaggedChange: (value: boolean) => void;
  onClose: () => void;
  resultCount?: number;
}
export function filterSummary(catalog: TagCatalog | null, selected: number[]) {
  return categoryChoices(catalog).flatMap(([category, label]) => {
    const names = (catalog?.items || []).filter(tag => !tag.deleted && tag.category === category && selected.includes(tag.id)).map(tagName);
    return names.length ? [`${categoryName(category, label)}：${names.join(' / ')}`] : [];
  }).join(' · ');
}

export function InventoryFilters({ catalog, selected, onChange, issuesOnly, untaggedOnly, onIssuesChange, onUntaggedChange, onClose, resultCount }: InventoryFiltersProps) {
  useI18n();
  const [category, setCategory] = useState<TagCategory>('author');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const categories = categoryChoices(catalog);
  const activeCategory = categories.some(([key]) => key === category) ? category : categories[0][0];
  const label = categories.find(([key]) => key === activeCategory)![1];
  const options = (catalog?.items || []).filter(tag => !tag.deleted && tag.category === activeCategory && [tag.name, tagName(tag)].some(name => name.toLowerCase().includes(query.trim().toLowerCase())));
  const totalPages = Math.max(1, Math.ceil(options.length / 18));
  const currentPage = Math.min(page, totalPages - 1);
  const choose = (id: number) => {
    const next = selected.includes(id) ? selected.filter(value => value !== id) : [...selected, id];
    if (next.length && untaggedOnly) onUntaggedChange(false);
    onChange(next);
  };
  const clear = () => { onChange([]); onIssuesChange(false); onUntaggedChange(false); };
  return <TagDialog closeLabel={translate('关闭组合筛选')} className="inventory-filter-dialog" title={translate('组合筛选')} subtitle={translate('按类别查找标签，支持多选')} dirty={false} saving={false} onClose={onClose} footer={<div className="tag-live-footer"><button className="tag-clear-action" onClick={clear} disabled={!selected.length && !issuesOnly && !untaggedOnly}>{translate('清空筛选')}</button><div><span className="help-text" role="status">{typeof resultCount === 'number' ? translate('符合 {count} 个作品', { count: resultCount }) : translate('已选 {count} 项', { count: selected.length + Number(issuesOnly) + Number(untaggedOnly) })}</span><button className="button primary" onClick={onClose}>{translate('查看作品')}</button></div></div>}>
    <div className="inventory-filter-browser"><nav className="inventory-filter-categories" aria-label={translate('筛选类别')}>{categories.map(([key, label]) => <button key={key} aria-pressed={activeCategory === key} onClick={() => { setCategory(key); setPage(0); setQuery(''); }}><span>{categoryName(key, label)}</span><small>{(catalog?.items || []).filter(tag => !tag.deleted && tag.category === key && selected.includes(tag.id)).length || ''}</small></button>)}<div className="inventory-filter-flags"><label><input type="checkbox" checked={issuesOnly} onChange={event => onIssuesChange(event.target.checked)} />{translate('仅看异常')}</label><label><input type="checkbox" checked={untaggedOnly} onChange={event => { if (event.target.checked) onChange([]); onUntaggedChange(event.target.checked); }} />{translate('仅看未标注')}</label></div></nav><section className="inventory-filter-library"><label className="search-field"><Search size={17} /><span className="sr-only">{translate('搜索筛选标签')}</span><input type="search" value={query} onChange={event => { setQuery(event.target.value); setPage(0); }} placeholder={translate('搜索当前类别…')} /></label><div className="inventory-filter-option-head"><h3>{categoryName(activeCategory, label)}</h3><span>{translate('{count} 项', { count: options.length })}</span></div><div className="tag-library-options inventory-filter-values">{options.slice(currentPage * 18, (currentPage + 1) * 18).map(tag => <button key={tag.id} className={`tag-option inventory-filter-value ${tag.category.startsWith('custom_') ? 'custom' : tag.category}`} style={tagColorStyle(tag)} aria-pressed={selected.includes(tag.id)} title={translate('{category}：{name}{suffix}', { category: categoryName(tag.category, tag.category_label || catalog?.category_definitions), name: tagName(tag), suffix: '' })} onClick={() => choose(tag.id)}><Check size={14} aria-hidden="true" /><span className="tag-value">{tagName(tag)}</span></button>)}</div>{!options.length && <p className="tag-library-empty">{catalog ? translate('没有匹配的标签') : translate('正在读取标签资料')}</p>}{totalPages > 1 && <div className="tag-library-pagination"><span>{currentPage + 1} / {totalPages}</span><div><button className="icon-button" disabled={!currentPage} onClick={() => setPage(currentPage - 1)} aria-label={translate('上一页')}><ChevronLeft size={16} /></button><button className="icon-button" disabled={currentPage + 1 === totalPages} onClick={() => setPage(currentPage + 1)} aria-label={translate('下一页')}><ChevronRight size={16} /></button></div></div>}<p className="inventory-filter-rule">{translate('同一类别匹配任意标签，不同类别同时满足。')}</p></section></div>
  </TagDialog>;
}

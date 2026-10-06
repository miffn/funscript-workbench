import { useState } from 'react';
import { Check, Search, X } from 'lucide-react';
import type { TagCatalog, TagCategory } from './api';
import { TagDialog, tagCategories, tagLabel } from './Tags';
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
}
export function filterSummary(catalog: TagCatalog | null, selected: number[]) {
  return tagCategories.flatMap(([category]) => {
    const names = (catalog?.items || []).filter(tag => tag.category === category && selected.includes(tag.id)).map(tagName);
    return names.length ? [`${tagLabel(category)}：${names.join(' / ')}`] : [];
  }).join(' · ');
}

export function InventoryFilters({ catalog, selected, onChange, issuesOnly, untaggedOnly, onIssuesChange, onUntaggedChange, onClose }: InventoryFiltersProps) {
  useI18n();
  const [category, setCategory] = useState<TagCategory>('author');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const options = (catalog?.items || []).filter(tag => tag.category === category && [tag.name, tagName(tag)].some(name => name.toLowerCase().includes(query.trim().toLowerCase())));
  const totalPages = Math.max(1, Math.ceil(options.length / 18));
  const currentPage = Math.min(page, totalPages - 1);
  const choose = (id: number) => {
    const next = selected.includes(id) ? selected.filter(value => value !== id) : [...selected, id];
    if (next.length && untaggedOnly) onUntaggedChange(false);
    onChange(next);
  };
  const clear = () => { onChange([]); onIssuesChange(false); onUntaggedChange(false); };
  return <TagDialog title={translate('组合筛选')} subtitle={translate('库存')} dirty={false} saving={false} onClose={onClose} footer={<div className="tag-live-footer"><button className="button small" onClick={clear} disabled={!selected.length && !issuesOnly && !untaggedOnly}>{translate('清空筛选')}</button><button className="button primary" onClick={onClose}>{translate('完成')}</button></div>}>
    <p className="help-text">{translate('同一类别匹配任意标签，不同类别同时满足。')}</p><div className="inventory-filter-flags"><label><input type="checkbox" checked={issuesOnly} onChange={event => onIssuesChange(event.target.checked)} />{translate('仅看异常')}</label><label><input type="checkbox" checked={untaggedOnly} onChange={event => { if (event.target.checked) onChange([]); onUntaggedChange(event.target.checked); }} />{translate('仅看未标注')}</label></div>
    {selected.length > 0 && <div className="inventory-selected-tags" aria-label={translate('已选筛选标签')}>{(catalog?.items || []).filter(tag => selected.includes(tag.id)).map(tag => <button key={tag.id} className={`tag-option selected ${tag.category}`} onClick={() => choose(tag.id)} aria-label={translate('取消筛选 {name}', { name: tagName(tag) })}>{tagName(tag)}<X size={13} aria-hidden="true" /></button>)}</div>}
    <div className="inventory-filter-browser"><nav className="inventory-filter-categories" aria-label={translate('筛选类别')}>{tagCategories.map(([key, label]) => <button key={key} aria-pressed={category === key} onClick={() => { setCategory(key); setPage(0); setQuery(''); }}>{translate(label)}<small>{(catalog?.items || []).filter(tag => tag.category === key && selected.includes(tag.id)).length || ''}</small></button>)}</nav><section className="inventory-filter-library"><label className="search-field"><Search size={17} /><span className="sr-only">{translate('搜索筛选标签')}</span><input type="search" value={query} onChange={event => { setQuery(event.target.value); setPage(0); }} placeholder={translate('搜索本类标签…')} /></label><div className="tag-options tag-library-options">{options.slice(currentPage * 18, (currentPage + 1) * 18).map(tag => <button key={tag.id} className={`tag-option ${tag.category}${selected.includes(tag.id) ? ' selected' : ''}`} aria-pressed={selected.includes(tag.id)} onClick={() => choose(tag.id)}>{tagName(tag)}{selected.includes(tag.id) && <Check size={14} aria-hidden="true" />}</button>)}</div>{!options.length && <p className="tag-library-empty">{catalog ? translate('没有匹配的标签') : translate('正在读取标签资料')}</p>}<div className="tag-library-pagination"><button className="button small" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>{translate('上一页')}</button><span>{currentPage + 1} / {totalPages}</span><button className="button small" disabled={currentPage + 1 === totalPages} onClick={() => setPage(currentPage + 1)}>{translate('下一页')}</button></div></section></div>
  </TagDialog>;
}
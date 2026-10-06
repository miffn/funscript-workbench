// @vitest-environment jsdom
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { InventoryFilters, filterSummary } from './InventoryFilters';
import type { Tag, TagCatalog } from './api';
import { categoryChoices, categoryName, registerCategoryDefinitions } from './TagCategories';
import { tagColorStyle } from './TagAppearance';
import { setLanguage } from './i18n';
const tag = (id: number, category: Tag['category'], name: string): Tag => ({ id, category, name, support_status: 'unknown', support_url: null, revision: 1, usage_count: 0 });
const catalog: TagCatalog = { items: [tag(1, 'author', '作者 A'), tag(2, 'author', '作者 B'), tag(3, 'video_type', 'Real'), tag(4, 'video_type', 'Anime'), ...Array.from({ length: 40 }, (_, index) => tag(100 + index, 'author', `作者 ${index}`))], categories: [] };
function Filters({ initial = [] as number[], untagged = false, data = catalog }: { initial?: number[]; untagged?: boolean; data?: TagCatalog }) {
  const [selected, onChange] = useState(initial);
  const [issuesOnly, onIssuesChange] = useState(false);
  const [untaggedOnly, onUntaggedChange] = useState(untagged);
  return <><output aria-label="筛选结果">{selected.join(',')}</output><InventoryFilters catalog={data} selected={selected} onChange={onChange} issuesOnly={issuesOnly} onIssuesChange={onIssuesChange} untaggedOnly={untaggedOnly} onUntaggedChange={onUntaggedChange} onClose={vi.fn()} /></>;
}
beforeEach(() => { localStorage.clear(); Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function(this: HTMLDialogElement) { this.open = true; } }); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); registerCategoryDefinitions([]); act(() => setLanguage({ language: 'zh-CN', revision: 0 })); });
describe('multi category inventory filter', () => {
  it('permits multiple tags per category and retains them when switching categories', () => {
    render(<Filters />);
    fireEvent.click(screen.getByRole('button', { name: '作者 A' }));
    fireEvent.click(screen.getByRole('button', { name: '作者 B' }));
    fireEvent.click(within(screen.getByRole('navigation', { name: '筛选类别' })).getByRole('button', { name: '视频类型' }));
    fireEvent.click(screen.getByRole('button', { name: 'Real' }));
    expect(screen.getByLabelText('筛选结果').textContent).toBe('1,2,3');
    fireEvent.click(within(screen.getByRole('navigation', { name: '筛选类别' })).getByRole('button', { name: '作者2' }));
    fireEvent.click(screen.getByRole('button', { name: '作者 A' }));
    expect(screen.getByLabelText('筛选结果').textContent).toBe('2,3');
    expect(filterSummary(catalog, [1, 2, 3])).toBe('作者：作者 A / 作者 B · 视频类型：Real');
  });
  it('uses bounded pages and search rather than expanding all tags', () => {
    render(<Filters />);
    const visibleOptions = () => document.querySelectorAll('.tag-library-options .tag-option').length;
    expect(visibleOptions()).toBe(18);
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(visibleOptions()).toBe(18);
    fireEvent.change(screen.getByLabelText('搜索筛选标签'), { target: { value: '作者 39' } });
    expect(visibleOptions()).toBe(1); expect(screen.queryByRole('button', { name: '下一页' })).toBeNull();
  });
  it('makes tag selections and untagged-only mutually exclusive but keeps issues independent', () => {
    render(<Filters untagged />);
    fireEvent.click(screen.getByRole('button', { name: '作者 A' }));
    expect((screen.getByLabelText('仅看未标注') as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByLabelText('仅看异常'));
    fireEvent.click(screen.getByLabelText('仅看未标注'));
    expect(screen.getByLabelText('筛选结果').textContent).toBe('');
    expect((screen.getByLabelText('仅看异常') as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '清空筛选' }));
    expect((screen.getByLabelText('仅看异常') as HTMLInputElement).checked).toBe(false);
    expect((screen.getByLabelText('仅看未标注') as HTMLInputElement).checked).toBe(false);
  });
  it('filters, searches and summarizes dynamic category tags while excluding deleted tags', () => {
    const definitions = [{ category: 'custom_1' as const, name: '场景', is_custom: true, revision: 1 }, { category: 'custom_2' as const, name: '情绪', is_custom: true, revision: 1 }];
    const data: TagCatalog = { items: [...catalog.items, { ...tag(200, 'custom_1', '夜间'), category_label: '场景' }, { ...tag(201, 'custom_1', '清晨'), category_label: '场景' }, { ...tag(202, 'custom_2', '柔和'), category_label: '情绪' }, { ...tag(203, 'custom_1', '已删除'), category_label: '场景', deleted: true }], categories: ['custom_1', 'custom_2'], category_definitions: definitions };
    render(<Filters data={data} />);
    const categories = screen.getByRole('navigation', { name: '筛选类别' });
    fireEvent.click(within(categories).getByRole('button', { name: '场景' }));
    expect(screen.getByRole('heading', { name: '场景' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '夜间' }).title).toBe('场景：夜间');
    expect(screen.queryByRole('button', { name: '已删除' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '夜间' }));
    fireEvent.change(screen.getByLabelText('搜索筛选标签'), { target: { value: '清晨' } });
    expect(screen.queryByRole('button', { name: '夜间' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '清晨' }));
    fireEvent.click(within(categories).getByRole('button', { name: '情绪' }));
    fireEvent.click(screen.getByRole('button', { name: '柔和' }));
    expect(screen.getByLabelText('筛选结果').textContent).toBe('200,201,202');
    expect(filterSummary(data, [1, 200, 201, 202, 203])).toBe('作者：作者 A · 场景：夜间 / 清晨 · 情绪：柔和');
    expect(categoryChoices(data, false).some(([category]) => category === 'duration')).toBe(false);
  });
  it('keeps user category names untranslated and retains renamed definitions in tooltips', () => {
    setLanguage({ language: 'en', revision: 1 });
    const definitions = [{ category: 'custom_7' as const, name: '作者', is_custom: true, revision: 1 }];
    registerCategoryDefinitions(definitions);
    const data: TagCatalog = { items: [{ ...tag(204, 'custom_7', 'User label'), category_label: '作者' }], categories: ['custom_7'], category_definitions: definitions };
    render(<Filters data={data} />);
    const categories = screen.getByRole('navigation', { name: 'Filter categories' });
    expect(within(categories).getByRole('button', { name: 'Author' })).toBeTruthy();
    fireEvent.click(within(categories).getByRole('button', { name: '作者' }));
    expect(screen.getByRole('button', { name: 'User label' }).title).toBe('作者: User label');
    expect(categoryName('custom_7')).toBe('作者');
    registerCategoryDefinitions([{ ...definitions[0], name: '人物', revision: 2 }]);
    expect(categoryName('custom_7')).toBe('人物');
    expect(tagColorStyle({ category: 'custom_7', color_light: '#123456', color_dark: null })).toEqual({ '--tag-fg': 'light-dark(#123456, var(--tag-custom-fg))' });
  });
});

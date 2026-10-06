// @vitest-environment jsdom
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { InventoryFilters, filterSummary } from './InventoryFilters';
import type { Tag, TagCatalog } from './api';
const tag = (id: number, category: Tag['category'], name: string): Tag => ({ id, category, name, support_status: 'unknown', support_url: null, revision: 1, usage_count: 0 });
const catalog: TagCatalog = { items: [tag(1, 'author', '作者 A'), tag(2, 'author', '作者 B'), tag(3, 'video_type', 'Real'), tag(4, 'video_type', 'Anime'), ...Array.from({ length: 40 }, (_, index) => tag(100 + index, 'author', `作者 ${index}`))], categories: [] };
function Filters({ initial = [] as number[], untagged = false }) {
  const [selected, onChange] = useState(initial);
  const [issuesOnly, onIssuesChange] = useState(false);
  const [untaggedOnly, onUntaggedChange] = useState(untagged);
  return <><output aria-label="筛选结果">{selected.join(',')}</output><InventoryFilters catalog={catalog} selected={selected} onChange={onChange} issuesOnly={issuesOnly} onIssuesChange={onIssuesChange} untaggedOnly={untaggedOnly} onUntaggedChange={onUntaggedChange} onClose={vi.fn()} /></>;
}
beforeEach(() => { localStorage.clear(); Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function(this: HTMLDialogElement) { this.open = true; } }); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
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
});
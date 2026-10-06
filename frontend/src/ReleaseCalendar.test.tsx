// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import App from './App';
import { calendarDays, ReleaseCalendar } from './ReleaseCalendar';
import type { CalendarData, CalendarMode, CalendarWork, PublicationPlatform } from './api';

const work = (id: number, fields: Partial<CalendarWork> = {}): CalendarWork => ({ id, script_id: `S00${id}`, title: `作品 ${id}`, revision: `r${id}`, es_published: false, patreon_published: false, es_published_date: null, patreon_published_date: null, es_planned_date: null, patreon_planned_date: null, ...fields });
const response = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
let works: CalendarWork[];
let fetchMock: ReturnType<typeof vi.fn>;
let operation = 0;
let undoWork: CalendarWork | null;
let mutationError = 0;
let getError = 0;
const select = vi.fn();
const changed = vi.fn();
function data(month: string): CalendarData {
  const days = calendarDays(month);
  return { month, today: '2026-10-03', works, events: works.flatMap(item => (['es', 'patreon'] as const).flatMap(platform => (['actual', 'planned'] as const).flatMap(mode => {
    const date = item[`${platform}_${mode === 'actual' ? 'published' : 'planned'}_date`];
    return date && days.includes(date) ? [{ key: `${item.id}:${platform}:${mode}`, work_id: item.id, script_id: item.script_id, title: item.title, platform, mode, date, published: item[`${platform}_published`], revision: item.revision }] : [];
  }))) };
}
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(new Date('2026-10-02T18:00:00Z'));
  select.mockClear(); changed.mockClear(); operation = 0; undoWork = null; mutationError = 0; getError = 0;
  works = [work(1), work(2, { es_planned_date: '2026-10-03', patreon_planned_date: '2026-10-03' }), work(3, { es_published: true, es_published_date: '2026-10-05', patreon_published: true, patreon_published_date: '2026-10-06' })];
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      if (mutationError) return response({ detail: 'Changed concurrently' }, mutationError);
      if (url.includes('/undo')) { const index = works.findIndex(item => item.id === undoWork!.id); works[index] = { ...undoWork!, revision: `r${++operation}` }; return response({ work: works[index], operation: { id: operation, undone: true }, message: '已撤销上次操作' }); }
      const body = JSON.parse(String(init.body)) as { work_id: number; platforms: PublicationPlatform[]; mode: CalendarMode; date: string | null };
      const index = works.findIndex(item => item.id === body.work_id); undoWork = { ...works[index] };
      const next = { ...works[index], revision: `r${++operation}` };
      for (const platform of body.platforms) { next[`${platform}_${body.mode === 'actual' ? 'published' : 'planned'}_date`] = body.date; if (body.mode === 'actual' && body.date) next[`${platform}_published`] = true; }
      works[index] = next;
      return response({ work: next, operation: { id: operation, undone: false }, message: '日历已保存' });
    }
    if (url.startsWith('/api/release-calendar?')) return getError ? response({ detail: '连接失败' }, getError) : response(data(url.split('month=')[1]));
    if (url.startsWith('/api/works?')) return response({ items: [], total: 0, stats: { total: 3, pending: 2, published: 1, issues: 0 }, last_scan: null });
    if (url === '/api/capabilities') return response({ can_open_folder: false, reason: '' });
    if (url === '/api/profile') return response({ name: 'Funscript', bio: '脚本工作台', avatar: null, revision: 0 });
    return response({ items: [] });
  });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); window.location.hash = ''; });
const posts = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
function renderCalendar() { return render(<ReleaseCalendar onSelect={select} onChanged={changed} />); }
async function ready() { return screen.findByRole('button', { name: '添加到当天 S001' }); }
const transfer = () => ({ effectAllowed: '', dropEffect: '', setData: vi.fn(), getData: vi.fn() });
function dragTo(element: HTMLElement, date: string) { const dataTransfer = transfer(); fireEvent.dragStart(element, { dataTransfer }); const day = screen.getByRole('button', { name: date }).closest('.calendar-day')!; fireEvent.dragOver(day, { dataTransfer }); fireEvent.drop(day, { dataTransfer }); fireEvent.dragEnd(element, { dataTransfer }); }

it('uses Beijing today, a Monday-to-Sunday grid and shows both platform plans', async () => {
  renderCalendar(); await ready();
  expect(screen.getByRole('heading', { name: '2026 年 10 月' })).toBeTruthy();
  expect(screen.getByRole('button', { name: '2026-10-03' }).getAttribute('aria-pressed')).toBe('true');
  expect(calendarDays('2026-10')).toHaveLength(35);
  expect(calendarDays('2026-10')[0]).toBe('2026-09-28');
  expect(calendarDays('2026-10').at(-1)).toBe('2026-11-01');
  expect(screen.getByRole('button', { name: 'S002 ES 计划 2026-10-03' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'S002 Patreon 计划 2026-10-03' })).toBeTruthy();
  expect(posts()).toHaveLength(0);
});

it('switches to the agenda and edits the same persisted platform record', async () => {
  renderCalendar(); await ready();
  fireEvent.click(screen.getByRole('button', { name: '日程' }));
  expect(screen.queryByRole('group', { name: '2026-10 月历' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'S003 ES 已发布 2026-10-05' }));
  fireEvent.change(screen.getByLabelText('记录日期'), { target: { value: '2026-10-09' } });
  fireEvent.click(screen.getByRole('button', { name: '保存日期' }));
  await screen.findByRole('button', { name: 'S003 ES 已发布 2026-10-09' });
  expect(JSON.parse(posts()[0][1].body)).toMatchObject({ work_id: 3, platforms: ['es'], mode: 'actual', date: '2026-10-09' });
  fireEvent.click(screen.getByRole('button', { name: '月历' }));
  expect(screen.getByRole('group', { name: '2026-10 月历' })).toBeTruthy();
});

it('uses platform segments and schedules a pending platform from the day sidebar', async () => {
  renderCalendar(); await ready();
  fireEvent.click(within(screen.getByRole('group', { name: '日历平台筛选' })).getByRole('button', { name: 'Patreon' }));
  expect(screen.queryByRole('button', { name: '安排 S001 ES' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '安排 S001 Patreon' }));
  await screen.findByRole('button', { name: 'S001 Patreon 计划 2026-10-03' });
  expect(JSON.parse(posts()[0][1].body)).toMatchObject({ work_id: 1, platforms: ['patreon'], mode: 'planned', date: '2026-10-03' });
  expect(works[0].patreon_published).toBe(false);
  expect(works[0].patreon_published_date).toBeNull();
});

it('searches and schedules an unnumbered folder work using its name and stable work ID', async () => {
  works.push(work(4, { script_id: null, title: '普通文件夹' }));
  renderCalendar(); await ready();
  fireEvent.change(screen.getByLabelText('搜索日历库存'), { target: { value: '普通文件夹' } });
  expect(screen.getByLabelText('拖动作品 普通文件夹')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '添加到当天 普通文件夹' }));
  await screen.findByRole('button', { name: '普通文件夹 ES 已发布 2026-10-03' });
  expect(JSON.parse(posts()[0][1].body).work_id).toBe(4);
  expect(screen.queryByText(/^null$/)).toBeNull();
});

it('shows one work record for both platforms and plans/actual dates on the same day', async () => {
  works[1] = { ...works[1], es_published: true, patreon_published: true, es_published_date: '2026-10-03', patreon_published_date: '2026-10-03' };
  renderCalendar(); await ready();
  const group = screen.getByRole('group', { name: 'S002 2026-10-03 发布记录' });
  expect(within(group).getAllByText('S002')).toHaveLength(1);
  expect(within(group).getAllByRole('button')).toHaveLength(4);
  const daily = screen.getByRole('group', { name: 'S002 当日记录' });
  expect(within(daily).getAllByText('S002')).toHaveLength(1);
  expect(within(daily).getAllByRole('button')).toHaveLength(4);
  const day = screen.getByRole('button', { name: '2026-10-03' }).closest('.calendar-day')!;
  expect(within(day as HTMLElement).getByText('1 条记录')).toBeTruthy();
  expect(day.querySelector('.calendar-mobile-count')?.textContent).toBe('1');
  expect(document.querySelector('.calendar-day-heading > span')?.textContent).toBe('1 条');
  fireEvent.click(within(screen.getByRole('group', { name: '日历平台筛选' })).getByRole('button', { name: 'Patreon' }));
  fireEvent.click(within(screen.getByRole('group', { name: '日历日期类型' })).getByRole('button', { name: '计划' }));
  expect(within(screen.getByRole('group', { name: 'S002 当日记录' })).getAllByRole('button')).toHaveLength(1);
});

it('counts and limits work groups, keeping full child IDs independent', async () => {
  works[0] = { ...works[0], es_published: true, patreon_published: true, es_published_date: '2026-10-03', patreon_published_date: '2026-10-03' };
  works[2] = { ...works[2], es_published_date: '2026-10-03', patreon_published_date: '2026-10-03' };
  works.push(work(4, { script_id: 'S001_001', es_planned_date: '2026-10-03' }));
  renderCalendar(); await screen.findByRole('group', { name: 'S001 当日记录' });
  const day = screen.getByRole('button', { name: '2026-10-03' }).closest('.calendar-day')!;
  expect(day.querySelectorAll('.calendar-event-group')).toHaveLength(3);
  expect(within(day as HTMLElement).getByRole('button', { name: '另 1 条' })).toBeTruthy();
  expect(day.querySelector('.calendar-mobile-count')?.textContent).toBe('4');
  expect(document.querySelectorAll('.calendar-entry-group')).toHaveLength(4);
  expect(screen.getByRole('group', { name: 'S001 当日记录' })).toBeTruthy();
  expect(screen.getByRole('group', { name: 'S001_001 当日记录' })).toBeTruthy();
});

it('moves only the dragged platform from a merged work record', async () => {
  works[2] = { ...works[2], patreon_published_date: '2026-10-05' };
  renderCalendar(); await ready();
  expect(within(screen.getByRole('group', { name: 'S003 2026-10-05 发布记录' })).getAllByRole('button')).toHaveLength(2);
  dragTo(screen.getByRole('button', { name: 'S003 ES 已发布 2026-10-05' }), '2026-10-08');
  await screen.findByRole('button', { name: 'S003 ES 已发布 2026-10-08' });
  expect(works[2].patreon_published_date).toBe('2026-10-05');
  expect(within(screen.getByRole('group', { name: 'S003 2026-10-05 发布记录' })).getAllByRole('button')).toHaveLength(1);
});

it('adds an actual publication to the selected day by a keyboard/touch-compatible button', async () => {
  renderCalendar(); await ready(); fireEvent.click(screen.getByRole('button', { name: '2026-10-04' }));
  expect(posts()).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '添加到当天 S001' }));
  await screen.findByRole('button', { name: 'S001 ES 已发布 2026-10-04' });
  expect(JSON.parse(posts()[0][1].body)).toEqual({ work_id: 1, platforms: ['es'], mode: 'actual', date: '2026-10-04', expected_revision: 'r1' });
  expect(works[0].es_published).toBe(true); expect(works[0].patreon_published).toBe(false);
  expect(changed).toHaveBeenCalledOnce();
});

it('records both platforms as a plan without changing actual dates or publication states', async () => {
  renderCalendar(); await ready();
  fireEvent.change(screen.getByLabelText('维护平台'), { target: { value: 'both' } });
  fireEvent.click(screen.getByLabelText('安排发布计划'));
  fireEvent.click(screen.getByRole('button', { name: '添加到当天 S001' }));
  await screen.findByRole('button', { name: 'S001 Patreon 计划 2026-10-03' });
  expect(JSON.parse(posts()[0][1].body)).toMatchObject({ platforms: ['es', 'patreon'], mode: 'planned' });
  expect(works[0]).toMatchObject({ es_published: false, patreon_published: false, es_published_date: null, patreon_published_date: null, es_planned_date: '2026-10-03', patreon_planned_date: '2026-10-03' });
});

it('drags a known inventory candidate to a date and ignores foreign drag data', async () => {
  renderCalendar(); await ready();
  fireEvent.drop(screen.getByRole('button', { name: '2026-10-04' }).closest('.calendar-day')!, { dataTransfer: { getData: () => '{"kind":"work","id":1}' } });
  expect(posts()).toHaveLength(0);
  dragTo(screen.getByLabelText('拖动作品 S001'), '2026-10-04');
  await screen.findByRole('button', { name: 'S001 ES 已发布 2026-10-04' });
  expect(posts()).toHaveLength(1);
});

it('moving an existing event preserves its own platform and mode despite toolbar settings', async () => {
  renderCalendar(); await ready();
  fireEvent.change(screen.getByLabelText('维护平台'), { target: { value: 'both' } }); fireEvent.click(screen.getByLabelText('安排发布计划'));
  dragTo(screen.getByRole('button', { name: 'S003 ES 已发布 2026-10-05' }), '2026-10-08');
  await screen.findByRole('button', { name: 'S003 ES 已发布 2026-10-08' });
  expect(JSON.parse(posts()[0][1].body)).toMatchObject({ work_id: 3, platforms: ['es'], mode: 'actual', date: '2026-10-08' });
  expect(works[2].patreon_published_date).toBe('2026-10-06');
});

it('edits dates, opens work details, and removes an actual date without unpublishing', async () => {
  renderCalendar(); await ready(); fireEvent.click(screen.getByRole('button', { name: 'S003 ES 已发布 2026-10-05' }));
  fireEvent.click(screen.getByRole('button', { name: '打开作品详情' })); expect(select).toHaveBeenCalledWith(3);
  fireEvent.change(screen.getByLabelText('记录日期'), { target: { value: '2026-10-09' } }); fireEvent.click(screen.getByRole('button', { name: '保存日期' }));
  await screen.findByRole('button', { name: 'S003 ES 已发布 2026-10-09' });
  fireEvent.click(screen.getByRole('button', { name: 'S003 ES 已发布 2026-10-09' })); fireEvent.click(screen.getByRole('button', { name: '移除日期' }));
  await waitFor(() => expect(works[2].es_published_date).toBeNull());
  expect(works[2].es_published).toBe(true); expect(works[2].patreon_published_date).toBe('2026-10-06');
  expect(JSON.parse(posts().at(-1)![1].body)).toMatchObject({ platforms: ['es'], mode: 'actual', date: null });
});

it('removes one platform plan and restores it using the persisted undo operation', async () => {
  renderCalendar(); await ready(); fireEvent.click(screen.getByRole('button', { name: 'S002 ES 计划 2026-10-03' })); fireEvent.click(screen.getByRole('button', { name: '移除计划' }));
  await waitFor(() => expect((screen.getByRole('button', { name: '撤销上次操作' }) as HTMLButtonElement).disabled).toBe(false));
  expect(works[1].es_planned_date).toBeNull(); expect(works[1].patreon_planned_date).toBe('2026-10-03');
  fireEvent.click(screen.getByRole('button', { name: '撤销上次操作' }));
  await screen.findByText('已撤销上次操作');
  expect(posts().at(-1)![0]).toBe('/api/release-calendar/operations/1/undo');
  expect(works[1].es_planned_date).toBe('2026-10-03');
});

it('refreshes a revision conflict without silently retrying the write', async () => {
  renderCalendar(); await ready(); mutationError = 409;
  const before = fetchMock.mock.calls.filter(([url]) => url.startsWith('/api/release-calendar?')).length;
  fireEvent.click(screen.getByRole('button', { name: '添加到当天 S001' }));
  await screen.findByText('作品资料已被更新，日历已重新读取。请检查后再操作。');
  await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url.startsWith('/api/release-calendar?')).length).toBeGreaterThan(before));
  expect(posts()).toHaveLength(1); expect(works[0].es_published_date).toBeNull();
});

it('blocks conflicting undo, keeps current data and explains the conflict', async () => {
  renderCalendar(); await ready(); fireEvent.click(screen.getByRole('button', { name: '添加到当天 S001' }));
  await waitFor(() => expect((screen.getByRole('button', { name: '撤销上次操作' }) as HTMLButtonElement).disabled).toBe(false));
  mutationError = 409; fireEvent.click(screen.getByRole('button', { name: '撤销上次操作' }));
  await screen.findByText('这条记录已有后续修改，无法撤销。日历已重新读取，请直接编辑当前记录。');
  expect(screen.queryByRole('button', { name: '撤销上次操作' })).toBeNull();
  expect(works[0].es_published_date).toBe('2026-10-03');
});

it('filters both platform and record kind, retains search through month navigation, and returns to today', async () => {
  renderCalendar(); await ready();
  fireEvent.click(within(screen.getByRole('group', { name: '日历平台筛选' })).getByRole('button', { name: 'Patreon' })); fireEvent.click(within(screen.getByRole('group', { name: '日历日期类型' })).getByRole('button', { name: '计划' }));
  expect(screen.queryByRole('button', { name: 'S002 ES 计划 2026-10-03' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'S003 Patreon 已发布 2026-10-06' })).toBeNull();
  expect(screen.getByRole('button', { name: 'S002 Patreon 计划 2026-10-03' })).toBeTruthy();
  fireEvent.change(screen.getByLabelText('搜索日历库存'), { target: { value: 'S001' } });
  expect(screen.queryByLabelText('拖动作品 S002')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '下个月' })); await screen.findByRole('group', { name: '2026-11 月历' });
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === '/api/release-calendar?month=2026-11')).toBe(true));
  expect((screen.getByLabelText('搜索日历库存') as HTMLInputElement).value).toBe('S001');
  fireEvent.click(screen.getByRole('button', { name: '今天' }));
  await screen.findByRole('group', { name: '2026-10 月历' });
  expect(screen.getByRole('button', { name: '2026-10-03' }).getAttribute('aria-pressed')).toBe('true');
});

it('reports actual dates with a pending status and allows same-day publication to be marked again', async () => {
  works[0].es_published_date = '2026-10-03';
  renderCalendar(); await ready();
  expect(screen.getByRole('button', { name: 'S001 ES 实际日期 · 待发布 2026-10-03' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '添加到当天 S001' }));
  await screen.findByRole('button', { name: 'S001 ES 已发布 2026-10-03' });
});

it('holds the editor revision while a details change refreshes the surrounding calendar', async () => {
  const rendered = renderCalendar(); await ready();
  fireEvent.click(screen.getByRole('button', { name: 'S003 ES 已发布 2026-10-05' }));
  fireEvent.change(screen.getByLabelText('记录日期'), { target: { value: '2026-10-09' } });
  works[2] = { ...works[2], revision: 'external-update', es_published_date: '2026-10-08' };
  rendered.rerender(<ReleaseCalendar revision={1} onSelect={select} onChanged={changed} />);
  await screen.findByRole('button', { name: 'S003 ES 已发布 2026-10-08' });
  await waitFor(() => expect((screen.getByRole('button', { name: '保存日期' }) as HTMLButtonElement).disabled).toBe(false));
  mutationError = 409; fireEvent.click(screen.getByRole('button', { name: '保存日期' }));
  await screen.findByText('作品资料已被更新，日历已重新读取。请检查后再操作。');
  expect(JSON.parse(posts()[0][1].body).expected_revision).toBe('r3');
  expect(screen.queryByLabelText('记录日期')).toBeNull();
  expect(works[2].es_published_date).toBe('2026-10-08');
});

it('prevents writes from stale data after a failed refresh, with retry recovery', async () => {
  const rendered = renderCalendar(); await ready(); getError = 503;
  rendered.rerender(<ReleaseCalendar revision={1} onSelect={select} onChanged={changed} />);
  await screen.findByText('无法读取日历：连接失败');
  expect((screen.getByRole('button', { name: '添加到当天 S001' }) as HTMLButtonElement).disabled).toBe(true);
  getError = 0; fireEvent.click(screen.getByRole('button', { name: '重试' }));
  await waitFor(() => expect((screen.getByRole('button', { name: '添加到当天 S001' }) as HTMLButtonElement).disabled).toBe(false));
});

it('exposes the calendar route and existing work detail callback from the application navigation', async () => {
  window.location.hash = '#/calendar'; render(<App />);
  await ready();
  expect(screen.getByRole('heading', { name: '发布日历', level: 1 })).toBeTruthy();
  expect(screen.getByRole('button', { name: '发布日历' }).getAttribute('aria-current')).toBe('page');
  expect(within(screen.getByLabelText('日历库存候选')).getByText('快速添加作品')).toBeTruthy();
});

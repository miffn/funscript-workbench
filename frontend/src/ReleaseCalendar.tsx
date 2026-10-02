import { useEffect, useMemo, useRef, useState } from 'react';
import type { DragEvent } from 'react';
import { ArrowLeft, ArrowRight, CalendarDays, Check, CircleAlert, ExternalLink, GripVertical, LoaderCircle, Plus, Search, Trash2, Undo2 } from 'lucide-react';
import { ApiError, errorMessage, request } from './api';
import type { CalendarData, CalendarEvent, CalendarMode, CalendarResult, CalendarWork, PublicationPlatform } from './api';
import './ReleaseCalendar.css';

type DragItem = { kind: 'work'; id: number } | { kind: 'event'; key: string };
const PLATFORM_LABEL = { es: 'ES', patreon: 'Patreon' };
const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日'];
export function beijingToday() {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  return ['year', 'month', 'day'].map(type => parts.find(part => part.type === type)!.value).join('-');
}
function isoDate(date: Date) { return date.toISOString().slice(0, 10); }
function monthDate(month: string) { return new Date(`${month}-01T00:00:00Z`); }
export function calendarDays(month: string) {
  const first = monthDate(month);
  first.setUTCDate(1 - (first.getUTCDay() + 6) % 7);
  const end = monthDate(month); end.setUTCMonth(end.getUTCMonth() + 1); end.setUTCDate(0);
  end.setUTCDate(end.getUTCDate() + (7 - end.getUTCDay()) % 7);
  const days: string[] = [];
  while (first <= end) { days.push(isoDate(first)); first.setUTCDate(first.getUTCDate() + 1); }
  return days;
}
function shiftMonth(month: string, offset: number) {
  const date = monthDate(month); date.setUTCMonth(date.getUTCMonth() + offset); return isoDate(date).slice(0, 7);
}
function titleFor(work: { script_id: string; title: string }) { return work.title.trim() && work.title.trim() !== work.script_id ? work.title : ''; }
function dateLabel(date: string) { return `${Number(date.slice(5, 7))} 月 ${Number(date.slice(8, 10))} 日`; }
function eventLabel(event: CalendarEvent) { return event.mode === 'planned' ? '计划' : event.published ? '已发布' : '实际日期 · 待发布'; }
export function groupCalendarEvents(events: CalendarEvent[]) {
  const groups = new Map<number, CalendarEvent[]>();
  events.forEach(event => {
    const group = groups.get(event.work_id) || [];
    group.push(event); groups.set(event.work_id, group);
  });
  return [...groups.values()];
}

export function ReleaseCalendar({ revision = 0, onSelect, onChanged }: { revision?: number; onSelect: (id: number) => void; onChanged: () => void }) {
  const [initialToday] = useState(beijingToday);
  const [month, setMonth] = useState(initialToday.slice(0, 7));
  const [selectedDate, setSelectedDate] = useState(initialToday);
  const [data, setData] = useState<CalendarData | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [platforms, setPlatforms] = useState<'es' | 'patreon' | 'both'>('es');
  const [mode, setMode] = useState<CalendarMode>('actual');
  const [platformFilter, setPlatformFilter] = useState<'all' | PublicationPlatform>('all');
  const [modeFilter, setModeFilter] = useState<'all' | CalendarMode>('all');
  const [query, setQuery] = useState('');
  const [selectedEventKey, setSelectedEventKey] = useState<string | null>(null);
  const [selectedEventRevision, setSelectedEventRevision] = useState('');
  const [editDate, setEditDate] = useState('');
  const [message, setMessage] = useState('');
  const [actionError, setActionError] = useState('');
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const drag = useRef<DragItem | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [undoId, setUndoId] = useState<number | null>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true);
    request<CalendarData>(`/api/release-calendar?month=${month}`, { signal: controller.signal }).then(value => {
      if (controller.signal.aborted) return;
      setData(value); setLoadError('');
    }).catch(error => { if (!controller.signal.aborted) setLoadError(errorMessage(error)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [month, revision, refresh]);

  const days = useMemo(() => calendarDays(month), [month]);
  const visibleEvents = (data?.events || []).filter(event => (platformFilter === 'all' || event.platform === platformFilter) && (modeFilter === 'all' || event.mode === modeFilter));
  const eventsByDay = new Map<string, CalendarEvent[]>();
  visibleEvents.forEach(event => { const events = eventsByDay.get(event.date) || []; events.push(event); eventsByDay.set(event.date, events); });
  const selectedEvents = eventsByDay.get(selectedDate) || [];
  const selectedGroups = groupCalendarEvents(selectedEvents);
  const selectedEvent = data?.events.find(event => event.key === selectedEventKey);
  const search = query.trim().toLocaleLowerCase();
  const candidates = (data?.works || []).filter(work => `${work.script_id} ${work.title}`.toLocaleLowerCase().includes(search));
  const selectedPlatforms: PublicationPlatform[] = platforms === 'both' ? ['es', 'patreon'] : [platforms];
  const disabled = busy || loading || !!loadError || !data || data.month !== month;
  const today = data?.today || initialToday;

  function selectDay(date: string) { setSelectedDate(date); setSelectedEventKey(null); }
  function selectEvent(event: CalendarEvent) { setSelectedDate(event.date); setSelectedEventKey(event.key); setSelectedEventRevision(event.revision); setEditDate(event.date); }
  function navigateMonth(offset: number) {
    const next = shiftMonth(month, offset); setMonth(next); selectDay(`${next}-01`);
  }
  function startDrag(event: DragEvent, item: DragItem) {
    if (disabled) { event.preventDefault(); return; }
    drag.current = item; event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('application/x-workbench-calendar', JSON.stringify(item));
  }
  function endDrag() { drag.current = null; setDropTarget(null); }
  async function commit(work: CalendarWork, targets: PublicationPlatform[], targetMode: CalendarMode, date: string | null) {
    if (busyRef.current || disabled) return;
    busyRef.current = true; setBusy(true); setActionError(''); setMessage('');
    try {
      const result = await request<CalendarResult>('/api/release-calendar', { method: 'POST', body: JSON.stringify({ work_id: work.id, platforms: targets, mode: targetMode, date, expected_revision: work.revision }) });
      if (!mounted.current) return;
      setMessage(result.message); setUndoId(result.operation.id); setSelectedEventKey(null);
      if (date) { setSelectedDate(date); setMonth(date.slice(0, 7)); }
      setRefresh(value => value + 1); onChanged();
    } catch (error) {
      if (!mounted.current) return;
      const conflict = error instanceof ApiError && error.status === 409;
      setActionError(conflict ? '作品资料已被更新，日历已重新读取。请检查后再操作。' : errorMessage(error));
      if (conflict) { setSelectedEventKey(null); setRefresh(value => value + 1); onChanged(); }
    } finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }
  async function undo() {
    if (busyRef.current || disabled || undoId === null) return;
    busyRef.current = true; setBusy(true); setActionError('');
    try {
      const result = await request<CalendarResult>(`/api/release-calendar/operations/${undoId}/undo`, { method: 'POST', body: '{}' });
      if (!mounted.current) return;
      setMessage(result.message); setUndoId(null); setSelectedEventKey(null); setRefresh(value => value + 1); onChanged();
    } catch (error) {
      if (!mounted.current) return;
      const conflict = error instanceof ApiError && error.status === 409;
      setActionError(conflict ? '这条记录已有后续修改，无法撤销。日历已重新读取，请直接编辑当前记录。' : errorMessage(error));
      if (conflict) { setUndoId(null); setRefresh(value => value + 1); onChanged(); }
    } finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }
  function dropOnDay(event: DragEvent, date: string) {
    event.preventDefault(); const item = drag.current; endDrag();
    if (!item || disabled) return;
    if (item.kind === 'work') {
      const work = data?.works.find(work => work.id === item.id);
      if (work) void commit(work, selectedPlatforms, mode, date);
    } else {
      const entry = data?.events.find(entry => entry.key === item.key);
      const work = data?.works.find(work => work.id === entry?.work_id);
      if (entry && work && entry.date !== date) void commit(work, [entry.platform], entry.mode, date);
    }
  }
  function editEvent(date: string | null) {
    const work = data?.works.find(work => work.id === selectedEvent?.work_id);
    if (selectedEvent && work) void commit({ ...work, revision: selectedEventRevision }, [selectedEvent.platform], selectedEvent.mode, date);
  }

  return <section className="release-calendar" aria-label="发布日历">
    <div className="calendar-toolbar">
      <div className="calendar-month-nav"><button className="icon-button" aria-label="上个月" onClick={() => navigateMonth(-1)} disabled={busy}><ArrowLeft size={18} /></button><h2>{Number(month.slice(0, 4))} 年 {Number(month.slice(5, 7))} 月</h2><button className="icon-button" aria-label="下个月" onClick={() => navigateMonth(1)} disabled={busy}><ArrowRight size={18} /></button><button className="button small" onClick={() => { setMonth(today.slice(0, 7)); selectDay(today); }} disabled={busy}>今天</button></div>
      <div className="calendar-filters"><label>平台筛选<select value={platformFilter} onChange={event => setPlatformFilter(event.target.value as typeof platformFilter)}><option value="all">全部平台</option><option value="es">ES</option><option value="patreon">Patreon</option></select></label><label>记录筛选<select value={modeFilter} onChange={event => setModeFilter(event.target.value as typeof modeFilter)}><option value="all">全部记录</option><option value="planned">发布计划</option><option value="actual">已发布记录</option></select></label></div>
    </div>
    {loadError && <div className="notice error" role="alert"><span><CircleAlert size={17} />无法读取日历：{loadError}</span><button className="button small" onClick={() => setRefresh(value => value + 1)}>重试</button></div>}
    {actionError && <div className="notice error" role="alert"><span><CircleAlert size={17} />{actionError}</span></div>}
    {(message || busy) && <div className="calendar-feedback" role="status"><span>{busy ? <LoaderCircle size={17} className="spin" /> : <Check size={17} />}{busy ? '正在保存日历…' : message}</span>{undoId !== null && <button className="button small" onClick={() => void undo()} disabled={disabled}><Undo2 size={16} />撤销上次操作</button>}</div>}
    <div className="calendar-layout">
      <div className="calendar-main">
        <div className="calendar-legend"><span className="calendar-platform es">ES</span><span className="calendar-platform patreon">Patreon</span><span className="calendar-legend-actual">已发布</span><span className="calendar-legend-plan">计划</span>{loading && <span className="calendar-reading" role="status"><LoaderCircle size={14} className="spin" />读取中</span>}</div>
        <div className="calendar-grid" role="group" aria-label={`${month} 月历`} aria-busy={loading}>
          {WEEKDAYS.map(day => <div className="calendar-weekday" key={day}>周{day}</div>)}
          {days.map(date => { const entries = eventsByDay.get(date) || []; const groups = groupCalendarEvents(entries); return <div key={date} className={`calendar-day ${date.slice(0, 7) !== month ? 'outside' : ''} ${date === today ? 'today' : ''} ${date === selectedDate ? 'selected' : ''} ${date === dropTarget ? 'drop-target' : ''}`} onDragOver={event => { if (drag.current && !disabled) { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; setDropTarget(date); } }} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropTarget(null); }} onDrop={event => dropOnDay(event, date)}>
            <button className="calendar-date-button" aria-label={date} aria-pressed={date === selectedDate} onClick={() => selectDay(date)}><span>{Number(date.slice(8, 10))}</span>{date === today && <span className="calendar-today-label">今天</span>}<span className="sr-only">{groups.length} 条记录</span></button>
            <div className="calendar-day-events">{groups.slice(0, 3).map(group => <div className="calendar-event-group" key={group[0].work_id} role="group" aria-label={`${group[0].script_id} ${date} 发布记录`}>
              <strong className="script-id">{group[0].script_id}</strong>
              <div className="calendar-group-platforms">{group.map(entry => <button key={entry.key} type="button" className={`calendar-event ${entry.platform} ${entry.mode}`} draggable={!disabled} onDragStart={event => startDrag(event, { kind: 'event', key: entry.key })} onDragEnd={endDrag} onClick={() => selectEvent(entry)} aria-label={`${entry.script_id} ${PLATFORM_LABEL[entry.platform]} ${eventLabel(entry)} ${entry.date}`} title={`${entry.script_id} ${titleFor(entry)} · ${PLATFORM_LABEL[entry.platform]} ${eventLabel(entry)}`}><span>{PLATFORM_LABEL[entry.platform]}</span><span>{eventLabel(entry)}</span></button>)}</div>
            </div>)}{groups.length > 3 && <button className="calendar-more" onClick={() => selectDay(date)}>另 {groups.length - 3} 条</button>}</div>
            {!!groups.length && <div className="calendar-mobile-count" aria-hidden="true">{entries.some(entry => entry.platform === 'es') && <span className="es" />}{entries.some(entry => entry.platform === 'patreon') && <span className="patreon" />}<span>{groups.length}</span></div>}
          </div>; })}
        </div>
        <div className="calendar-selected-day"><div className="calendar-day-heading"><h3><CalendarDays size={18} />{dateLabel(selectedDate)}的记录</h3><span>{selectedGroups.length} 条</span></div>{!selectedGroups.length ? <p className="calendar-empty">当天暂无{modeFilter === 'planned' ? '计划' : modeFilter === 'actual' ? '发布' : ''}记录，可从库存添加。</p> : <div className="calendar-entry-list">{selectedGroups.map(group => <div className="calendar-entry-group" key={group[0].work_id} role="group" aria-label={`${group[0].script_id} 当日记录`}>
          <div className="calendar-entry-title"><strong className="script-id">{group[0].script_id}</strong>{titleFor(group[0]) && <span>{titleFor(group[0])}</span>}</div>
          <div className="calendar-group-platforms">{group.map(entry => <button className={`calendar-entry ${entry.platform} ${entry.mode} ${entry.key === selectedEventKey ? 'active' : ''}`} key={entry.key} onClick={() => selectEvent(entry)} draggable={!disabled} onDragStart={event => startDrag(event, { kind: 'event', key: entry.key })} onDragEnd={endDrag} aria-label={`${entry.script_id} ${PLATFORM_LABEL[entry.platform]} ${eventLabel(entry)} 当日记录`}>{PLATFORM_LABEL[entry.platform]} · {eventLabel(entry)}</button>)}</div>
        </div>)}</div>}
          {selectedEvent && <div className="calendar-event-editor"><h4>{selectedEvent.script_id} · {PLATFORM_LABEL[selectedEvent.platform]} {selectedEvent.mode === 'planned' ? '发布计划' : '实际发布'}</h4><label>记录日期<input type="date" value={editDate} onChange={event => setEditDate(event.target.value)} disabled={disabled} min="1900-01-01" max="9999-12-31" /></label><div className="calendar-editor-actions"><button className="button primary" disabled={disabled || !editDate || editDate === selectedEvent.date} onClick={() => editEvent(editDate)}>保存日期</button><button className="button" onClick={() => onSelect(selectedEvent.work_id)}><ExternalLink size={16} />打开作品详情</button><button className="button calendar-remove" disabled={disabled} onClick={() => editEvent(null)}><Trash2 size={16} />移除{selectedEvent.mode === 'planned' ? '计划' : '日期'}</button></div><p className="calendar-help">{selectedEvent.mode === 'planned' ? '取消计划不会改变实际发布日期和发布状态。' : '仅清除该平台的发布日期，已发布状态和帖子链接保留。'}</p></div>}
        </div>
      </div>
      <aside className="calendar-inventory" aria-label="日历库存候选">
        <h3>快速添加作品</h3><p className="calendar-help">选中日期，把作品拖入日历，或点击“添加到当天”。</p>
        <div className="calendar-add-controls"><label>维护平台<select value={platforms} onChange={event => setPlatforms(event.target.value as typeof platforms)} disabled={busy}><option value="es">ES</option><option value="patreon">Patreon</option><option value="both">ES 和 Patreon</option></select></label><fieldset><legend>维护方式</legend><label><input type="radio" name="calendar-mode" checked={mode === 'actual'} onChange={() => setMode('actual')} disabled={busy} />记录实际发布</label><label><input type="radio" name="calendar-mode" checked={mode === 'planned'} onChange={() => setMode('planned')} disabled={busy} />安排发布计划</label></fieldset></div>
        <div className={`calendar-target-note ${mode}`}><CalendarDays size={17} /><span>{dateLabel(selectedDate)} · {platforms === 'both' ? 'ES 和 Patreon' : PLATFORM_LABEL[platforms]}<small>{mode === 'actual' ? '保存实际日期，并标记对应平台已发布' : '只保存计划日期，不改变发布状态'}</small></span></div>
        <label className="search-field calendar-search"><Search size={17} /><span className="sr-only">搜索日历库存</span><input type="search" placeholder="搜索编号或标题…" value={query} onChange={event => setQuery(event.target.value)} /></label>
        <div className="calendar-candidate-heading"><span>可选库存</span><span>{candidates.length} 个</span></div>
        <div className="calendar-candidates">{candidates.map(work => { const alreadyAdded = selectedPlatforms.every(platform => (work[`${platform}_${mode === 'actual' ? 'published' : 'planned'}_date`] === selectedDate && (mode === 'planned' || work[`${platform}_published`]))); return <article className="calendar-candidate" key={work.id} draggable={!disabled} onDragStart={event => startDrag(event, { kind: 'work', id: work.id })} onDragEnd={endDrag} aria-label={`拖动作品 ${work.script_id}`}><div className="calendar-candidate-title"><GripVertical size={16} aria-hidden="true" /><strong className="script-id">{work.script_id}</strong>{titleFor(work) && <span title={work.title}>{titleFor(work)}</span>}</div><div className="calendar-candidate-status"><span className="es">ES {work.es_published ? '已发布' : '待发布'}</span><span className="patreon">Patreon {work.patreon_published ? '已发布' : '待发布'}</span></div><button className="button small" disabled={disabled || alreadyAdded} aria-label={`${alreadyAdded ? '已添加当日' : '添加到当天'} ${work.script_id}`} onClick={() => void commit(work, selectedPlatforms, mode, selectedDate)}>{alreadyAdded ? <Check size={15} /> : <Plus size={15} />}{alreadyAdded ? '已添加当日' : '添加到当天'}</button></article>; })}{!loading && !candidates.length && <p className="calendar-empty">{query ? '没有符合搜索条件的作品' : '暂无库存，先手动扫描素材。'}</p>}</div>
      </aside>
    </div>
  </section>;
}

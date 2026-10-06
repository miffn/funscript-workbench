// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { formatDate } from './api';
import { getWorkspaceTimezone, setWorkspaceTimezone, useWorkspaceTimezoneBootstrap, WorkspaceTimezoneSettings, workspaceToday } from './WorkspaceTimezone';

const choices = ['Asia/Shanghai', 'America/Los_Angeles', 'UTC'];
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
let revision: number;
beforeEach(() => {
 revision = getWorkspaceTimezone().revision + 1;
 setWorkspaceTimezone({ timezone: 'Asia/Shanghai', revision, choices });
 vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(new Date('2026-10-06T01:30:00Z'));
 Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function(this: HTMLDialogElement) { this.open = true; } });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); setWorkspaceTimezone({ timezone: 'Asia/Shanghai', revision: getWorkspaceTimezone().revision + 1, choices }); });

it('searches and saves the shared time zone, updating UTC timestamps and today across a date boundary', async () => {
 const fetchMock = vi.fn(async (_path: string, init?: RequestInit) => response(init?.method === 'PUT'
   ? { timezone: 'America/Los_Angeles', revision: revision + 1, choices }
   : { timezone: 'Asia/Shanghai', revision, choices }));
 vi.stubGlobal('fetch', fetchMock);
 render(<WorkspaceTimezoneSettings />);
 fireEvent.click(screen.getByRole('button', { name: 'Asia/Shanghai' }));
 await screen.findByRole('button', { name: 'America/Los_Angeles' });
 fireEvent.change(screen.getByLabelText('搜索时区'), { target: { value: 'Los_Angeles' } });
 expect(screen.queryByRole('button', { name: 'UTC' })).toBeNull();
 fireEvent.click(screen.getByRole('button', { name: 'America/Los_Angeles' }));
 fireEvent.click(screen.getByRole('button', { name: '保存时区' }));
 await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
 expect(JSON.parse(fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT')![1]!.body as string)).toEqual({ timezone: 'America/Los_Angeles', expected_revision: revision });
 expect(workspaceToday()).toBe('2026-10-05');
 expect(formatDate('2026-10-06T01:30:00Z')).toContain('18:30');
 expect(formatDate('2026-10-06')).toBe('2026-10-06');
});

it('keeps a conflicting selection, guards unsaved closing and never overwrites the newer server version', async () => {
 const fetchMock = vi.fn(async (_path: string, init?: RequestInit) => init?.method === 'PUT'
   ? response({ detail: '工作台时区已被其他页面修改' }, 409)
   : response({ timezone: 'Asia/Shanghai', revision, choices }));
 vi.stubGlobal('fetch', fetchMock);
 render(<WorkspaceTimezoneSettings />);
 fireEvent.click(screen.getByRole('button', { name: 'Asia/Shanghai' }));
 fireEvent.click(await screen.findByRole('button', { name: 'UTC' }));
 fireEvent.click(screen.getByRole('button', { name: '关闭时区选择' }));
 expect(screen.getByText('时区修改尚未保存')).toBeTruthy();
 fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
 fireEvent.click(screen.getByRole('button', { name: '保存时区' }));
 await screen.findByRole('alert');
 expect(screen.getByRole('button', { name: 'UTC' }).getAttribute('aria-pressed')).toBe('true');
 expect((screen.getByRole('button', { name: '保存时区' }) as HTMLButtonElement).disabled).toBe(true);
 expect(getWorkspaceTimezone().timezone).toBe('Asia/Shanghai');
 expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1);
});

it('ignores a delayed bootstrap response after a newer shared choice was saved', async () => {
 let finish!: (result: Response) => void;
 vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { finish = resolve; })));
 function Bootstrap() { const value = useWorkspaceTimezoneBootstrap(); return <span>{value.timezone}</span>; }
 render(<Bootstrap />);
 act(() => setWorkspaceTimezone({ timezone: 'America/Los_Angeles', revision: revision + 1, choices }));
 await act(async () => finish(response({ timezone: 'Asia/Shanghai', revision, choices })));
 expect(screen.getByText('America/Los_Angeles')).toBeTruthy();
});

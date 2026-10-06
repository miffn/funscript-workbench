import { getLanguage, translate as t } from './i18n';

export type Status = 'pending' | 'published';
export type Filter = 'all' | Status | 'to_make' | 'es_published' | 'patreon_published';
export interface Issue { type: string; message: string; script_id?: string | null; work_id?: number | null; paths?: string[] }
export interface Directory { id: number; windows_path: string; path: string; available: boolean }
export interface Asset { id: number; name: string; relative_path: string; kind: string; axis?: string | null; size: number; directory_id: number }
export type TagCategory = 'author' | 'video_type' | 'axis_type' | 'release_type' | 'tier' | 'duration' | 'custom';
export interface Tag { id: number; category: TagCategory; name: string; support_url: string | null; support_status: 'unknown' | 'none' | 'url'; revision: number; usage_count: number; support_candidates?: string[] }
export interface WorkTags { work_id: number; tags: Tag[]; tags_revision: number }
export type WorkLinkKind = 'patreon' | 'video' | 'script' | 'es';
export type WorkLinkValues = Record<WorkLinkKind, string>;
export interface WorkLinks {
  work_id: number; links: WorkLinkValues; links_revision: number;
  es_published?: boolean; patreon_published?: boolean;
  es_published_date?: string | null; patreon_published_date?: string | null;
  es_planned_date?: string | null; patreon_planned_date?: string | null;
  publication_revision?: string;
}
export interface TagCatalog { items: Tag[]; categories: string[]; import_report?: Record<string, unknown> | null }
export interface Work {
  id: number; script_id: string | null; title: string; status: Status; video_type?: string | null; axis_type?: string | null;
  video_count: number; script_count: number; cover_url: string | null; issues: Issue[]; directories: Directory[];
  updated_at: string; notes?: string; assets?: Asset[]; metadata?: Record<string, unknown>; tags?: Tag[]; tags_revision?: number;
  production_required?: boolean; production_confirmed_at?: string | null; production_revision?: number;
  association_status?: 'available' | 'missing' | 'unavailable' | 'conflict' | 'unlinked'; association_revision?: number;
  preview_key?: string | null; preview_stale?: boolean;
  links?: WorkLinkValues; links_revision?: number;
  es_published?: boolean; patreon_published?: boolean;
  es_published_date?: string | null; patreon_published_date?: string | null;
  es_planned_date?: string | null; patreon_planned_date?: string | null;
  duration_seconds?: number | null; duration_minutes?: number | null; duration_status?: string; duration_error?: string | null;
  duration_last_known_seconds?: number | null; duration_last_known_minutes?: number | null;
}
export type PublicationPlatform = 'es' | 'patreon';
export type CalendarMode = 'actual' | 'planned';
export interface CalendarWork {
  id: number; script_id: string | null; title: string; revision: string;
  es_published: boolean; patreon_published: boolean;
  es_published_date: string | null; patreon_published_date: string | null;
  es_planned_date: string | null; patreon_planned_date: string | null;
}
export interface CalendarEvent {
  key: string; work_id: number; script_id: string | null; title: string;
  platform: PublicationPlatform; mode: CalendarMode; date: string;
  published: boolean; revision: string;
}
export interface CalendarData { month: string; today: string; events: CalendarEvent[]; works: CalendarWork[] }
export interface CalendarResult { work: CalendarWork; operation: { id: number; undone: boolean }; message: string }
export function workIdentity(work: { id?: number; work_id?: number | null; script_id?: string | null; title?: string | null }) {
  return work.script_id?.trim() || work.title?.trim() || `work-${work.id ?? work.work_id ?? 'unknown'}`;
}
export function isPublished(work: Work, platform: PublicationPlatform) { return work[`${platform}_published`] ?? work.status === 'published'; }
export interface Job { id: number; type: string; status: string; created_at: string; started_at?: string | null; finished_at?: string | null; progress?: number; message?: string; result?: Record<string, unknown> | null; inputs?: Record<string, unknown>; error?: string | null }
export type PreviewAxis = 'stroke' | 'surge' | 'sway' | 'twist' | 'roll' | 'pitch';
export interface PreviewMatching { work_id: number; video_asset_id: number | null; mode: 'auto' | 'manual'; revision: number; script_asset_ids: Partial<Record<PreviewAxis, number>>; issues: string[]; videos: Asset[]; scripts: Asset[]; job: Job | null; source_changed: boolean }
export interface PreviewFile { filename: string; kind: 'video' | 'gif' | 'heatmap'; clip_index: number; width: number; height: number; url: string; size: number }
export interface PreviewState { job: Job | null; files: PreviewFile[]; output_dir: string; windows_path: string; error?: string | null; preview_key?: string; stale?: boolean }
export type IdentificationMode = 'numbered' | 'folder';
export interface ScanCandidate { id: number; name: string; script_id?: string | null; path: string; windows_path: string; root_path: string; available: boolean; revision: number; missing_work_ids: number[]; video_count: number; script_count: number }
export interface CandidateWork { id: number; script_id: string | null; title: string; association_revision: number }
export interface ScanCandidates { items: ScanCandidate[]; works: CandidateWork[]; total: number }
export interface Inventory { items: Work[]; total: number; page: number; page_size: number; stats: { total: number; pending: number; to_make?: number; published: number; es_published?: number; patreon_published?: number; issues: number }; last_scan: { at: string; [key: string]: unknown } | null }
export interface Capabilities { can_open_folder: boolean; reason: string }
export interface Settings { roots: (string | Record<string, unknown>)[]; scan_interval_seconds: number; scan_roots_revision?: number; [key: string]: unknown }

export class ApiError extends Error {
  status: number;
  rawMessage: string;
  constructor(status: number, message: string) { super(message); this.status = status; this.rawMessage = message; this.name = 'ApiError'; }
}
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, headers: { ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers } });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const data = await response.json();
      if (typeof data.detail === 'string') message = data.detail;
      else if (typeof data.message === 'string') message = data.message;
    } catch { /* The HTTP status still gives a useful error if the gateway returns HTML. */ }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}
export function errorMessage(error: unknown) { return error instanceof Error ? error.message : '操作失败，请稍后重试'; }
export function formatDate(value?: string | null): string {
  if (!value) return t('尚无记录');
  const date = new Date(value.endsWith('Z') || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat(getLanguage().language, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Shanghai' }).format(date);
}
export function formatSize(bytes: number) {
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}
export function isActiveJob(job?: Job | null) { return !!job && ['queued', 'pending', 'running'].includes(job.status); }
export function jobLabel(status: string, type = 'scan') {
  return t(({ queued: '排队中', pending: '排队中', running: type === 'preview' ? '生成中' : type === 'rematch' ? '匹配中' : '扫描中', completed: '已完成', succeeded: '已完成', failed: '失败', interrupted: '已中断', cancelled: '已取消' } as Record<string, string>)[status] || status);
}
export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return t(value ? '是' : '否');
  if (typeof value === 'object') return JSON.stringify(value, null, 2);
  return String(value);
}
export function historyEntries(metadata: Record<string, unknown> = {}): [string, unknown][] {
  const fields: [string, string[]][] = [
    ['视频类型', ['video_type', 'Video Type']], ['时长', ['length', 'duration', 'Length']],
    ['轴类型', ['axis_type', 'Axis Type']], ['作者', ['author', 'creator', 'Creator']],
    ['发布档位', ['tier', 'Slot Type']], ['发布类型', ['release_type', 'Release Type']],
    ['发布渠道', ['channel', 'Channel']], ['计划日期', ['planned_date', 'Planned Date', 'Planned Release Date']],
    ['实际日期', ['actual_date', 'release_date', 'Actual Release Date']],
    ['EroScripts', ['es_url', 'ES Post URL', 'ES Link']], ['Patreon', ['patreon_url', 'Patreon Post URL', 'Patreon post ink']],
    ['视频来源', ['video_url', 'Video Link']], ['作者主页', ['creator_url', 'Support Creator URL']],
    ['历史备注', ['historical_notes', 'Notes']],
  ];
  return fields.flatMap(([label, keys]) => {
    const key = keys.find(key => metadata[key] != null && String(metadata[key]).trim() !== '');
    return key ? [[t(label), typeof metadata[key] === 'string' ? (metadata[key] as string).trim() : metadata[key]] as [string, unknown]] : [];
  });
}
export function safeLink(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : null; } catch { return null; }
}

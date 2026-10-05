import { workIdentity } from './api';
import { useEffect, useRef, useState } from 'react';
import { Check, CircleAlert, Download, Eye, FileText, FolderOpen, LoaderCircle, RefreshCw, Save, Scissors, X, ZoomIn, ZoomOut } from 'lucide-react';
import { useI18n, translate } from './i18n';
import { ApiError, errorMessage, formatSize, isActiveJob, jobLabel, request } from './api';
import type { Capabilities, Job, PreviewAxis, PreviewFile, PreviewMatching, PreviewState, Work } from './api';

const axes: [PreviewAxis, string][] = [['stroke', 'Stroke · 主轴'], ['surge', 'Surge · 前后'], ['sway', 'Sway · 左右'], ['twist', 'Twist · 扭转'], ['roll', 'Roll · 翻滚'], ['pitch', 'Pitch · 俯仰']];
const sameMapping = (left: Partial<Record<PreviewAxis, number>>, right: Partial<Record<PreviewAxis, number>>) => axes.every(([axis]) => left[axis] === right[axis]);
const errorPrefixes = ['无法读取预览状态', '无法读取文件匹配', '对应关系未保存', '重新匹配未提交', '预览任务未提交', '无法打开预览文件夹'];
function previewErrorMessage(message: string) {
  const separator = message.indexOf('：');
  const prefix = message.slice(0, separator);
  return separator >= 0 && errorPrefixes.includes(prefix)
    ? translate(`${prefix}：{error}`, { error: translate(message.slice(separator + 1)) })
    : translate(message);
}

const mediaName = (file: PreviewFile, ordinal: number) => file.kind === 'heatmap' ? translate('完整时长热力图') : translate('片段 {count} · {format}', { count: ordinal, format: file.kind === 'gif' ? 'GIF' : 'WebM' });
const inlineUrl = (url: string) => {
  const [resource, fragment] = url.split('#', 2);
  const separator = resource.indexOf('?');
  const query = new URLSearchParams(separator < 0 ? '' : resource.slice(separator + 1));
  query.set('inline', '1');
  return `${separator < 0 ? resource : resource.slice(0, separator)}?${query}${fragment ? `#${fragment}` : ''}`;
};

function InlinePreview({ file, ordinal, onClose }: { file: PreviewFile; ordinal: number; onClose: () => void }) {
  const { t } = useI18n();
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [zoomed, setZoomed] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const containerRef = useRef<HTMLElement>(null);
  const name = mediaName(file, ordinal);
  const src = inlineUrl(file.url);
  useEffect(() => {
    containerRef.current?.focus({ preventScroll: true });
    containerRef.current?.scrollIntoView?.({ block: 'nearest' });
    const video = videoRef.current;
    return () => { if (video) { video.pause(); video.removeAttribute('src'); video.load(); } };
  }, []);
  const loaded = () => setLoading(false);
  const error = () => { setLoading(false); setFailed(true); };
  return <section ref={containerRef} className="inline-preview" tabIndex={-1} aria-label={t('正在查看 {name}', { name })}>
    <div className="inline-preview-heading"><div><strong>{name}</strong><span>{file.width} × {file.height} · {formatSize(file.size)}</span></div><button type="button" className="button small" onClick={onClose} aria-label={t('关闭内容预览')}><X size={16} aria-hidden="true" />{t('关闭')}</button></div>
    {loading && <p className="inline-preview-feedback" role="status"><LoaderCircle size={16} className="spin" aria-hidden="true" />{t('正在加载{name}…', { name })}</p>}
    {failed && <p className="inline-error" role="alert">{t('内容加载失败，可关闭后重新查看，或下载文件。')}</p>}
    <div className={`inline-preview-media ${file.kind}${zoomed ? ' zoomed' : ''}`}>
      {file.kind === 'video' ? <video ref={videoRef} src={src} controls playsInline preload="metadata" width={file.width} height={file.height} onLoadedMetadata={loaded} onError={error} aria-label={name} /> : <img src={src} alt={name} width={file.width} height={file.height} onLoad={loaded} onError={error} />}
    </div>
    <div className="inline-preview-tools">{file.kind === 'heatmap' && <button type="button" className="button small" aria-pressed={zoomed} onClick={() => setZoomed(value => !value)}>{zoomed ? <ZoomOut size={16} aria-hidden="true" /> : <ZoomIn size={16} aria-hidden="true" />}{zoomed ? t('适应宽度') : t('原尺寸查看')}</button>}<a href={file.url} download={file.filename} className="button small"><Download size={16} aria-hidden="true" />{t('下载{name}', { name })}</a>{file.kind === 'video' && <span className="help-text">{t('点击播放器播放，可全屏查看。')}</span>}{file.kind === 'heatmap' && zoomed && <span className="help-text">{t('横向滚动查看细节。')}</span>}</div>
  </section>;
}

export function PreviewSection({ work, capabilities, onSourcesChanged, onDirtyChange }: { work: Work; capabilities: Capabilities; onSourcesChanged?: () => Promise<void> | void; onDirtyChange?: (dirty: boolean) => void }) {
  const { t } = useI18n();
  const [data, setData] = useState<PreviewState | null>(null);
  const [matching, setMatching] = useState<PreviewMatching | null>(null);
  const [draft, setDraft] = useState<Partial<Record<PreviewAxis, number>>>({});
  const [loadError, setLoadError] = useState('');
  const [matchingLoadError, setMatchingLoadError] = useState('');
  const [matchingError, setMatchingError] = useState('');
  const [matchingMessage, setMatchingMessage] = useState('');
  const [actionError, setActionError] = useState('');
  const [openingMessage, setOpeningMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [rematching, setRematching] = useState(false);
  const [savingMapping, setSavingMapping] = useState(false);
  const [opening, setOpening] = useState(false);
  const [revision, setRevision] = useState(0);
  const [matchingRevision, setMatchingRevision] = useState(0);
  const [videoId, setVideoId] = useState<number | null>(null);
  const [selectedMedia, setSelectedMedia] = useState<{ workId: number; filename: string } | null>(null);
  const submitLock = useRef(false);
  const dataRef = useRef<PreviewState | null>(null);
  const matchingRef = useRef<PreviewMatching | null>(null);
  const draftRef = useRef<Partial<Record<PreviewAxis, number>>>({});
  const completedRematch = useRef<number | null>(null);
  const callbackRef = useRef(onSourcesChanged);
  callbackRef.current = onSourcesChanged;
  const alive = useRef(true);
  const endpoint = `/api/works/${work.id}/preview`;
  const matchingEndpoint = `/api/works/${work.id}/preview-matching`;
  const videos = matching?.videos || [];
  const selected = videos.find(video => video.id === videoId);
  const active = isActiveJob(data?.job);
  const matchingActive = isActiveJob(matching?.job);
  const busy = submitting || rematching || savingMapping || active || matchingActive;
  const dirty = !!matching && !sameMapping(draft, matching.script_asset_ids);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  const generationBlocked = !selected || !data || !matching || !!loadError || !!matchingLoadError || dirty || !!matching.issues.length || !Object.keys(draft).length || busy;
  const files = data?.files || [];
  const viewedFile = selectedMedia?.workId === work.id ? files.find(file => file.filename === selectedMedia.filename) : undefined;
  const clipFiles = files.filter(file => file.kind !== 'heatmap');
  const heatmaps = files.filter(file => file.kind === 'heatmap');
  const clipIndices = [...new Set(clipFiles.map(file => file.clip_index))].sort((left, right) => left - right);
  const jobError = data?.job?.status === 'failed' ? data.job.error || data.error || t('任务失败，请检查视频与脚本后重试。') : data?.error;
  const progress = typeof data?.job?.progress === 'number' && Number.isFinite(data.job.progress) ? Math.min(100, Math.max(0, data.job.progress)) : undefined;

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    let disposed = false;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const load = async () => {
      controller = new AbortController();
      try {
        const next = await request<PreviewState>(endpoint, { signal: controller.signal });
        if (disposed) return;
        dataRef.current = next;
        setData(next); setLoadError('');
      } catch (error) { if (!disposed && !controller.signal.aborted) setLoadError(`无法读取预览状态：${errorMessage(error)}`); }
      finally { if (!disposed) timer = setTimeout(() => void load(), isActiveJob(dataRef.current?.job) ? 1600 : 10000); }
    };
    void load();
    return () => { disposed = true; controller?.abort(); if (timer) clearTimeout(timer); };
  }, [endpoint, revision]);
  useEffect(() => {
    let disposed = false;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const load = async () => {
      controller = new AbortController();
      try {
        const next = await request<PreviewMatching>(`${matchingEndpoint}${videoId !== null ? `?video_asset_id=${videoId}` : ''}`, { signal: controller.signal });
        if (disposed) return;
        const hadDraft = matchingRef.current && !sameMapping(draftRef.current, matchingRef.current.script_asset_ids);
        const current = hadDraft && matchingRef.current ? { ...next, revision: matchingRef.current.revision, script_asset_ids: matchingRef.current.script_asset_ids } : next;
        matchingRef.current = current; setMatching(current); setMatchingLoadError('');
        if (!hadDraft) { draftRef.current = next.script_asset_ids; setDraft(next.script_asset_ids); }
        if (videoId === null && next.video_asset_id !== null) setVideoId(next.video_asset_id);
        if (next.job?.status === 'completed' && completedRematch.current !== next.job.id && !(videoId === null && next.video_asset_id !== null)) {
          await callbackRef.current?.();
          if (!disposed) { completedRematch.current = next.job.id; setRevision(value => value + 1); }
        }
      } catch (error) { if (!disposed && !controller.signal.aborted) setMatchingLoadError(`无法读取文件匹配：${errorMessage(error)}`); }
      finally { if (!disposed) timer = setTimeout(() => void load(), isActiveJob(matchingRef.current?.job) ? 1600 : 10000); }
    };
    void load();
    return () => { disposed = true; controller?.abort(); if (timer) clearTimeout(timer); };
  }, [matchingEndpoint, videoId, matchingRevision]);

  const chooseVideo = (value: string) => {
    if (busy || dirty) return;
    const nextId = value ? Number(value) : null;
    setVideoId(nextId); setMatching(null); matchingRef.current = null;
    draftRef.current = {}; setDraft({}); setMatchingError(''); setMatchingMessage('');
  };
  const changeScript = (axis: PreviewAxis, value: string) => {
    const next = { ...draftRef.current };
    if (value) next[axis] = Number(value); else delete next[axis];
    draftRef.current = next; setDraft(next); setMatchingError(''); setMatchingMessage('');
  };
  const saveMapping = async () => {
    if (submitLock.current || busy || !selected || !matching || (!dirty && matching.mode === 'manual')) return;
    submitLock.current = true; setSavingMapping(true); setMatchingError(''); setMatchingMessage('');
    try {
      const next = await request<PreviewMatching>(matchingEndpoint, { method: 'PUT', body: JSON.stringify({ video_asset_id: selected.id, script_asset_ids: draftRef.current, expected_revision: matching.revision }) });
      if (!alive.current) return;
      matchingRef.current = next; setMatching(next); draftRef.current = next.script_asset_ids; setDraft(next.script_asset_ids);
      setMatchingMessage('视频与各轴脚本的对应关系已保存。');
      setMatchingRevision(value => value + 1);
    } catch (error) {
      if (!alive.current) return;
      if (error instanceof ApiError && error.status === 409) {
        // Reload authoritative selections instead of overwriting a newer mapping.
        draftRef.current = matchingRef.current?.script_asset_ids || {}; setDraft(draftRef.current);
        setMatchingError('对应关系已在其他页面更新，正在重新读取，请检查后再调整。');
        setMatchingRevision(value => value + 1);
      } else setMatchingError(`对应关系未保存：${errorMessage(error)}`);
    } finally { submitLock.current = false; if (alive.current) setSavingMapping(false); }
  };
  const rematch = async () => {
    if (submitLock.current || busy || dirty || !matching || matchingLoadError) return;
    submitLock.current = true; setRematching(true); setMatchingError(''); setMatchingMessage('');
    try {
      const job = await request<Job>(`/api/works/${work.id}/rematch`, { method: 'POST' });
      if (!alive.current) return;
      const next = { ...matchingRef.current!, job };
      matchingRef.current = next; setMatching(next); setMatchingRevision(value => value + 1);
    } catch (error) { if (alive.current) setMatchingError(`重新匹配未提交：${errorMessage(error)}`); }
    finally { submitLock.current = false; if (alive.current) setRematching(false); }
  };
  const generate = async (force = false) => {
    if (submitLock.current || generationBlocked || !selected) return;
    submitLock.current = true; setSubmitting(true); setActionError(''); setOpeningMessage('');
    try {
      const job = await request<Job>(endpoint, { method: 'POST', body: JSON.stringify({ video_asset_id: selected.id, ...(force ? { force: true } : {}) }) });
      if (!alive.current) return;
      const next = { ...dataRef.current!, job, error: null };
      dataRef.current = next; setData(next); setRevision(value => value + 1);
    } catch (error) { if (alive.current) setActionError(`预览任务未提交：${errorMessage(error)}`); }
    finally { submitLock.current = false; if (alive.current) setSubmitting(false); }
  };
  const openFolder = async () => {
    if (!capabilities.can_open_folder || opening || !files.length) return;
    setOpening(true); setOpeningMessage(''); setActionError('');
    try { const result = await request<{ message: string }>(`${endpoint}/open-folder`, { method: 'POST' }); if (alive.current) setOpeningMessage(result.message); }
    catch (error) { if (alive.current) setActionError(`无法打开预览文件夹：${errorMessage(error)}`); }
    finally { if (alive.current) setOpening(false); }
  };
  const buttonLabel = submitting ? t('正在提交') : active ? data?.job?.status === 'running' ? t('正在生成预览') : t('预览排队中') : data?.job?.status === 'failed' || actionError ? t('重试生成预览') : t('一键生成预览');

  return <section className="detail-section preview-section" aria-labelledby={`preview-title-${work.id}`}>
    {(data?.stale ?? work.preview_stale) && <p className="preview-source-changed" role="status"><CircleAlert size={16} />{t('素材目录已重新关联，旧手动对应关系已失效。请先核对源视频和全部轴脚本，再重新生成预览。旧预览仍可查看。')}</p>}
    <div className="section-heading"><Scissors size={17} aria-hidden="true" /><h3 id={`preview-title-${work.id}`}>{t('预览生成')}</h3>{data?.job && <span className={`badge ${data.job.status === 'failed' ? 'warning' : active ? 'pending' : 'neutral'}`}>{t(jobLabel(data.job.status, 'preview'))}</span>}</div>
    <p className="help-text">{t('按所选视频与各轴脚本生成 4 个 WebM、4 个 GIF 和 1 张完整时长热力图。已有且输入未变化的结果会复用；缺少热力图时自动补齐。重新生成会强制重做，成功后替换旧结果。')}</p>
    <div className="preview-matching" aria-labelledby={`matching-title-${work.id}`}>
      <div className="preview-matching-heading"><h4 id={`matching-title-${work.id}`}><FileText size={16} aria-hidden="true" />{t('视频与脚本匹配')}</h4>{matching && <span className="badge neutral">{matching.mode === 'manual' ? t('手动指定') : t('自动匹配')}</span>}</div>
      <p className="help-text">{t('重新匹配只刷新 {name} 的当前目录、素材、轴类型和时间；保留有效的手动选择，以及其他标签、链接和发布资料。', { name: workIdentity(work) })}</p>
      {videos.length > 1 || videos.length === 1 && !selected ? <div className="preview-source"><label htmlFor={`preview-source-${work.id}`}>{t('选择源视频')}</label><select id={`preview-source-${work.id}`} value={selected?.id || ''} onChange={event => chooseVideo(event.target.value)} disabled={busy || dirty}><option value="">{t('请选择要生成预览的视频')}</option>{videos.map(video => <option key={video.id} value={video.id}>{video.relative_path} · {formatSize(video.size)}</option>)}</select></div> : selected ? <p className="preview-source-name"><span>{t('源视频')}</span><strong>{selected.name}</strong></p> : <p className="help-text">{matching ? t('没有可读取的源视频，请重新匹配文件或检查素材目录。') : t('正在读取视频与脚本的对应关系…')}</p>}
      {matching && selected && <><div className="preview-axis-grid">{axes.map(([axis, label]) => <div className="preview-source" key={axis}><label htmlFor={`preview-axis-${work.id}-${axis}`}>{t(label)}</label><select id={`preview-axis-${work.id}-${axis}`} value={draft[axis] || ''} disabled={busy} onChange={event => changeScript(axis, event.target.value)}><option value="">{t('不使用此轴')}</option>{matching.scripts.filter(script => script.id === draft[axis] || !Object.values(draft).includes(script.id)).map(script => <option key={script.id} value={script.id}>{script.relative_path}</option>)}</select></div>)}</div><div className="preview-controls"><button className="button small" onClick={() => void saveMapping()} disabled={(!dirty && matching.mode === 'manual') || busy || !!matchingLoadError}>{savingMapping ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}{savingMapping ? t('正在保存对应关系') : t('保存对应关系')}</button>{dirty && <button className="button small" disabled={busy} onClick={() => { draftRef.current = matching.script_asset_ids; setDraft(matching.script_asset_ids); setMatchingError(''); }}>{t('放弃调整')}</button>}<span className="help-text">{dirty ? t('有未保存的对应关系，请先保存或放弃调整。') : t('每个视频单独保存对应关系。')}</span></div></>}
      {matching && dirty && !selected && <div className="preview-controls"><button className="button small" disabled={busy} onClick={() => { draftRef.current = matching.script_asset_ids; setDraft(matching.script_asset_ids); setMatchingError(''); }}>{t('放弃调整')}</button><span className="help-text">{t('源视频已不可用，放弃未保存的调整后可重新匹配文件。')}</span></div>}
      <div className="preview-controls"><button className="button small" onClick={() => void rematch()} disabled={busy || dirty || !matching || !!matchingLoadError}>{rematching || matchingActive ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}{rematching ? t('正在提交匹配') : matchingActive ? t('正在重新匹配') : t('重新匹配文件')}</button></div>
      {matching?.job && <p className={`preview-match-status ${matching.job.status === 'failed' ? 'inline-error' : ''}`} role="status">{matching.job.status === 'failed' ? t('重新匹配失败：{error}', { error: t(matching.job.error || '请检查扫描目录后重试。') }) : t(matching.job.message || '') || (matchingActive ? t('正在刷新当前编号的素材…') : t('文件重新匹配已完成。'))}</p>}
      {matching?.issues.length ? <ul className="preview-matching-issues" aria-label={t('文件匹配问题')}>{matching.issues.map((issue, index) => <li key={`${issue}-${index}`}><CircleAlert size={14} aria-hidden="true" /><span>{t(issue)}</span></li>)}</ul> : null}
      {matchingLoadError && <div className="preview-error" role="alert"><p><CircleAlert size={15} />{previewErrorMessage(matchingLoadError)}</p><button className="button small" onClick={() => setMatchingRevision(value => value + 1)}>{t('重试读取文件匹配')}</button></div>}
      {matchingError && <p className="inline-error preview-error-text" role="alert">{previewErrorMessage(matchingError)}</p>}
      {matchingMessage && <p className="preview-open-message" role="status"><Check size={15} />{t(matchingMessage)}</p>}
    </div>
    {matching?.source_changed && files.length > 0 && <p className="preview-source-changed" role="status"><CircleAlert size={15} />{t('源视频或脚本已变化，现有预览需要重新生成。')}</p>}
    <div className="preview-controls"><button className="button" onClick={() => void generate()} disabled={generationBlocked}>{submitting || active || !data && !loadError ? <LoaderCircle className="spin" size={16} /> : <Scissors size={16} />}{!data && !loadError ? t('正在读取预览状态') : buttonLabel}</button><button className="button" onClick={() => void generate(true)} disabled={generationBlocked}><RefreshCw size={16} />{t('重新生成预览')}</button>{(active || matchingActive) && <span className="help-text">{t('关闭详情不会中断任务')}</span>}</div>
    {data?.job && <div className={`preview-progress ${data.job.status === 'failed' ? 'failed' : ''}`} role="status" aria-live="polite" aria-atomic="true"><div><span>{t(data.job.message || '') || (active ? data.job.status === 'running' ? t('正在后台处理视频与脚本') : t('任务已加入后台队列') : data.job.status === 'failed' ? t('预览生成失败') : t('预览任务已完成'))}</span>{progress !== undefined && <strong>{progress}%</strong>}</div>{active && <progress max={100} value={progress} aria-label={t('预览生成进度')} />}</div>}
    {loadError && <div className="preview-error" role="alert"><p><CircleAlert size={15} />{previewErrorMessage(loadError)}</p><button className="button small" onClick={() => setRevision(value => value + 1)}><RefreshCw size={14} />{t('重试读取状态')}</button></div>}
    {(actionError || jobError) && <p className="inline-error preview-error-text" role="alert">{previewErrorMessage(actionError) || t('预览生成失败：{error}', { error: t(jobError || '') })}{files.length > 0 && t(' 之前生成的结果仍可使用。')}</p>}
    {files.length > 0 && <div className="preview-results">
      <div className="preview-results-heading"><strong>{active || data?.job?.status === 'failed' ? t('已有预览') : t('预览结果')}</strong><span>{t('{count} 个文件', { count: files.length })}</span></div>
      {viewedFile && <InlinePreview key={`${work.id}:${viewedFile.filename}:${viewedFile.url}:${viewedFile.size}:${viewedFile.width}:${viewedFile.height}`} file={viewedFile} ordinal={clipIndices.indexOf(viewedFile.clip_index) + 1} onClose={() => setSelectedMedia(null)} />}
      {clipIndices.map((index, ordinal) => <div className="preview-clip" key={index}><span>{t('片段 {count}', { count: ordinal + 1 })}</span><div>{clipFiles.filter(file => file.clip_index === index).map(file => <div className="preview-file-actions" key={file.filename}><button type="button" className="button small preview-view-button" onClick={() => setSelectedMedia({ workId: work.id, filename: file.filename })} aria-label={t('查看片段 {count} {format}', { count: ordinal + 1, format: file.kind === 'gif' ? 'GIF' : 'WebM' })} aria-pressed={viewedFile?.filename === file.filename}><Eye size={15} aria-hidden="true" />{t('查看 {format}', { format: file.kind === 'gif' ? 'GIF' : 'WebM' })}</button><a href={file.url} download={file.filename} className="preview-file" title={`${file.filename} · ${file.width}×${file.height} · ${formatSize(file.size)}`}><Download size={14} aria-hidden="true" /><span>{file.kind === 'gif' ? 'GIF' : 'WebM'}</span><small>{formatSize(file.size)}</small><span className="sr-only">{t('片段 {count}', { count: ordinal + 1 })} · {file.filename}</span></a></div>)}</div></div>)}
      {heatmaps.length > 0 && <div className="preview-clip"><span>{t('热力图')}</span><div>{heatmaps.map(file => <div className="preview-file-actions" key={file.filename}><button type="button" className="button small preview-view-button" onClick={() => setSelectedMedia({ workId: work.id, filename: file.filename })} aria-label={t('查看完整时长热力图')} aria-pressed={viewedFile?.filename === file.filename}><Eye size={15} aria-hidden="true" />{t('查看热力图')}</button><a href={file.url} download={file.filename} className="preview-file" title={`${file.filename} · ${file.width}×${file.height} · ${formatSize(file.size)}`}><Download size={14} aria-hidden="true" /><span>PNG</span><small>{formatSize(file.size)}</small><span className="sr-only">{t('完整时长热力图')} · {file.filename}</span></a></div>)}</div></div>}
      <p className="help-text">{t('点击“查看”在网页预览，视频需手动播放；下载按钮单独保存文件。')}</p>{!heatmaps.length && !active && <p className="help-text">{t('现有结果尚无热力图，点击“一键生成预览”即可补齐。')}</p>}
    </div>}
    {files.length > 0 && <><button className="button small preview-open-folder" onClick={() => void openFolder()} disabled={!capabilities.can_open_folder || opening}>{opening ? <LoaderCircle className="spin" size={15} /> : <FolderOpen size={15} />}{opening ? t('正在发送打开请求') : t('打开预览文件夹')}</button>{data?.windows_path && <code className="preview-output-path">{data.windows_path}</code>}<p className="host-note">{capabilities.can_open_folder ? t('在素材所在 Windows 主机上打开预览目录。') : t('不支持打开预览文件夹，仅素材所在主机可用。')}</p></>}
    {openingMessage && <p className="preview-open-message" role="status"><Check size={15} />{t(openingMessage)}</p>}
  </section>;
}

import { useEffect, useRef, useState } from 'react';
import { Check, CircleAlert, Download, FolderOpen, LoaderCircle, RefreshCw, Scissors } from 'lucide-react';
import { errorMessage, formatSize, isActiveJob, jobLabel, request } from './api';
import type { Capabilities, Job, PreviewState, Work } from './api';

export function PreviewSection({ work, capabilities }: { work: Work; capabilities: Capabilities }) {
  const [data, setData] = useState<PreviewState | null>(null);
  const [loadError, setLoadError] = useState('');
  const [actionError, setActionError] = useState('');
  const [openingMessage, setOpeningMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [opening, setOpening] = useState(false);
  const [revision, setRevision] = useState(0);
  const [videoId, setVideoId] = useState<number | null>(null);
  const submitLock = useRef(false);
  const dataRef = useRef<PreviewState | null>(null);
  const alive = useRef(true);
  const videos = (work.assets || []).filter(asset => asset.kind === 'video' && work.directories.some(directory => directory.id === asset.directory_id && directory.available));
  const endpoint = `/api/works/${work.id}/preview`;
  const selected = videos.find(video => video.id === videoId);
  const active = isActiveJob(data?.job);
  const busy = submitting || active;
  const files = data?.files || [];
  const clipIndices = [...new Set(files.map(file => file.clip_index))].sort((left, right) => left - right);
  const jobError = data?.job?.status === 'failed' ? data.job.error || data.error || '任务失败，请检查视频与同名脚本后重试。' : data?.error;
  const progress = typeof data?.job?.progress === 'number' && Number.isFinite(data.job.progress) ? Math.min(100, Math.max(0, data.job.progress)) : undefined;

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (videoId !== null) return;
    const originalId = Number(data?.job?.result?.video_asset_id);
    if (videos.some(video => video.id === originalId)) setVideoId(originalId);
    else if (videos.length === 1) setVideoId(videos[0].id);
  }, [data?.job?.result?.video_asset_id, videos.length, videoId]);
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

  const generate = async () => {
    if (submitLock.current || busy || !selected || !data || loadError) return;
    submitLock.current = true; setSubmitting(true); setActionError(''); setOpeningMessage('');
    try {
      const job = await request<Job>(endpoint, { method: 'POST', body: JSON.stringify({ video_asset_id: selected.id }) });
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
  const buttonLabel = submitting ? '正在提交' : active ? data?.job?.status === 'running' ? '正在生成预览' : '预览排队中' : data?.job?.status === 'failed' || actionError ? '重试生成预览' : '一键生成预览';

  return <section className="detail-section preview-section" aria-labelledby={`preview-title-${work.id}`}>
    <div className="section-heading"><Scissors size={17} aria-hidden="true" /><h3 id={`preview-title-${work.id}`}>预览生成</h3>{data?.job && <span className={`badge ${data.job.status === 'failed' ? 'warning' : active ? 'pending' : 'neutral'}`}>{jobLabel(data.job.status, 'preview')}</span>}</div>
    <p className="help-text">按视频和同名脚本生成预览片段与 GIF，结果保存在专用预览目录。已有且输入未变化的结果会复用。</p>
    {videos.length > 1 ? <div className="preview-source"><label htmlFor={`preview-source-${work.id}`}>选择源视频</label><select id={`preview-source-${work.id}`} value={selected?.id || ''} onChange={event => setVideoId(event.target.value ? Number(event.target.value) : null)} disabled={busy}><option value="">请选择要生成预览的视频</option>{videos.map(video => <option key={video.id} value={video.id}>{video.relative_path} · {formatSize(video.size)}</option>)}</select></div> : selected ? <p className="preview-source-name"><span>源视频</span><strong>{selected.name}</strong></p> : <p className="help-text">没有可读取的视频，检查素材目录后重新扫描。</p>}
    <div className="preview-controls"><button className="button" onClick={() => void generate()} disabled={!selected || !data || !!loadError || busy}>{busy || !data && !loadError ? <LoaderCircle className="spin" size={16} /> : <Scissors size={16} />}{!data && !loadError ? '正在读取预览状态' : buttonLabel}</button>{active && <span className="help-text">关闭详情不会中断任务</span>}</div>
    {data?.job && <div className={`preview-progress ${data.job.status === 'failed' ? 'failed' : ''}`} role="status" aria-live="polite" aria-atomic="true"><div><span>{data.job.message || (active ? data.job.status === 'running' ? '正在后台处理视频与脚本' : '任务已加入后台队列' : data.job.status === 'failed' ? '预览生成失败' : '预览任务已完成')}</span>{progress !== undefined && <strong>{progress}%</strong>}</div>{active && <progress max={100} value={progress} aria-label="预览生成进度" />}</div>}
    {loadError && <div className="preview-error" role="alert"><p><CircleAlert size={15} />{loadError}</p><button className="button small" onClick={() => setRevision(value => value + 1)}><RefreshCw size={14} />重试读取状态</button></div>}
    {(actionError || jobError) && <p className="inline-error preview-error-text" role="alert">{actionError || `预览生成失败：${jobError}`}{files.length > 0 && ' 之前生成的结果仍可使用。'}</p>}
    {files.length > 0 && <div className="preview-results"><div className="preview-results-heading"><strong>{active || data?.job?.status === 'failed' ? '已有预览' : '预览结果'}</strong><span>{files.length} 个文件</span></div>{clipIndices.map((index, ordinal) => <div className="preview-clip" key={index}><span>片段 {ordinal + 1}</span><div>{files.filter(file => file.clip_index === index).map(file => <a key={file.filename} href={file.url} download={file.filename} className="preview-file" title={`${file.filename} · ${file.width}×${file.height} · ${formatSize(file.size)}`}><Download size={14} aria-hidden="true" /><span>{file.kind === 'gif' ? 'GIF' : 'WebM'}</span><small>{formatSize(file.size)}</small><span className="sr-only">片段 {ordinal + 1} · {file.filename}</span></a>)}</div></div>)}<p className="help-text">点击链接下载，不会自动播放视频或加载 GIF。</p></div>}
    {files.length > 0 && <><button className="button small preview-open-folder" onClick={() => void openFolder()} disabled={!capabilities.can_open_folder || opening}>{opening ? <LoaderCircle className="spin" size={15} /> : <FolderOpen size={15} />}{opening ? '正在发送打开请求' : '打开预览文件夹'}</button>{data?.windows_path && <code className="preview-output-path">{data.windows_path}</code>}<p className="host-note">{capabilities.can_open_folder ? '在素材所在 Windows 主机上打开预览目录。' : '不支持打开预览文件夹，仅素材所在主机可用。'}</p></>}
    {openingMessage && <p className="preview-open-message" role="status"><Check size={15} />{openingMessage}</p>}
  </section>;
}

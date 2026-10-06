import { useDialogBackdropClose } from './useDialogBackdropClose';
import { useEffect, useImperativeHandle, useRef, useState } from 'react';
import type { Ref } from 'react';
import { Camera, Check, LoaderCircle, RotateCcw, X } from 'lucide-react';
import { errorMessage, request, workIdentity } from './api';
import type { Work } from './api';
import { SourceVideo, videoTime } from './SourceVideo';
import { translate as t } from './i18n';

export interface CoverState { work_id: number; revision: number; mode: 'automatic' | 'manual'; cover_url: string | null; video_asset_id: number | null; time_seconds: number | null; crop: Crop | null }
interface Crop { x: number; y: number; width: number; height: number }
interface Frame { frame_id: string; frame_url: string; width: number; height: number; video_asset_id: number; time_seconds: number }
export interface CoverEditorHandle { requestLeave(next: () => void): void }
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));
export function coverCrop(width: number, height: number, zoom: number, centerX: number, centerY: number): Crop {
  const ratio = width / height, target = 16 / 9;
  const cropWidth = Math.min(1, target / ratio) / zoom, cropHeight = Math.min(1, ratio / target) / zoom;
  return { x: clamp(centerX, cropWidth / 2, 1 - cropWidth / 2) - cropWidth / 2,
    y: clamp(centerY, cropHeight / 2, 1 - cropHeight / 2) - cropHeight / 2, width: cropWidth, height: cropHeight };
}
export function CoverEditor({ work, onClose, onSaved, onOpenFolder, ref }: {
  work: Work; onClose: () => void; onSaved: (value: CoverState) => void; onOpenFolder: (assetId: number) => void; ref?: Ref<CoverEditorHandle>;
}) {
  const videos = (work.assets || []).filter(asset => asset.kind === 'video');
  const [assetId, setAssetId] = useState(videos[0]?.id || 0);
  const [videoQuery, setVideoQuery] = useState('');
  const [state, setState] = useState<CoverState | null>(null);
  const [frame, setFrame] = useState<Frame | null>(null);
  const [zoom, setZoom] = useState(1), [centerX, setCenterX] = useState(.5), [centerY, setCenterY] = useState(.5);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [conflict, setConflict] = useState(false);
  const [imageReady, setImageReady] = useState(false), [confirmClose, setConfirmClose] = useState(false);
  const [retry, setRetry] = useState(0);
  const operation = useRef(false), time = useRef(0), pendingLeave = useRef<(() => void) | null>(null);
  const dialog = useRef<HTMLDialogElement>(null), image = useRef<HTMLImageElement>(null), canvas = useRef<HTMLCanvasElement>(null);
  const drag = useRef<{ x: number; y: number; centerX: number; centerY: number } | null>(null);
  const dirty = !!frame;
  const requestLeave = (next: () => void) => { if (operation.current) return; if (dirty) { pendingLeave.current = next; setConfirmClose(true); } else next(); };
  const backdrop = useDialogBackdropClose(() => requestLeave(onClose));
  useImperativeHandle(ref, () => ({ requestLeave }));
  useEffect(() => { const element = dialog.current!; element.showModal(); return () => element.close(); }, []);
  useEffect(() => {
    const controller = new AbortController();
    request<CoverState>(`/api/works/${work.id}/cover`, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) { setState(value); setConflict(false); setError(''); }
    }).catch(reason => { if (!controller.signal.aborted) setError(errorMessage(reason)); });
    return () => controller.abort();
  }, [work.id, retry]);
  useEffect(() => {
    if (!dirty) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', protect); return () => window.removeEventListener('beforeunload', protect);
  }, [dirty]);
  const crop = frame ? coverCrop(frame.width, frame.height, zoom, centerX, centerY) : null;
  useEffect(() => {
    if (!imageReady || !frame || !crop || !image.current) return;
    const context = canvas.current?.getContext('2d');
    context?.drawImage(image.current, crop.x * frame.width, crop.y * frame.height, crop.width * frame.width, crop.height * frame.height, 0, 0, 1280, 720);
  }, [imageReady, frame, crop?.x, crop?.y, crop?.width, crop?.height]);
  const capture = async () => {
    if (operation.current || !assetId || !state || conflict) return;
    operation.current = true; setBusy(true); setError('');
    try {
      const value = await request<Frame>(`/api/works/${work.id}/cover/frames`, { method: 'POST', body: JSON.stringify({ video_asset_id: assetId, time_seconds: time.current }) });
      setImageReady(false); setFrame(value); setZoom(1); setCenterX(.5); setCenterY(.5);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { operation.current = false; setBusy(false); }
  };
  const save = async (restore = false) => {
    if (operation.current || !state || conflict || !restore && (!frame || !crop || !imageReady)) return;
    operation.current = true; setBusy(true); setError('');
    try {
      const value = await request<CoverState>(`/api/works/${work.id}/cover${restore ? '/restore' : ''}`, { method: 'POST', body: JSON.stringify(restore ? { expected_revision: state.revision } : { expected_revision: state.revision, frame_id: frame!.frame_id, crop }) });
      setState(value); setFrame(null); onSaved(value);
    } catch (reason) { setConflict((reason as { status?: number }).status === 409); setError(errorMessage(reason)); }
    finally { operation.current = false; setBusy(false); }
  };
  return <dialog {...backdrop} className="cover-editor" ref={dialog} aria-labelledby="cover-editor-title" onCancel={event => { event.preventDefault(); requestLeave(onClose); }}>
    <header className="cover-editor-header"><div><h2 id="cover-editor-title">{t('更换作品封面')}</h2><p>{workIdentity(work)} · 16:9</p></div><button className="icon-button" onClick={() => requestLeave(onClose)} aria-label={t('关闭封面编辑')} disabled={busy}><X size={19} /></button></header>
    <div className="cover-editor-body">
      {confirmClose && <div className="discard-confirm" role="alert"><strong>{t('封面修改尚未保存')}</strong><p>{t('关闭会放弃当前截图和裁剪。')}</p><div><button autoFocus className="button small" onClick={() => setConfirmClose(false)}>{t('继续编辑')}</button><button className="button small" onClick={() => { setConfirmClose(false); (pendingLeave.current || onClose)(); }}>{t('放弃封面修改')}</button></div></div>}
      {error && <div className="notice error" role="alert"><span>{t(error)}</span>{(!state || conflict) && <button className="text-action" disabled={busy} onClick={() => setRetry(value => value + 1)}>{t('重新读取封面状态')}</button>}</div>}
      <div className="cover-editor-layout"><section><h3>{t('选择画面')}</h3>{videos.length > 6 && <label className="search-field"><span className="sr-only">{t('搜索源视频')}</span><input type="search" placeholder={t('搜索源视频…')} value={videoQuery} onChange={event => setVideoQuery(event.target.value)} /></label>}<div className="cover-video-picker" role="group" aria-label={t('封面源视频')}>{videos.filter(asset => asset.name.toLocaleLowerCase().includes(videoQuery.trim().toLocaleLowerCase())).map(asset => <button key={asset.id} aria-pressed={assetId === asset.id} disabled={busy || conflict} onClick={() => { setAssetId(asset.id); time.current = 0; }}>{asset.name}</button>)}</div>
        {assetId ? <SourceVideo key={assetId} workId={work.id} assetId={assetId} supported onOpenFolder={() => onOpenFolder(assetId)} onTimeSelected={seconds => { time.current = seconds; }} /> : <p className="cover-editor-empty">{t('暂无可用于截帧的源视频')}</p>}
        <button className="button primary" style={{ marginTop: 12 }} disabled={busy || !assetId || !state || conflict} onClick={() => void capture()}>{busy ? <LoaderCircle size={16} className="spin" /> : <Camera size={16} />}{t('截取当前画面')}</button>
      </section><section><h3>{t('裁剪与显示位置')}</h3>{frame && crop ? <>
        <div className="cover-crop-frame" onPointerDown={event => { if (busy) return; event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); drag.current = { x: event.clientX, y: event.clientY, centerX, centerY }; }} onPointerMove={event => { if (!drag.current || busy) return; const rect = event.currentTarget.getBoundingClientRect(); setCenterX(clamp(drag.current.centerX - (event.clientX - drag.current.x) / rect.width * crop.width, crop.width / 2, 1 - crop.width / 2)); setCenterY(clamp(drag.current.centerY - (event.clientY - drag.current.y) / rect.height * crop.height, crop.height / 2, 1 - crop.height / 2)); }} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
          <img ref={image} src={frame.frame_url} alt={t('截取的视频画面')} draggable={false} onLoad={() => setImageReady(true)} onError={() => { setImageReady(false); setError(t('截图无法读取，请重新截取')); }} style={{ width: `${100 / crop.width}%`, height: `${100 / crop.height}%`, left: `${-crop.x / crop.width * 100}%`, top: `${-crop.y / crop.height * 100}%` }} /><div className="cover-crop-guide" aria-hidden="true"><span /><span /><span /></div>
        </div><p className="help-text">{videos.find(asset => asset.id === frame.video_asset_id)?.name} · {t('截图时间')} · {videoTime(frame.time_seconds)} · {t('拖动画面调整显示位置')}</p>
        <div className="cover-crop-controls"><label>{t('缩放')}<input type="range" min={1} max={5} step={.02} value={zoom} disabled={busy} onChange={event => setZoom(Number(event.target.value))} /></label><label>{t('水平位置')}<input type="range" min={crop.width / 2} max={1 - crop.width / 2} step={.001} value={crop.x + crop.width / 2} disabled={busy || crop.width === 1} onChange={event => setCenterX(Number(event.target.value))} /></label><label>{t('垂直位置')}<input type="range" min={crop.height / 2} max={1 - crop.height / 2} step={.001} value={crop.y + crop.height / 2} disabled={busy || crop.height === 1} onChange={event => setCenterY(Number(event.target.value))} /></label></div>
        <canvas ref={canvas} width={1280} height={720} className="cover-final-preview" aria-label={t('最终封面预览')} />
      </> : <div className="cover-editor-empty"><Camera size={28} /><span>{t('截取画面后，可裁剪和调整位置')}</span></div>}</section></div>
    </div><footer className="cover-editor-footer"><span>{frame && frame.video_asset_id !== assetId ? t('切换源视频后，请重新截取画面。') : t('库存与详情共用封面，手动封面不会被扫描覆盖。')}</span><div>{state?.mode === 'manual' && <button className="text-action" disabled={busy || conflict} onClick={() => requestLeave(() => void save(true))}><RotateCcw size={15} />{t('恢复自动封面')}</button>}<button className="button" disabled={busy} onClick={() => requestLeave(onClose)}>{t('取消')}</button><button className="button primary" disabled={!frame || !imageReady || !state || busy || conflict || frame.video_asset_id !== assetId} onClick={() => void save()}>{busy ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />}{t('保存封面')}</button></div></footer>
  </dialog>;
}

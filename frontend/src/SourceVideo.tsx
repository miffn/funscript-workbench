import { useRef, useState } from 'react';
import { translate as t } from './i18n';

export function videoTime(value: number) {
  const safe = Number.isFinite(value) && value >= 0 ? Math.round(value * 1000) / 1000 : 0;
  const hours = Math.floor(safe / 3600), minutes = Math.floor(safe / 60) % 60;
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${(safe % 60).toFixed(3).padStart(6, '0')}`;
}
export function parseVideoTime(text: string): number | null {
  if (!/^\d+(?:\.\d+)?$|^\d+:\d{1,2}(?::\d{1,2})?(?:\.\d+)?$/.test(text.trim())) return null;
  const parts = text.trim().split(':').map(Number);
  if (parts.length > 1 && parts.slice(1).some(value => value >= 60)) return null;
  const value = parts.reduce((total, part) => total * 60 + part, 0);
  return Number.isFinite(value) && value >= 0 ? value : null;
}
export function SourceVideo({ workId, assetId, supported, onOpenFolder, onTimeSelected }: {
  workId: number; assetId: number; supported: boolean; onOpenFolder?: () => void;
  onTimeSelected?: (seconds: number) => void;
}) {
  const player = useRef<HTMLVideoElement>(null);
  const [time, setTime] = useState('00:00:00.000');
  const [error, setError] = useState('');
  const [failed, setFailed] = useState(false);
  const seek = () => {
    const seconds = parseVideoTime(time);
    if (seconds === null) { setError(t('请输入有效时间，例如 00:01:30.500')); return; }
    const duration = player.current?.duration;
    if (duration !== undefined && Number.isFinite(duration) && seconds >= duration) { setError(t('时间必须在视频时长范围内')); return; }
    setError(''); onTimeSelected?.(seconds);
    if (!failed && player.current && player.current.readyState > 0) { player.current.pause(); player.current.currentTime = seconds; }
  };
  if (!supported) return <div className="video-unavailable">{t('原视频播放仅在素材所在本机可用。')}</div>;
  return <div className="source-video">
    <video ref={player} key={`${workId}-${assetId}`} controls controlsList="nodownload" preload="metadata" playsInline src={`/api/works/${workId}/assets/${assetId}/media`}
      onError={() => setFailed(true)} onTimeUpdate={event => onTimeSelected?.(event.currentTarget.currentTime)} onSeeked={event => setTime(videoTime(event.currentTarget.currentTime))} />
    {failed && <div className="video-unavailable">{t('浏览器无法播放此视频，可打开所在目录；封面仍可按时间截取。')}{onOpenFolder && <button className="text-action" onClick={onOpenFolder}>{t('打开所在目录')}</button>}</div>}
    <div className="video-time-controls"><label>{t('时间位置')}<input value={time} placeholder="00:00:00.000" onChange={event => setTime(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); seek(); } }} /></label><button className="button small" type="button" onClick={seek}>{t('定位时间')}</button></div>
    {error && <p className="inline-error" role="alert">{error}</p>}
  </div>;
}

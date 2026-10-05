import { useEffect, useRef, useState } from 'react';
import { Check, CircleAlert, FileText, Film, Folder, Link2, LoaderCircle, Plus, RefreshCw, Search } from 'lucide-react';
import { ApiError, errorMessage, request, workIdentity } from './api';
import type { ScanCandidate, ScanCandidates as CandidateData, Work } from './api';
import { useI18n } from './i18n';

type Resolution = { action: 'create' | 'associate'; workId?: number; workRevision?: number };
export function ScanCandidates({ revision, onChanged, onSelect, onCount }: { revision: number; onChanged: () => void; onSelect: (id: number) => void; onCount: (total: number) => void }) {
  const { t } = useI18n();
  const [data, setData] = useState<CandidateData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [feedbackError, setFeedbackError] = useState(false);
  const [lastWorkId, setLastWorkId] = useState<number | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [search, setSearch] = useState('');
  const [drafts, setDrafts] = useState<Record<number, Resolution>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const operation = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true);
    request<CandidateData>('/api/scan-candidates', { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) { setData(value); setError(''); }
    }).catch(reason => { if (!controller.signal.aborted) setError(errorMessage(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [revision, refresh]);
  useEffect(() => { if (data) onCount(data.total); }, [data, onCount]);
  function edit(id: number, next: Resolution) { if (!operation.current) { setDrafts(previous => ({ ...previous, [id]: next })); setMessage(''); } }
  async function resolve(candidate: ScanCandidate) {
    const choice = drafts[candidate.id];
    if (operation.current || loading || error || !choice || !candidate.available || (choice.action === 'associate' && (!choice.workId || choice.workRevision === undefined))) return;
    operation.current = true; setBusy(candidate.id); setError(''); setMessage(''); setLastWorkId(null); setFeedbackError(false);
    try {
      const result = await request<{ candidate_id: number; action: string; work: Work }>(`/api/scan-candidates/${candidate.id}/resolve`, { method: 'POST', body: JSON.stringify({ action: choice.action, expected_revision: candidate.revision, ...(choice.action === 'associate' ? { work_id: choice.workId, expected_work_revision: choice.workRevision } : {}) }) });
      if (!alive.current) return;
      setMessage(t(choice.action === 'associate' ? '已将文件夹关联到 {name}，既有作品资料保留。' : '已创建作品 {name}。', { name: workIdentity(result.work) }));
      setLastWorkId(result.work.id); setDrafts(previous => { const next = { ...previous }; delete next[candidate.id]; return next; });
      setRefresh(value => value + 1); onChanged();
    } catch (reason) {
      if (!alive.current) return;
      if (reason instanceof ApiError && reason.status === 409) {
        setMessage(`${t(errorMessage(reason))} ${t('候选或作品资料已变化，已重新读取。请检查后重新选择；正在运行的素材任务需等待完成。')}`);
        setFeedbackError(true);
        setDrafts({}); setRefresh(value => value + 1); onChanged();
      } else setError(errorMessage(reason));
    } finally { operation.current = false; if (alive.current) setBusy(null); }
  }
  const choices = (data?.works || []).filter(work => `${workIdentity(work)} ${work.title}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()) || Object.values(drafts).some(choice => choice.workId === work.id));
  return <section className="scan-candidates" aria-labelledby="scan-candidate-title">
    <div className="section-heading"><Folder size={19} /><h2 id="scan-candidate-title">{t('扫描候选')}</h2>{data && <span className="badge pending">{t('{count} 个', { count: data.total })}</span>}<button className="button small" onClick={() => setRefresh(value => value + 1)} disabled={busy !== null || loading}><RefreshCw size={15} />{t('重新读取')}</button></div>
    <p className="help-text">{t('文件夹改名或迁移后，请明确关联已有作品或创建新作品。关联只更新素材目录，标题、标签、链接和发布记录保留；不会自动猜测同名作品。')}</p>
    {loading && <p className="loading-state" role="status"><LoaderCircle size={17} className="spin" />{t('正在读取扫描候选…')}</p>}
    {error && <div className="notice error" role="alert"><span><CircleAlert size={17} />{t(error)}</span><button className="button small" onClick={() => setRefresh(value => value + 1)} disabled={busy !== null}>{t('重试')}</button></div>}
    {message && <div className={`scan-candidate-feedback ${feedbackError ? 'warning' : ''}`} role={feedbackError ? 'alert' : 'status'}>{feedbackError ? <CircleAlert size={16} /> : <Check size={16} />}<span>{message}</span>{lastWorkId !== null && <button className="button small" onClick={() => onSelect(lastWorkId)}>{t('查看作品')}</button>}</div>}
    {!loading && data && !data.items.length && <p className="help-text">{t('暂无需要确认的扫描候选。')}</p>}
    {!!data?.items.length && <>
      <label className="search-field scan-candidate-search"><Search size={17} /><span className="sr-only">{t('搜索可关联的失联作品')}</span><input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder={t('搜索可关联的失联作品')} /></label>
      <div className="scan-candidate-list">{data.items.map(candidate => { const choice = drafts[candidate.id]; const isBusy = busy === candidate.id; const disabled = busy !== null || loading || !!error || !candidate.available; return <article className="scan-candidate-card" key={candidate.id}>
        <div className="scan-candidate-heading"><h3>{candidate.name}</h3><span className={`badge ${candidate.available ? 'neutral' : 'warning'}`}>{t(candidate.available ? '可读取' : '暂不可用')}</span></div>
        <code className="scan-candidate-path">{candidate.windows_path || candidate.path}</code>
        <div className="detail-summary"><span><Film size={15} />{candidate.video_count} {t('个视频', { count: candidate.video_count })}</span><span><FileText size={15} />{candidate.script_count} {t('个脚本', { count: candidate.script_count })}</span></div>
        <fieldset className="candidate-resolution" disabled={disabled}><legend>{t('如何维护 {name}', { name: candidate.name })}</legend><label><input type="radio" name={`resolve-${candidate.id}`} checked={choice?.action === 'associate'} onChange={() => edit(candidate.id, { action: 'associate' })} />{t('关联已有作品')}</label><label><input type="radio" name={`resolve-${candidate.id}`} checked={choice?.action === 'create'} onChange={() => edit(candidate.id, { action: 'create' })} />{t('创建新作品')}</label></fieldset>
        {choice?.action === 'associate' && <label className="candidate-work-select">{t('选择可关联的作品')}<select aria-label={t("选择可关联的作品")} disabled={disabled} value={choice.workId || ''} onChange={event => { const work = data.works.find(work => work.id === Number(event.target.value)); edit(candidate.id, { action: 'associate', workId: work?.id, workRevision: work?.association_revision }); }}><option value="">{t('请选择作品')}</option>{choices.map(work => <option key={work.id} value={work.id}>{workIdentity(work)}</option>)}</select><small>{t('只列出扫描已确认失联且无其他活动目录的作品。未扫描或目录临时不可访问时，请恢复目录并重新扫描。')}</small></label>}
        {choice?.action === 'create' && <p className="help-text">{t('新作品使用文件夹名作为标题。有编号的文件夹保留编号，无编号的文件夹不自动生成编号；确认与已有作品无关后再创建。')}</p>}
        <button className="button primary" disabled={disabled || !choice || (choice.action === 'associate' && !choice.workId)} onClick={() => void resolve(candidate)}>{isBusy ? <LoaderCircle size={16} className="spin" /> : choice?.action === 'associate' ? <Link2 size={16} /> : <Plus size={16} />}{t(isBusy ? '正在确认…' : choice?.action === 'associate' ? '确认关联' : '确认创建')}</button>
      </article>; })}</div>
    </>}
  </section>;
}

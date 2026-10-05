import { useEffect, useRef, useState } from 'react';
import { Check, CircleAlert, Copy, Download, FileText, LoaderCircle, RefreshCw, Save, Settings2, X } from 'lucide-react';
import { ApiError, errorMessage, formatDate, formatSize, request } from './api';
import type { Asset, PreviewFile, Work } from './api';
import { copyText } from './clipboard';
import { useI18n } from './i18n';
import './ReleasePosts.css';

interface PostInputs {
  release_title: string; intro_markdown: string; cover_markdown: string; preview_markdown: string; heatmap_markdown: string;
  attachment_markdown: string; selected_script_ids: number[]; selected_preview_filenames: string[];
  no_creator_link: boolean; video_asset_id: number | null;
}
interface PostOutput { title: string; body: string; status: 'draft' | 'ready'; missing: string[]; warnings: string[]; generated_at: string; template_revision: number; stale?: boolean }
interface PostState {
  work_id: number; revision: number; inputs: PostInputs; output: PostOutput | null; saved_cover_url?: string | null;
  sources: { scripts: (Asset & { download_url?: string })[]; videos: Asset[]; previews: PreviewFile[]; author_support: { name: string | null; status: string; url: string | null }; duration_seconds?: number | null };
}
interface PromoButton { id: string; label?: string; imageUrl?: string; linkSource?: string; linkUrl?: string; group?: string; width?: string; [key: string]: unknown }
interface TemplateConfig { brandingHeaderMarkdown?: string; brandingFooterMarkdown?: string; promoButtons?: PromoButton[]; recentPinnedIds?: string[]; [key: string]: unknown }
interface TemplateState { name: string; body: string; config: TemplateConfig; revision: number }
const TOKENS = ['header', 'intro', 'metadata', 'preview', 'heatmaps', 'attachments', 'navigation', 'motion', 'recent', 'footer'];
const json = (value: unknown) => JSON.stringify(value, null, 2);

function PostField({ label, help, value, onChange, rows = 4, disabled = false, placeholder }: { label: string; help?: string; value: string; onChange: (value: string) => void; rows?: number; disabled?: boolean; placeholder?: string }) {
  return <label className="es-field"><span>{label}</span><textarea value={value} onChange={event => onChange(event.target.value)} rows={rows} disabled={disabled} placeholder={placeholder} />{help && <small>{help}</small>}</label>;
}

export function ReleasePostEditor({ workId, onClose }: { workId: number; onClose: () => void }) {
  const { t } = useI18n();
  const [state, setState] = useState<PostState | null>(null);
  const [work, setWork] = useState<Work | null>(null);
  const [inputs, setInputs] = useState<PostInputs | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [conflict, setConflict] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [retry, setRetry] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const alive = useRef(true);
  const continueButton = useRef<HTMLButtonElement>(null);
  const errorSummary = useRef<HTMLDivElement>(null);
  const dirty = !!state && !!inputs && json(inputs) !== json(state.inputs);
  const output = state?.output;
  const free = /free|免费/i.test(work?.tags?.find(tag => tag.category === 'release_type')?.name || '');
  const creator = state?.sources.author_support;
  const fresh = !!output && !output.stale && !dirty;

  useEffect(() => {
    alive.current = true; dialog.current?.showModal();
    const previous = document.body.style.overflow; document.body.style.overflow = 'hidden';
    return () => { alive.current = false; document.body.style.overflow = previous; };
  }, []);
  useEffect(() => { if (confirmClose) continueButton.current?.focus(); }, [confirmClose]);
  useEffect(() => { if (error) errorSummary.current?.focus(); }, [error]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError(''); setMessage('');
    void (async () => {
      try {
        const [saved, loadedWork] = await Promise.all([
          request<PostState>(`/api/works/${workId}/es-post`, { signal: controller.signal }),
          request<Work>(`/api/works/${workId}`, { signal: controller.signal }),
        ]);
        if (controller.signal.aborted) return;
        setState(saved); setInputs(saved.inputs); setWork(loadedWork); setConflict(false);
        if (!saved.output) {
          try {
            const generated = await request<PostState>(`/api/works/${workId}/es-post/generate`, { method: 'POST', body: json({ expected_revision: saved.revision }), signal: controller.signal });
            if (!controller.signal.aborted) { setState(generated); setInputs(generated.inputs); }
          } catch (reason) {
            if (controller.signal.aborted) return;
            if (reason instanceof ApiError && reason.status === 409) {
              const latest = await request<PostState>(`/api/works/${workId}/es-post`, { signal: controller.signal });
              if (!controller.signal.aborted) { setState(latest); setInputs(latest.inputs); }
            } else throw reason;
          }
        }
      } catch (reason) { if (!controller.signal.aborted) setError(errorMessage(reason)); }
      finally { if (!controller.signal.aborted) setBusy(false); }
    })();
    return () => controller.abort();
  }, [workId, retry]);

  function edit(patch: Partial<PostInputs>) { setInputs(value => value && ({ ...value, ...patch })); setError(''); setMessage(''); }
  function toggleScript(id: number) { if (inputs) edit({ selected_script_ids: inputs.selected_script_ids.includes(id) ? inputs.selected_script_ids.filter(value => value !== id) : [...inputs.selected_script_ids, id] }); }
  function togglePreview(filename: string) { if (inputs) edit({ selected_preview_filenames: inputs.selected_preview_filenames.includes(filename) ? inputs.selected_preview_filenames.filter(value => value !== filename) : [...inputs.selected_preview_filenames, filename] }); }
  function close() { if (busy) return; if (dirty) { setConfirmClose(true); return; } onClose(); }
  async function save(generate: boolean, cover = false) {
    if (!state || !inputs || busy) return;
    setBusy(true); setError(''); setMessage('');
    try {
      let saved = state;
      if (dirty) {
        saved = await request<PostState>(`/api/works/${workId}/es-post`, { method: 'PUT', body: json({ inputs, expected_revision: state.revision }) });
        if (!alive.current) return;
        setState(saved); setInputs(saved.inputs);
      }
      if (generate || cover) {
        saved = await request<PostState>(`/api/works/${workId}/es-post/${cover ? 'cover' : 'generate'}`, { method: 'POST', body: json({ expected_revision: saved.revision }) });
        if (!alive.current) return;
        setState(saved); setInputs(saved.inputs);
      }
      if (alive.current) { setConflict(false); setMessage(cover ? t("预览封面已保存，之后生成贴文时会自动取用") : generate ? t("贴文已生成并保存到数据库") : t("发布资料已保存到数据库")); }
    } catch (reason) { if (alive.current) { setError(errorMessage(reason)); setConflict(reason instanceof ApiError && reason.status === 409); } }
    finally { if (alive.current) setBusy(false); }
  }
  async function copy(kind: 'title' | 'body') {
    if (!output || !fresh) return;
    setError('');
    try { await copyText(output[kind], dialog.current); if (alive.current) setMessage(kind === 'title' ? t("标题已复制") : t("正文已复制")); }
    catch (reason) { if (alive.current) setError(errorMessage(reason)); }
  }
  function exportPost() {
    if (!output || !fresh) return;
    const url = URL.createObjectURL(new Blob([`${output.title}\n\n${output.body}\n`], { type: 'text/plain;charset=utf-8' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${work?.script_id || workId}-ES-${output.status}.txt`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function sourceChoices(files: PreviewFile[], group: string) {
    return <div className="es-source-list">{files.map(file => <div className="es-source" key={file.filename}><label className="es-check"><input type="checkbox" aria-label={t('{group}：{filename}', { group: t(group), filename: file.filename })} disabled={busy} checked={inputs?.selected_preview_filenames.includes(file.filename) || false} onChange={() => togglePreview(file.filename)} /><span>{file.kind === 'heatmap' ? t("热力图") : file.kind === 'gif' ? 'GIF' : 'WebM'} · {file.filename}<small>{formatSize(file.size)}</small></span></label><a className="button small" href={file.url} download><Download size={15} aria-hidden="true" />{t("下载")}</a></div>)}</div>;
  }

  return <dialog className="tag-dialog es-post-dialog" ref={dialog} aria-labelledby="es-post-title" onCancel={event => { event.preventDefault(); close(); }}>
    <header className="detail-header"><div><p>{t("ES 发布准备")}</p><h2 id="es-post-title">{work?.script_id || t("作品")} {t("· 生成贴文")}</h2></div><button className="icon-button" type="button" onClick={close} disabled={busy} aria-label={t("关闭贴文编辑器")}><X size={22} /></button></header>
    <div className="tag-dialog-body es-post-body">
      <p className="es-help">{t("资料、上传 Markdown 和生成稿保存在数据库中。上传附件、粘贴贴文和发布由你在 ES 完成。")}</p>
      {error && <div ref={errorSummary} tabIndex={-1} className="es-feedback error" role="alert"><CircleAlert size={17} aria-hidden="true" /><span>{t(error)}</span>{conflict ? <button className="button small" disabled={busy} onClick={() => { if (!dirty || window.confirm(t("重新加载会替换当前未保存的输入，是否继续？"))) setRetry(value => value + 1); }}>{t("加载最新资料")}</button> : !state && <button className="button small" disabled={busy} onClick={() => setRetry(value => value + 1)}>{t("重试")}</button>}</div>}
      {message && <p className="es-feedback success" role="status"><Check size={17} aria-hidden="true" />{t(message)}</p>}
      {busy && <p className="es-loading" role="status"><LoaderCircle size={17} className="spin" aria-hidden="true" />{t("正在读取或生成贴文")}</p>}
      {inputs && state && <div className="es-editor-grid"><div className="es-inputs">
        <section className="es-block"><h3>{t("发布资料")}</h3><label className="es-field"><span>{t("发布标题")}</span><input value={inputs.release_title} placeholder={work?.title === work?.script_id ? t("填写对外发布的作品标题") : work?.title || t("沿用库存中的标题")} disabled={busy} maxLength={300} onChange={event => edit({ release_title: event.target.value })} /><small>{t("留空时沿用作品标题。贴文标题自动加入档位、编号和轴类型。")}</small></label>
          <PostField label={t("公开发布说明")} help={t("可填写 Markdown；库存中的内部备注不会加入贴文。")} value={inputs.intro_markdown} onChange={value => edit({ intro_markdown: value })} disabled={busy} rows={3} />
          {state.sources.videos.length > 1 && <label className="es-field"><span>{t("当前发布的视频")}</span><select disabled={busy} value={inputs.video_asset_id ?? ''} onChange={event => edit({ video_asset_id: event.target.value ? Number(event.target.value) : null })}><option value="">{t("按当前预览匹配选择")}</option>{state.sources.videos.map(video => <option key={video.id} value={video.id}>{video.name}</option>)}</select></label>}
          <p className="es-help">{t("支持作者：")}{creator?.name || t("尚未绑定作者")} · {creator?.status === 'url' ? t("使用作者标签中的支持链接") : creator?.status === 'none' ? t("已确认无需支持链接") : t("尚未确认支持链接")}</p>
          {creator?.status !== 'url' && creator?.status !== 'none' && <label className="es-check"><input type="checkbox" disabled={busy} checked={inputs.no_creator_link} onChange={event => edit({ no_creator_link: event.target.checked })} /><span>{t("我确认这个作品没有支持作者的链接")}</span></label>}
        </section>
        <section className="es-block"><h3>{t("封面")}</h3><p className="es-help">{t("单独保存这个作品的预览封面，后续贴文的近期作品卡片会自动取用。封面不会自动放入当前正文，修改正文预览也不会替换已保存的封面。")}</p>
          {state.sources.previews.some(file => ['gif', 'image'].includes(file.kind)) ? sourceChoices(state.sources.previews.filter(file => ['gif', 'image'].includes(file.kind)), t("封面素材")) : <p className="es-empty">{t("暂无本地 GIF 封面候选，也可以手动上传其他图片到 ES。")}</p>}
          <PostField label={t("ES 封面 Markdown")} help={t("手动上传一张 GIF 或图片到 ES 后粘贴 Markdown；视频和热力图不能作为封面。")} placeholder={t("粘贴 ES 上传后返回的 GIF 或图片 Markdown")} value={inputs.cover_markdown || ''} onChange={value => edit({ cover_markdown: value })} disabled={busy} rows={3} />
          <div className="es-cover-actions"><button className="button" type="button" disabled={busy || !inputs.cover_markdown?.trim()} onClick={() => void save(false, true)}><Save size={16} aria-hidden="true" />{t("保存预览封面")}</button><span className="es-help">{state.saved_cover_url ? t("已保存封面，可用于近期作品预览") : t("尚未保存封面")}{t('。')}</span></div>
        </section>
        <section className="es-block"><h3>{t("贴文内部预览")}</h3><p className="es-help">{t("选择本地视频或 GIF 并下载，手动上传到 ES 后填写 Markdown。素材勾选只记录上传选择，实际贴文使用下方的 ES 地址。")}</p>
          {state.sources.previews.some(file => file.kind !== 'heatmap') ? sourceChoices(state.sources.previews.filter(file => file.kind !== 'heatmap'), t("正文预览素材")) : <p className="es-empty">{t("尚未生成本地预览，可以先保存草稿。")}</p>}
          <PostField label={t("ES 预览 Markdown")} help={t("原样加入当前贴文正文，支持 ES 的 upload:// GIF、图片或视频；独立封面保持不变。")} placeholder={t("粘贴 ES 上传后返回的预览 Markdown")} value={inputs.preview_markdown} onChange={value => edit({ preview_markdown: value })} disabled={busy} />
        </section>
        <section className="es-block"><h3>{t("热力图")}</h3><p className="es-help">{t("热力图单独放入正文的折叠区。")}</p>
          {state.sources.previews.some(file => file.kind === 'heatmap') && sourceChoices(state.sources.previews.filter(file => file.kind === 'heatmap'), t("热力图素材"))}
          <PostField label={t("ES 热力图 Markdown（可选）")} help={t("只填写热力图图片，自动放入折叠区。")} value={inputs.heatmap_markdown} onChange={value => edit({ heatmap_markdown: value })} disabled={busy} rows={3} />
        </section>
        {free && <section className="es-block"><h3>{t("免费脚本附件")}</h3><p className="es-help">{t("即使只有一个文件，也需要明确勾选。下载后手动上传到 ES，将附件 Markdown 粘贴在下方。")}</p><div className="es-source-list">{state.sources.scripts.map(script => <div className="es-source" key={script.id}><label className="es-check"><input type="checkbox" disabled={busy} checked={inputs.selected_script_ids.includes(script.id)} onChange={() => toggleScript(script.id)} /><span>{script.name}<small>{script.axis || 'stroke'} · {formatSize(script.size)}</small></span></label>{script.download_url && <a className="button small" href={script.download_url} download><Download size={15} aria-hidden="true" />{t("下载")}</a>}</div>)}</div>{!state.sources.scripts.length && <p className="es-empty">{t("当前没有匹配的脚本文件。")}</p>}<PostField label={t("ES 脚本附件 Markdown")} value={inputs.attachment_markdown} onChange={value => edit({ attachment_markdown: value })} disabled={busy} rows={3} /></section>}
      </div><section className="es-output es-block" aria-labelledby="es-output-title"><div className="es-section-heading"><h3 id="es-output-title">{t("生成结果")}</h3>{output && <span className={`badge ${output.status === 'ready' && fresh ? 'published' : 'pending'}`}>{!fresh ? t("等待更新") : output.status === 'ready' ? t("资料齐全") : t("草稿")}</span>}</div>
        <p className="es-help">{t("近期作品预览在每次生成时自动更新：取已发布且有封面的最近四部，保留模板指定的固定作品。")}</p>
        {output ? <>{(output.missing.length > 0 || output.warnings.length > 0 || !fresh) && <div className="es-missing">{!fresh && <p>{t("资料或模板已变化，请重新生成后再复制。")}</p>}{output.missing.length > 0 && <><strong>{t("发布前需要补齐")}</strong><ul>{output.missing.map(item => <li key={item}>{t(item)}</li>)}</ul></>}{output.warnings.map(item => <p key={item}>{t(item)}</p>)}</div>}
          <div className="es-output-tools"><button type="button" className="button" disabled={busy || !fresh} onClick={() => void copy('title')}><Copy size={16} aria-hidden="true" />{t("复制标题")}</button><button type="button" className="button" disabled={busy || !fresh} onClick={() => void copy('body')}><Copy size={16} aria-hidden="true" />{t("复制正文")}</button><button type="button" className="button" disabled={busy || !fresh} onClick={exportPost}><Download size={16} aria-hidden="true" />{t("导出文本")}</button></div>
          <label className="es-field"><span>{t("贴文标题")}</span><textarea value={output.title} readOnly rows={2} /></label><label className="es-field"><span>{t("贴文正文")}</span><textarea value={output.body} readOnly rows={22} className="es-markdown" /></label><p className="es-help">{t("生成于")} {formatDate(output.generated_at)} {t("· 模板版本")} {output.template_revision}</p>
        </> : <p className="es-empty">{t("生成后可以分别复制标题和正文。")}</p>}
      </section></div>}
    </div>
    <footer className="detail-actions es-post-actions">{confirmClose ? <div className="es-close-confirm" role="alert"><p>{t("发布资料尚未保存，是否放弃这些修改？")}</p><div><button className="button" ref={continueButton} onClick={() => setConfirmClose(false)}>{t("继续编辑")}</button><button className="button" onClick={onClose}>{t("放弃修改并关闭")}</button></div></div> : <><button className="button" type="button" onClick={close} disabled={busy}>{t("关闭")}</button><div><button className="button" type="button" onClick={() => void save(false)} disabled={busy || !dirty}><Save size={16} aria-hidden="true" />{t("保存资料")}</button><button className="button primary" type="button" onClick={() => void save(true)} disabled={busy || !state}><RefreshCw size={16} aria-hidden="true" />{output ? t("更新贴文") : t("生成贴文")}</button></div></>}</footer>
  </dialog>;
}

export function EsTemplateSettings() {
  const { t } = useI18n();
  const [saved, setSaved] = useState<TemplateState | null>(null);
  const [draft, setDraft] = useState<TemplateState | null>(null);
  const [advanced, setAdvanced] = useState('');
  const [pinnedText, setPinnedText] = useState('');
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [conflict, setConflict] = useState(false);
  const [retry, setRetry] = useState(0);
  const alive = useRef(true);
  const errorSummary = useRef<HTMLDivElement>(null);
  const dirty = !!draft && !!saved && (draft.name !== saved.name || draft.body !== saved.body || advanced !== json(saved.config));
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { if (error) errorSummary.current?.focus(); }, [error]);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setError('');
    request<TemplateState>('/api/es-template', { signal: controller.signal }).then(result => { if (!controller.signal.aborted) { setSaved(result); setDraft(result); setAdvanced(json(result.config)); setPinnedText((result.config.recentPinnedIds || []).join(', ')); setConflict(false); } }).catch(reason => { if (!controller.signal.aborted) setError(errorMessage(reason)); }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [retry]);
  function edit(patch: Partial<TemplateState>) { setDraft(value => value && ({ ...value, ...patch })); setMessage(''); setError(''); }
  function configEdit(patch: Partial<TemplateConfig>) {
    try {
      const current = JSON.parse(advanced) as TemplateConfig;
      const next = { ...current, ...patch }; setAdvanced(json(next)); edit({ config: next });
    } catch { setError(t("请先修正高级设置中的 JSON，再修改主题字段")); }
  }
  function buttonEdit(index: number, patch: Partial<PromoButton>) {
    try {
      const current = JSON.parse(advanced) as TemplateConfig;
      configEdit({ promoButtons: (current.promoButtons || []).map((button, position) => position === index ? { ...button, ...patch } : button) });
    } catch { setError(t("请先修正高级设置中的 JSON")); }
  }
  async function save(event: React.FormEvent) {
    event.preventDefault(); if (!saved || !draft || busy) return;
    let config: TemplateConfig;
    try { config = JSON.parse(advanced); if (!config || Array.isArray(config) || typeof config !== 'object') throw new Error(); }
    catch { setError(t("高级设置必须是有效的 JSON 对象，当前输入已保留")); return; }
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await request<TemplateState>('/api/es-template', { method: 'PUT', body: json({ name: draft.name.trim(), body: draft.body, config, expected_revision: saved.revision }) });
      if (alive.current) { setSaved(result); setDraft(result); setAdvanced(json(result.config)); setPinnedText((result.config.recentPinnedIds || []).join(', ')); setConflict(false); setMessage(t("ES 模板已保存到数据库；下次生成贴文时使用新模板")); }
    } catch (reason) { if (alive.current) { setError(errorMessage(reason)); setConflict(reason instanceof ApiError && reason.status === 409); } }
    finally { if (alive.current) setBusy(false); }
  }
  let config = draft?.config || {};
  try { const parsed = JSON.parse(advanced); if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) config = parsed; } catch { /* Preserve the last valid structured fields while JSON is edited. */ }

  return <section className="settings-card es-template-settings" aria-labelledby="es-template-title"><h2 id="es-template-title"><Settings2 size={20} aria-hidden="true" />{t("ES 贴文模板")}</h2><p className="es-help">{t("修改正文结构、主题图片和导航链接，模板保存在数据库中。每次生成都会按发帖规则更新近期作品预览。")}</p>
    {error && <div className="es-feedback error" role="alert" tabIndex={-1} ref={errorSummary}><CircleAlert size={17} aria-hidden="true" /><span>{error}</span>{(!saved || conflict) && <button className="button small" disabled={busy} onClick={() => { if (!dirty || window.confirm(t("重新加载会替换未保存的模板，是否继续？"))) setRetry(value => value + 1); }}>{conflict ? t("加载最新模板") : t("重试")}</button>}</div>}
    {message && <p className="es-feedback success" role="status"><Check size={17} aria-hidden="true" />{t(message)}</p>}
    {busy && !draft && <p className="es-loading" role="status"><LoaderCircle className="spin" size={17} aria-hidden="true" />{t("正在读取 ES 模板")}</p>}
    {draft && <form onSubmit={save}><label className="es-field"><span>{t("模板名称")}</span><input required disabled={busy} value={draft.name} maxLength={80} onChange={event => edit({ name: event.target.value })} /></label><PostField label={t("正文模板")} value={draft.body} onChange={value => edit({ body: value })} rows={12} disabled={busy} help={t("标题单独生成。模块由作品资料自动填入，内部备注不会进入正文。")} /><div className="es-token-list" aria-label={t("可用模板变量")}>{TOKENS.map(token => <code key={token}>{`{{${token}}}`}</code>)}</div>
      <details className="es-theme-details"><summary><FileText size={17} aria-hidden="true" />{t("主题与导航设置")}</summary><div className="es-theme-form"><PostField label={t("顶部占位 Markdown")} value={String(config.brandingHeaderMarkdown || '')} onChange={value => configEdit({ brandingHeaderMarkdown: value })} disabled={busy} rows={2} /><PostField label={t("品牌页脚 Markdown")} value={String(config.brandingFooterMarkdown || '')} onChange={value => configEdit({ brandingFooterMarkdown: value })} disabled={busy} rows={3} /><label className="es-field"><span>{t("近期作品固定编号")}</span><input value={pinnedText} disabled={busy} onChange={event => { setPinnedText(event.target.value); configEdit({ recentPinnedIds: event.target.value.split(/[,，\s]+/).filter(Boolean) }); }} /><small>{t("可指定需要保留的完整编号；仅在作品已发布且有已保存封面时生效。留空则全部按 ES 发布日期自动更新，最多四部。")}</small></label>
        {(Array.isArray(config.promoButtons) ? config.promoButtons : []).map((button, index) => <fieldset className="es-promo-editor" key={button.id || index} disabled={busy}><legend>{button.label || button.id || t('导航 {number}', { number: index + 1 })}</legend><label className="es-field"><span>{t("按钮文案")}</span><input value={String(button.label || '')} onChange={event => buttonEdit(index, { label: event.target.value })} /></label><label className="es-field"><span>{t("按钮图片 upload:// 地址")}</span><input value={String(button.imageUrl || '')} onChange={event => buttonEdit(index, { imageUrl: event.target.value })} /><small>{t("留空会使用文字链接。图片由你手动上传 ES。")}</small></label><label className="es-field"><span>{t("目标链接来源")}</span><select value={button.linkSource || 'custom'} onChange={event => buttonEdit(index, { linkSource: event.target.value === 'custom' ? undefined : event.target.value })}><option value="custom">{t("固定链接")}</option><option value="videoLink">{t("当前作品的视频链接")}</option><option value="patreonLink">{t("当前作品的 Patreon 文章链接")}</option><option value="supportCreatorLink">{t("当前作者的支持链接")}</option></select></label>{!button.linkSource && <label className="es-field"><span>{t("固定链接")}</span><input value={String(button.linkUrl || '')} onChange={event => buttonEdit(index, { linkUrl: event.target.value })} /></label>}</fieldset>)}
      </div></details>
      <details className="es-theme-details"><summary>{t("高级设置 JSON")}</summary><PostField label={t("主题配置 JSON")} value={advanced} onChange={value => { setAdvanced(value); setMessage(''); setError(''); }} disabled={busy} rows={16} help={t("包含导航分组、图片宽度及其他主题选项。保存时校验，不会丢失原有字段。")} /></details><div className="es-template-actions"><button className="button primary" disabled={busy || !dirty || !draft.name.trim()} type="submit">{busy ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <Save size={16} aria-hidden="true" />}{busy ? t("正在保存") : t("保存 ES 模板")}</button>{saved && <span className="es-help">{t("模板版本")} {saved.revision}{dirty ? t(" · 有未保存修改") : t(" · 已保存")}</span>}</div></form>}
  </section>;
}

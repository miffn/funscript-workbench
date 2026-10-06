import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { ApiError, errorMessage, request } from './api';
import type { Tag, TagCategory, TagCategoryStyle } from './api';
import { translate as t } from './i18n';

const colorTokens: Record<TagCategory, string> = { author: 'author', video_type: 'video', axis_type: 'axis', release_type: 'release', tier: 'tier', custom: 'custom', duration: 'custom' };
const defaultColors: Record<TagCategory, [string, string]> = { author: ['#2F5947', '#BEDBCA'], video_type: ['#505057', '#C4C4CC'], axis_type: ['#505057', '#C4C4CC'], release_type: ['#505057', '#C4C4CC'], tier: ['#75562C', '#DEC69B'], custom: ['#505057', '#C4C4CC'], duration: ['#505057', '#C4C4CC'] };
const validHex = (value: string) => /^#[0-9A-F]{6}$/i.test(value);
export const validTagColors = (...values: string[]) => values.every(value => !value || validHex(value));

export function tagColorStyle(tag: Pick<Tag, 'category' | 'color_light' | 'color_dark' | 'bold' | 'category_style'>): CSSProperties | undefined {
  const light = tag.color_light ?? tag.category_style?.color_light;
  const dark = tag.color_dark ?? tag.category_style?.color_dark;
  const bold = tag.bold ?? tag.category_style?.bold;
  if (!light && !dark && bold == null) return undefined;
  const fallback = `var(--tag-${colorTokens[tag.category]}-fg)`;
  return { ...(light || dark ? { '--tag-fg': `light-dark(${light || fallback}, ${dark || fallback})` } : {}), ...(bold != null ? { '--tag-weight': bold ? 650 : 400 } : {}) } as CSSProperties;
}

export function TagAppearanceFields({ category, name, colorLight, colorDark, bold, setColorLight, setColorDark, setBold, inherited, disabled, categoryMode = false }: {
  category: TagCategory; name: string; colorLight: string; colorDark: string; bold: boolean | null;
  setColorLight: (value: string) => void; setColorDark: (value: string) => void; setBold: (value: boolean | null) => void;
  inherited?: TagCategoryStyle; disabled: boolean; categoryMode?: boolean;
}) {
  const prefix = categoryMode ? 'category' : 'tag';
  const fallback = [inherited?.color_light || defaultColors[category][0], inherited?.color_dark || defaultColors[category][1]];
  const effectiveBold = bold ?? inherited?.bold;
  return <fieldset className="tag-color-settings" disabled={disabled}><legend>{t('标签文字颜色')}</legend><p className="help-text">{t(categoryMode ? '本类型的标签统一使用此样式；单个标签的自定义设置优先。' : '留空跟随类型设置；单个标签的自定义设置优先。')}</p>
    <div className="tag-color-grid">{([['light', colorLight, setColorLight], ['dark', colorDark, setColorDark]] as const).map(([theme, value, setValue], index) => <div className="tag-color-control" key={theme}><label htmlFor={`${prefix}-color-${theme}`}>{t(theme === 'light' ? '浅色标签颜色' : '深色标签颜色')}</label><div className="tag-color-inputs"><input type="color" aria-label={t(theme === 'light' ? '选择浅色颜色' : '选择深色颜色')} value={validHex(value) ? value : fallback[index]} onChange={event => setValue(event.target.value.toUpperCase())} /><input id={`${prefix}-color-${theme}`} type="text" maxLength={7} placeholder={t(categoryMode ? '系统默认' : '跟随类型')} value={value} spellCheck={false} aria-invalid={!!value && !validHex(value)} onChange={event => setValue(event.target.value.toUpperCase())} /></div><div className={`tag-color-preview ${theme}`}><small>{t(theme === 'light' ? '浅色预览' : '深色预览')}</small><span style={{ color: validHex(value) ? value : fallback[index], fontWeight: effectiveBold == null ? 550 : effectiveBold ? 650 : 400 }}>{name}</span></div></div>)}</div>
    {!validTagColors(colorLight, colorDark) && <p className="inline-error" role="alert">{t('颜色格式为 #RRGGBB，例如 #2F5947。')}</p>}
    <div className="tag-inline-choices" role="group" aria-label={t('文字加粗')}><span>{t('文字加粗')}</span>{([[null, categoryMode ? '系统默认' : '跟随类型'], [false, '不加粗'], [true, '加粗']] as const).map(([value, label]) => <button type="button" key={label} aria-pressed={bold === value} onClick={() => setBold(value)}>{t(label)}</button>)}</div>
    <button type="button" className="tag-clear-action" disabled={!colorLight && !colorDark && bold == null} onClick={() => { setColorLight(''); setColorDark(''); setBold(null); }}>{t('恢复默认颜色与字重')}</button>
  </fieldset>;
}

export function TagCategoryStyleEditor({ initial, name, onClose, onSaved, onEditingChange }: { initial: TagCategoryStyle; name: string; onClose: () => void; onSaved: () => void; onEditingChange: (value: boolean) => void }) {
  const [current, setCurrent] = useState(initial);
  const [colorLight, setColorLight] = useState(initial.color_light || '');
  const [colorDark, setColorDark] = useState(initial.color_dark || '');
  const [bold, setBold] = useState(initial.bold ?? null);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const lock = useRef(false);
  const alive = useRef(true);
  const dirty = colorLight !== (current.color_light || '') || colorDark !== (current.color_dark || '') || bold !== (current.bold ?? null);
  useEffect(() => { onEditingChange(dirty || saving); }, [dirty, saving, onEditingChange]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; onEditingChange(false); }; }, [onEditingChange]);
  const path = `/api/tag-category-styles/${current.category}`;
  const refresh = async () => {
    if (lock.current) return; lock.current = true; setSaving(true);
    try { const latest = await request<TagCategoryStyle>(path); if (alive.current) { setCurrent(latest); setColorLight(latest.color_light || ''); setColorDark(latest.color_dark || ''); setBold(latest.bold ?? null); setError(''); setConflict(false); } }
    catch (error) { if (alive.current) setError(errorMessage(error)); }
    finally { lock.current = false; if (alive.current) setSaving(false); }
  };
  const save = async () => {
    if (lock.current || conflict || !validTagColors(colorLight, colorDark)) return; lock.current = true; setSaving(true); setError(''); setFeedback('');
    try { const result = await request<TagCategoryStyle>(path, { method: 'PATCH', body: JSON.stringify({ expected_revision: current.revision, color_light: colorLight || null, color_dark: colorDark || null, bold }) }); if (alive.current) { setCurrent(result); setFeedback(t('类型样式已保存')); onSaved(); } }
    catch (error) { if (alive.current) { const stale = error instanceof ApiError && error.status === 409; setConflict(stale); setError(stale ? t('类型样式已被其他客户端修改，请刷新后重新编辑。') : errorMessage(error)); } }
    finally { lock.current = false; if (alive.current) setSaving(false); }
  };
  return <section className="tag-catalog-editor tag-category-style-editor"><header className="tag-surface-head"><h2>{t('编辑类型样式')} · {name}</h2></header><div className="tag-catalog-editor-body">
    {error && <div className="notice error" role="alert">{error}{conflict && <button className="button small" disabled={saving} onClick={() => void refresh()}>{t('刷新并重新编辑')}</button>}</div>}
    <div className="tag-manage-form"><TagAppearanceFields category={current.category} name={`${name} · ${t('示例标签')}`} colorLight={colorLight} colorDark={colorDark} bold={bold} setColorLight={value => { setColorLight(value); setFeedback(''); }} setColorDark={value => { setColorDark(value); setFeedback(''); }} setBold={value => { setBold(value); setFeedback(''); }} disabled={saving || conflict} categoryMode /></div>
    <div className="tag-catalog-actions"><span className="help-text" role="status">{feedback || t('保存后同步到本类型的全部标签，新建标签也会继承。')}</span><div><button className="button" disabled={saving} onClick={onClose}>{t('取消')}</button><button className="button primary" disabled={saving || conflict || !dirty || !validTagColors(colorLight, colorDark)} onClick={() => void save()}>{t(saving ? '正在保存' : '保存类型样式')}</button></div></div>
  </div></section>;
}

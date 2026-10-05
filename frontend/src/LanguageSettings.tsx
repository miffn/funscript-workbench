import { useEffect, useState } from 'react';
import { Languages, LoaderCircle } from 'lucide-react';
import { errorMessage, request } from './api';
import { getLanguage, setLanguage, useI18n } from './i18n';
import type { Language, LanguageState } from './i18n';

export function LanguageBootstrap() {
  const { locale } = useI18n();
  useEffect(() => {
    let alive = true;
    let controller: AbortController | null = null;
    let inFlight = false;
    const load = async () => {
      if (inFlight) return;
      inFlight = true; controller = new AbortController();
      try {
        const saved = await request<LanguageState>('/api/settings/language', { signal: controller.signal });
        if (alive && saved.revision >= getLanguage().revision) setLanguage(saved);
      } catch { /* Keep the current UI usable while the server is unreachable. */ }
      finally { inFlight = false; }
    };
    void load(); const timer = setInterval(() => void load(), 10000);
    return () => { alive = false; clearInterval(timer); controller?.abort(); };
  }, []);
  useEffect(() => {
    document.documentElement.lang = locale;
    document.title = locale === 'en' ? 'Funscript Workbench' : 'Funscript 工作台';
  }, [locale]);
  return null;
}

export function LanguageSettings() {
  const { locale, t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const change = async (language: Language) => {
    if (busy || language === locale) return;
    setBusy(true); setError(''); setSaved(false);
    try {
      const latest = await request<LanguageState>('/api/settings/language');
      const result = await request<LanguageState>('/api/settings/language', { method: 'PUT', body: JSON.stringify({ language, expected_revision: latest.revision }) });
      setLanguage(result); setSaved(true);
    } catch (reason) {
      setError(errorMessage(reason));
      if (getLanguage().language !== locale) setSaved(false);
    } finally { setBusy(false); }
  };
  return <section className="settings-card language-settings" aria-labelledby="language-settings-title">
    <div className="section-heading"><Languages size={20} /><h2 id="language-settings-title">{t('界面语言')}</h2></div>
    <p className="help-text">{t('语言设置保存在数据库中，所有设备共用；作品标题、作者、标签和文件名保持原样。')}</p>
    <label htmlFor="interface-language">{t('显示语言')}</label>
    <select id="interface-language" value={locale} disabled={busy} onChange={event => void change(event.target.value as Language)}>
      <option value="zh-CN">简体中文</option><option value="en">English</option>
    </select>
    {busy && <p className="help-text" role="status"><LoaderCircle size={16} className="spin" />{t('正在保存语言设置…')}</p>}
    {saved && <p className="help-text" role="status">{t('界面语言已保存')}</p>}
    {error && <p className="inline-error" role="alert">{t(error)}</p>}
  </section>;
}

import { useEffect, useState } from 'react';
import { ChevronDown, Languages, LoaderCircle } from 'lucide-react';
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
  return <div className="sidebar-language">
    <label className="sidebar-language-control" title={t('界面语言')}><Languages size={17} aria-hidden="true" /><span className="sr-only">{t('界面语言')}</span><select value={locale} disabled={busy} onChange={event => void change(event.target.value as Language)}><option value="zh-CN">简体中文</option><option value="en">English</option></select><ChevronDown className="language-chevron" size={13} aria-hidden="true" /></label>
    {busy && <span className="language-feedback help-text" role="status"><LoaderCircle size={13} className="spin" />{t('正在保存语言设置…')}</span>}
    {saved && <span className="language-feedback help-text" role="status">{t('界面语言已保存')}</span>}
    {error && <span className="language-feedback inline-error" role="alert">{t(error)}</span>}
  </div>;
}

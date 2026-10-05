import { useSyncExternalStore } from 'react';
import { forms } from './i18n/forms';
import { workflows } from './i18n/workflows';
import { posts } from './i18n/posts';
import { server } from './i18n/server';
import { core } from './i18n/core';

export type Language = 'zh-CN' | 'en';
export interface LanguageState { language: Language; revision: number }
let state: LanguageState = { language: 'zh-CN', revision: 0 };
const listeners = new Set<() => void>();
const catalog: Record<string, string> = { ...server, ...posts, ...workflows, ...forms, ...core };
const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
function patternFor(key: string, value: string) {
  const names: string[] = [];
  let last = 0, source = '';
  for (const match of key.matchAll(/\{(\w+)\}/g)) {
    source += escape(key.slice(last, match.index)) + '(.*?)';
    names.push(match[1]); last = match.index! + match[0].length;
  }
  return { regex: new RegExp('^' + source + escape(key.slice(last)) + '$', 's'), names, value, sourceKey: key };
}
const patterns = Object.entries(catalog).filter(([key]) => /\{\w+\}/.test(key)).map(([key, value]) => patternFor(key, value));
const reverseCatalog = Object.fromEntries(Object.entries(catalog).map(([key, value]) => [value, key]));
const reversePatterns = Object.entries(catalog).filter(([key, value]) => /\{\w+\}/.test(key) && /[A-Za-z]{2}/.test(value.replace(/\{\w+\}/g, ''))).map(([key, value]) => patternFor(value, key));

export function getLanguage() { return state; }
export function setLanguage(next: LanguageState) {
  if (!['zh-CN', 'en'].includes(next.language) || !Number.isInteger(next.revision)) return;
  if (state.language === next.language && state.revision === next.revision) return;
  state = next; listeners.forEach(listener => listener());
}
function subscribe(listener: () => void) { listeners.add(listener); return () => listeners.delete(listener); }
function interpolate(text: string, values: Record<string, string | number>) {
  return text.replace(/\{(\w+)\}/g, (all, name: string) => name in values ? String(values[name]) : all);
}
function renderTranslation(text: string, values: Record<string, string | number>, locale: Language) {
  if (locale === 'en' && Number(values.count) === 1) {
    text = text.replace(/\{count\} (videos|scripts|works|files|issues|clips|records)\b/g, (_, word: string) => '{count} ' + word.slice(0, -1))
      .replace(/^(videos|scripts|works|files|issues|clips|records)$/, word => word.slice(0, -1));
  }
  return interpolate(text, values);
}
export function translate(text: string, values: Record<string, string | number> = {}, locale = state.language): string {
  if (locale === 'zh-CN' && (catalog[text] !== undefined || patterns.some(pattern => /[\u3400-\u9fff]/.test(pattern.sourceKey) && pattern.regex.test(text)))) {
    return interpolate(text, values);
  }
  const dictionary = locale === 'en' ? catalog : reverseCatalog;
  if (dictionary[text] !== undefined) return renderTranslation(dictionary[text], values, locale);
  for (const pattern of locale === 'en' ? patterns : reversePatterns) {
    const match = text.match(pattern.regex);
    if (match) {
      const errorContext = /失败|无法|错误|未保存|未更新|未完成|不可访问|未加载/.test(locale === 'en' ? text : pattern.value);
      const captured = Object.fromEntries(pattern.names.map((name, index) => {
        const value = match[index + 1];
        // Only known nested errors are translated. User titles, authors and paths
        // in ordinary templates are preserved even if they resemble UI labels.
        return [name, errorContext && dictionary[value] !== undefined ? dictionary[value] : value];
      }));
      return renderTranslation(pattern.value, captured, locale);
    }
  }
  return interpolate(text, values);
}
export function useI18n() {
  const current = useSyncExternalStore(subscribe, getLanguage, getLanguage);
  return { locale: current.language, t: (text: string, values?: Record<string, string | number>) => translate(text, values, current.language) };
}
export function tagName(tag: { category: string; name: string }): string {
  if (tag.category === 'axis_type' && ['单轴', '多轴'].includes(tag.name)) return translate(tag.name);
  const duration = tag.category === 'duration' && tag.name.match(/^(\d+) 分钟$/);
  return duration ? translate('{count} 分钟', { count: duration[1] }) : tag.name;
}

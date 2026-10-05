import { useI18n, translate } from './i18n';
import { CalendarDays } from 'lucide-react';
import type { Work } from './api';
import { workIdentity } from './api';

export function workDisplayTitle(work: Pick<Work, 'title' | 'script_id'>) {
  if (!work.script_id?.trim()) return null;
  const title = work.title.trim();
  return title && title !== workIdentity(work) ? title : null;
}

export function ReleaseDates({ work }: { work: Work }) {
  useI18n();
  return <dl className="release-dates" aria-label={translate('{id} 发布日期', { id: workIdentity(work) })}>
    {(['patreon', 'es'] as const).map(platform => {
      const date = work[`${platform}_published_date`];
      return <div key={platform}><dt><CalendarDays size={13} aria-hidden="true" />{translate('{platform} 发布', { platform: platform === 'patreon' ? 'Patreon' : 'ES' })}</dt><dd>{date ? <time dateTime={date}>{date}</time> : <span className="release-date-empty">{translate("未记录")}</span>}</dd></div>;
    })}
  </dl>;
}

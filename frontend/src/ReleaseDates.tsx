import { CalendarDays } from 'lucide-react';
import type { Work } from './api';

export function workDisplayTitle(work: Pick<Work, 'title' | 'script_id'>) {
  const title = work.title.trim();
  return title && title !== work.script_id ? title : null;
}

export function ReleaseDates({ work }: { work: Work }) {
  return <dl className="release-dates" aria-label={`${work.script_id} 发布日期`}>
    {(['patreon', 'es'] as const).map(platform => {
      const date = work[`${platform}_published_date`];
      return <div key={platform}><dt><CalendarDays size={13} aria-hidden="true" />{platform === 'patreon' ? 'Patreon' : 'ES'} 发布</dt><dd>{date ? <time dateTime={date}>{date}</time> : <span className="release-date-empty">未记录</span>}</dd></div>;
    })}
  </dl>;
}

import { useId } from 'react';
import type { ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';

export function SettingsSection({ title, icon, children, className = '', headingId, headingExtra,
  collapsible = false }: { title: string; icon: ReactNode; children: ReactNode; className?: string;
    headingId?: string; headingExtra?: ReactNode; collapsible?: boolean }) {
  const generatedId = useId();
  const id = headingId || generatedId;
  const heading = <div className="section-heading">{icon}<h2 id={id}>{title}</h2>{headingExtra}</div>;
  if (!collapsible) return <section className={`settings-card ${className}`} aria-labelledby={id}>{heading}{children}</section>;
  return <details className={`settings-card settings-disclosure ${className}`} aria-labelledby={id}>
    <summary className="settings-summary">{heading}<ChevronRight className="settings-chevron" size={18} aria-hidden="true" /></summary>
    <div className="settings-content">{children}</div>
  </details>;
}

// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { Tag, Work } from './api';
import { ProfileSettings } from './ProfileSettings';
import { ReleaseDates } from './ReleaseDates';
import { TagChips } from './Tags';
import { WorkLinkButtons } from './WorkLinks';
import { setLanguage } from './i18n';

afterEach(() => { cleanup(); setLanguage({ language: 'zh-CN', revision: 0 }); });

describe('localized forms', () => {
  it('uses English controls without translating the user profile and switches live', () => {
    setLanguage({ language: 'en', revision: 1 });
    render(<ProfileSettings profile={{ name: '我的名字', bio: '我的简介', avatar: null, revision: 0 }} onSaved={() => {}} />);
    expect(screen.getByRole('heading', { name: 'Workspace profile' })).toBeTruthy();
    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('我的名字');
    expect((screen.getByLabelText('Bio', { exact: false }) as HTMLTextAreaElement).value).toBe('我的简介');
    expect(screen.getByRole('button', { name: 'Choose avatar' })).toBeTruthy();
    act(() => setLanguage({ language: 'zh-CN', revision: 2 }));
    expect(screen.getByRole('button', { name: '选择头像' })).toBeTruthy();
    expect((screen.getByLabelText('姓名') as HTMLInputElement).value).toBe('我的名字');
  });

  it('translates axis and duration labels while preserving author and custom tag names', () => {
    setLanguage({ language: 'en', revision: 1 });
    const tags = [
      { id: 1, category: 'author', name: '单轴' },
      { id: 2, category: 'axis_type', name: '多轴' },
      { id: 3, category: 'duration', name: '12 分钟' },
      { id: 4, category: 'custom', name: '我的分类' },
    ] as Tag[];
    render(<TagChips tags={tags} />);
    expect(screen.getByText('Author')).toBeTruthy();
    expect(screen.getByText('Multi-axis')).toBeTruthy();
    expect(screen.getByText('12 min')).toBeTruthy();
    expect(screen.getByText('单轴')).toBeTruthy();
    expect(screen.getByText('我的分类')).toBeTruthy();
    expect(tags[1].name).toBe('多轴');
    expect(tags[2].name).toBe('12 分钟');
  });

  it('localizes link buttons and publication dates independently for each platform', () => {
    setLanguage({ language: 'en', revision: 1 });
    const work = { script_id: 'S064', links: { patreon: '', video: '', script: '', es: 'https://example.com/topic' }, patreon_published_date: null, es_published_date: '2026-10-05' } as Work;
    render(<><WorkLinkButtons work={work} onEdit={() => {}} /><ReleaseDates work={work} /></>);
    expect(screen.getByRole('group', { name: 'Publication links for S064' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Add Video link S064' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Edit ES post link S064' })).toBeTruthy();
    expect(screen.getByText('ES published')).toBeTruthy();
    expect(screen.getByText('Patreon published')).toBeTruthy();
    expect(screen.getByText('Not recorded')).toBeTruthy();
    expect(screen.getByText('2026-10-05')).toBeTruthy();
  });
});
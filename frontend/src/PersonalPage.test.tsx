// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { PersonalPage } from './PersonalPage';
import type { ProfileData } from './ProfileSettings';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('edits a platform home inline, preserves identity and cancels without writing', async () => {
  const profile: ProfileData = { name: 'Miffn', bio: 'Bio', avatar: null, revision: 4, es_home: '', patreon_home: 'https://www.patreon.com/example' };
  const saved = vi.fn();
  const fetch = vi.fn(async () => new Response(JSON.stringify({ ...profile, es_home: 'https://eroscripts.com/u/example', revision: 5 })));
  vi.stubGlobal('fetch', fetch);
  render(<PersonalPage profile={profile} loading={false} error="" onRetry={() => {}} onSaved={saved} inventory={null} onNavigate={() => {}} onSelect={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: '添加 ES 主页' }));
  fireEvent.change(screen.getByLabelText('ES 主页'), { target: { value: 'https://eroscripts.com/u/cancelled' } });
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '添加 ES 主页' }));
  expect((screen.getByLabelText('ES 主页') as HTMLInputElement).value).toBe('');
  fireEvent.change(screen.getByLabelText('ES 主页'), { target: { value: 'https://eroscripts.com/u/example' } });
  fireEvent.click(screen.getByRole('button', { name: '保存' }));
  await waitFor(() => expect(saved).toHaveBeenCalled());
  const body = JSON.parse((fetch.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);
  expect(body).toEqual({ name: 'Miffn', bio: 'Bio', avatar: null, expected_revision: 4, es_home: 'https://eroscripts.com/u/example' });
});

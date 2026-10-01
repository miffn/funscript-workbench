// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ProfileSettings, type ProfileData } from './ProfileSettings';

const profile: ProfileData = { name: 'Funscript', bio: '脚本工作台', avatar: null, revision: 0 };
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => { fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('database-backed workspace profile', () => {
  it('saves identity using the current revision and reports success', async () => {
    const onSaved = vi.fn();
    fetchMock.mockResolvedValue(response({ ...profile, name: '我的库存', bio: '我的简介', revision: 1 }));
    render(<ProfileSettings profile={profile} onSaved={onSaved} />);
    expect((screen.getByRole('button', { name: '保存工作台资料' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('姓名'), { target: { value: '我的库存' } });
    fireEvent.change(screen.getByLabelText(/简介/), { target: { value: '我的简介' } });
    fireEvent.click(screen.getByRole('button', { name: '保存工作台资料' }));
    await screen.findByRole('status');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ name: '我的库存', bio: '我的简介', avatar: null, expected_revision: 0 });
    expect(onSaved).toHaveBeenCalledWith({ ...profile, name: '我的库存', bio: '我的简介', revision: 1 });
  });

  it('uploads an embedded image and supports removing the existing avatar', async () => {
    const image = new File(['fake image'], 'avatar.png', { type: 'image/png' });
    fetchMock.mockImplementation((_url, init) => Promise.resolve(response({ ...JSON.parse(init.body), revision: 1 })));
    render(<ProfileSettings profile={profile} onSaved={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('上传工作台头像'), { target: { files: [image] } });
    await screen.findByRole('img', { name: 'Funscript头像' });
    expect(screen.getByRole('img').getAttribute('src')).toMatch(/^data:image\/png;base64,/);
    fireEvent.click(screen.getByRole('button', { name: '保存工作台资料' }));
    await screen.findByRole('status');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).avatar).toMatch(/^data:image\/png;base64,/);
    fireEvent.click(screen.getByRole('button', { name: '移除头像' }));
    expect(screen.queryByRole('img')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '保存工作台资料' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).avatar).toBeNull();
  });

  it('rejects unsuitable files before submitting', async () => {
    render(<ProfileSettings profile={profile} onSaved={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('上传工作台头像'), { target: { files: [new File(['<svg/>'], 'avatar.svg', { type: 'image/svg+xml' })] } });
    expect(screen.getByRole('alert').textContent).toContain('PNG');
    fireEvent.change(screen.getByLabelText('上传工作台头像'), { target: { files: [new File(['x'.repeat(300_001)], 'big.jpg', { type: 'image/jpeg' })] } });
    expect(screen.getByRole('alert').textContent).toContain('300 KB');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('retains drafts on conflict until the user chooses to reload', async () => {
    const onSaved = vi.fn();
    fetchMock.mockResolvedValueOnce(response({ detail: '工作台资料已被其他页面修改，请重新加载资料后再保存；当前输入已保留' }, 409)).mockResolvedValueOnce(response({ ...profile, name: '其他页面的姓名', revision: 2 }));
    const { rerender } = render(<ProfileSettings profile={profile} onSaved={onSaved} />);
    fireEvent.change(screen.getByLabelText('姓名'), { target: { value: '我的修改' } });
    rerender(<ProfileSettings profile={{ ...profile, name: '已发生变化', revision: 1 }} onSaved={onSaved} />);
    expect((screen.getByLabelText('姓名') as HTMLInputElement).value).toBe('我的修改');
    fireEvent.click(screen.getByRole('button', { name: '保存工作台资料' }));
    await screen.findByRole('alert');
    expect((screen.getByLabelText('姓名') as HTMLInputElement).value).toBe('我的修改');
    expect(onSaved).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '重新加载资料（替换当前输入）' }));
    await waitFor(() => expect((screen.getByLabelText('姓名') as HTMLInputElement).value).toBe('其他页面的姓名'));
    expect(onSaved).toHaveBeenCalledWith({ ...profile, name: '其他页面的姓名', revision: 2 });
  });
});

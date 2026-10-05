// @vitest-environment jsdom
// @vitest-environment-options {"url":"http://192.168.1.40:8787/#/settings"}
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ApiError, request } from './api';
import { McpSettings } from './McpSettings';
import { setLanguage } from './i18n';

vi.mock('./api', async () => ({ ...await vi.importActual('./api'), request: vi.fn() }));
const call = vi.mocked(request);
const savedToken = 'a'.repeat(43);
const newToken = 'b'.repeat(43);
const status = { enabled: true, can_manage: true, revision: 3, updated_at: null };

beforeEach(() => {
  setLanguage({ language: 'zh-CN', revision: 1 }); call.mockReset(); call.mockResolvedValue(status);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setLanguage({ language: 'zh-CN', revision: 1 }); });
async function ready() { render(<McpSettings />); await screen.findByText('MCP 已启用 Token 认证，所有连接都必须携带 Token。'); }
function enterToken(token = savedToken) { fireEvent.change(screen.getByLabelText('Token'), { target: { value: token } }); }
function clipboard() { const writeText = vi.fn().mockResolvedValue(undefined); vi.stubGlobal('navigator', { clipboard: { writeText } }); return writeText; }

it('uses one client-independent LAN command, requires a token and hides secrets in previews', async () => {
  const writeText = clipboard(); await ready();
  expect(screen.queryByLabelText('Agent 客户端')).toBeNull();
  expect((screen.getByRole('button', { name: '复制启动命令' }) as HTMLButtonElement).disabled).toBe(true);
  enterToken();
  expect((screen.getByLabelText('Token') as HTMLInputElement).type).toBe('password');
  const command = screen.getByLabelText('通用启动命令') as HTMLTextAreaElement;
  expect(command.value).toContain('"http://192.168.1.40:8787/mcp"'); expect(command.value).toContain('<TOKEN>'); expect(command.value).not.toContain(savedToken);
  expect((screen.getByLabelText('通用 MCP 配置') as HTMLTextAreaElement).value).not.toContain(savedToken);
  fireEvent.click(screen.getByRole('button', { name: '复制启动命令' }));
  await screen.findByText('启动命令已复制，请添加到 Agent 的 MCP 设置中。');
  expect(writeText).toHaveBeenCalledWith(`npx -y mcp-remote "http://192.168.1.40:8787/mcp" --allow-http --transport http-only --header "Authorization: Bearer ${savedToken}"`);
  expect(call).toHaveBeenCalledTimes(1); // Copying never registers or installs anything on a server.
});

it('copies stdio JSON with the complete header in an environment variable', async () => {
  const writeText = clipboard(); await ready(); enterToken();
  fireEvent.click(screen.getByRole('button', { name: '复制 MCP 配置' })); await screen.findByText('MCP 配置已复制，其中包含 Token，请妥善保管。');
  const config = JSON.parse(writeText.mock.calls[0][0]).mcpServers['funscript-workbench'];
  expect(config.command).toBe('npx'); expect(config.args).toEqual(['-y', 'mcp-remote', 'http://192.168.1.40:8787/mcp', '--allow-http', '--transport', 'http-only', '--header', 'Authorization:${WORKBENCH_MCP_AUTH}']);
  expect(config.env).toEqual({ WORKBENCH_MCP_AUTH: `Bearer ${savedToken}` });
  fireEvent.click(screen.getByRole('button', { name: '显示 Token' }));
  expect((screen.getByLabelText('通用 MCP 配置') as HTMLTextAreaElement).value).toContain(savedToken);
  fireEvent.click(screen.getByRole('button', { name: '隐藏 Token' }));
  expect((screen.getByLabelText('通用 MCP 配置') as HTMLTextAreaElement).value).not.toContain(savedToken);
});

it('generates the initial token once without browser storage and does not retrieve it after remount', async () => {
  const storage = vi.spyOn(Storage.prototype, 'setItem');
  call.mockResolvedValueOnce({ ...status, enabled: false, revision: 0 }).mockResolvedValueOnce({ ...status, revision: 1, token: newToken }).mockResolvedValue(status);
  const view = render(<McpSettings />); await screen.findByText('尚未生成 Token，MCP 暂不可访问。');
  fireEvent.click(screen.getByRole('button', { name: '生成 Token' })); await screen.findByText('新 Token 已生成，请立即复制并妥善保存；刷新页面后不会再次显示。');
  expect(call).toHaveBeenNthCalledWith(2, '/api/mcp-auth/token', { method: 'POST', body: JSON.stringify({ expected_revision: 0 }) });
  expect((screen.getByLabelText('Token') as HTMLInputElement).value).toBe(newToken); expect(storage).not.toHaveBeenCalled();
  view.unmount(); await ready(); expect((screen.getByLabelText('Token') as HTMLInputElement).value).toBe('');
});

it('requires explicit reset confirmation and keeps the current token if the reset fails', async () => {
  call.mockResolvedValueOnce(status).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ ...status, revision: 4 });
  await ready(); enterToken(); fireEvent.click(screen.getByRole('button', { name: '重置 Token' }));
  expect(call).toHaveBeenCalledTimes(1); expect(screen.getByText('重置后旧 Token 立即失效，已连接的 Agent 需要更新配置。')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '取消' })); expect(call).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: '重置 Token' })); fireEvent.click(screen.getByRole('button', { name: '确认重置 Token' }));
  await screen.findByText('未收到新的 Token。已尝试刷新认证状态；当前输入可能已失效，请重新生成并更新 Agent 配置。');
  expect(call).toHaveBeenCalledTimes(3); expect(call).toHaveBeenNthCalledWith(3, '/api/mcp-auth');
  expect(screen.queryByRole('button', { name: '确认重置 Token' })).toBeNull();
  expect((screen.getByLabelText('Token') as HTMLInputElement).value).toBe(savedToken);
});

it('refreshes a stale revision without retrying reset or losing the current token', async () => {
  call.mockResolvedValueOnce(status).mockRejectedValueOnce(new ApiError(409, 'changed')).mockResolvedValueOnce({ ...status, revision: 5 });
  await ready(); enterToken(); fireEvent.click(screen.getByRole('button', { name: '重置 Token' })); fireEvent.click(screen.getByRole('button', { name: '确认重置 Token' }));
  await screen.findByText('Token 已被其他页面更新，已刷新状态，请确认后重试。');
  expect(call).toHaveBeenCalledTimes(3); expect((screen.getByLabelText('Token') as HTMLInputElement).value).toBe(savedToken);
  expect(screen.queryByRole('button', { name: '确认重置 Token' })).toBeNull();
});

it('allows remote clients to paste a token and copy settings without offering token management', async () => {
  const writeText = clipboard(); call.mockResolvedValue({ ...status, can_manage: false }); await ready();
  expect(screen.queryByRole('button', { name: '重置 Token' })).toBeNull(); enterToken();
  fireEvent.click(screen.getByRole('button', { name: '复制地址' })); await screen.findByText('MCP 地址已复制。');
  expect(writeText).toHaveBeenCalledWith('http://192.168.1.40:8787/mcp');
});

it('rejects incomplete or shell-special tokens and disables authenticated copying', async () => {
  await ready(); enterToken('a'.repeat(42));
  expect(screen.getByText('Token 格式无效，请粘贴完整的工作台 Token。')).toBeTruthy();
  expect((screen.getByRole('button', { name: '复制启动命令' }) as HTMLButtonElement).disabled).toBe(true);
  enterToken('a'.repeat(41) + '$('); expect((screen.getByRole('button', { name: '复制 MCP 配置' }) as HTMLButtonElement).disabled).toBe(true);
});

it.each([true, false])('reports the actual HTTP clipboard fallback outcome (%s)', async success => {
  vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('blocked')) } });
  const fallback = vi.fn(() => success); Object.defineProperty(document, 'execCommand', { configurable: true, value: fallback });
  try {
    await ready(); enterToken(); fireEvent.click(screen.getByRole('button', { name: '复制启动命令' }));
    await screen.findByText(success ? '启动命令已复制，请添加到 Agent 的 MCP 设置中。' : '复制失败，请先显示 Token，再选中内容手动复制。'); expect(fallback).toHaveBeenCalledWith('copy');
    if (!success) {
      expect((screen.getByLabelText('Token') as HTMLInputElement).type).toBe('password');
      expect((screen.getByLabelText('通用启动命令') as HTMLTextAreaElement).value).not.toContain(savedToken);
    }
  } finally { Reflect.deleteProperty(document, 'execCommand'); }
});

it('translates the security and universal connection controls', async () => {
  setLanguage({ language: 'en', revision: 2 }); render(<McpSettings />);
  await screen.findByText('MCP token authentication is enabled. Every connection must provide a token.');
  expect(screen.getByRole('heading', { name: 'Connect an AI Agent' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Copy launch command' })).toBeTruthy(); expect(screen.getByRole('button', { name: 'Reset token' })).toBeTruthy();
  expect((screen.getByLabelText('Universal launch command') as HTMLTextAreaElement).value).toMatch(/^npx -y mcp-remote/);
});
it('copies the token separately without exposing it in command previews', async () => {
  const writeText = clipboard(); await ready(); enterToken();
  fireEvent.click(screen.getByRole('button', { name: '复制 Token' })); await screen.findByText('Token 已复制，请妥善保存。');
  expect(writeText).toHaveBeenCalledWith(savedToken);
  expect((screen.getByLabelText('通用启动命令') as HTMLTextAreaElement).value).not.toContain(savedToken);
});
it.each([
  ['zh-CN', '接入后可读取库存与预览信息，并维护作品标题、备注、标签、链接、发布状态与日期及日历。Token 同时授权读取和资料维护。原始视频与脚本只读，不执行扫描、匹配、预览生成或网站发帖。'],
  ['en', 'Once connected, the agent can read inventory and preview information, and maintain work titles, notes, tags, links, publication statuses and dates, and calendar entries. The token authorizes both reading and data maintenance. Original videos and scripts remain read-only. Scanning, file matching, preview generation and website publishing are unavailable.'],
] as const)('explains token data-maintenance scope and excluded operations in %s', async (language, copy) => {
  setLanguage({ language, revision: 2 }); render(<McpSettings />);
  expect(screen.getByText(copy)).toBeTruthy();
  expect(screen.queryByText('接入后可读取库存、标签、发布链接、日历和预览信息。当前 MCP 为只读。')).toBeNull();
  expect(screen.queryByLabelText('Agent 客户端')).toBeNull();
  await screen.findByRole('button', { name: language === 'en' ? 'Reset token' : '重置 Token' });
});
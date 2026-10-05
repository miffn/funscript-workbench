// @vitest-environment jsdom
// @vitest-environment-options {"url":"http://192.168.1.40:8787/#/settings"}
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { McpSettings } from './McpSettings';
import { setLanguage } from './i18n';

beforeEach(() => setLanguage({ language: 'zh-CN', revision: 1 }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setLanguage({ language: 'zh-CN', revision: 1 }); });

it('uses the current LAN address and copies a Codex command without installing anything', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  const fetch = vi.fn();
  vi.stubGlobal('navigator', { clipboard: { writeText } }); vi.stubGlobal('fetch', fetch);
  render(<McpSettings />);
  const expected = 'codex mcp add funscript-workbench --url "http://192.168.1.40:8787/mcp"';
  expect((screen.getByLabelText('一键接入命令') as HTMLTextAreaElement).value).toBe(expected);
  expect((screen.getByLabelText('MCP 地址') as HTMLInputElement).value).toBe('http://192.168.1.40:8787/mcp');
  fireEvent.click(screen.getByRole('button', { name: '复制接入命令' }));
  await screen.findByText('接入命令已复制，请在 Agent 的终端执行。');
  expect(writeText).toHaveBeenCalledWith(expected); expect(fetch).not.toHaveBeenCalled();
});

it('switches to the user-scoped Claude Code command and clears the previous copy feedback', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal('navigator', { clipboard: { writeText } }); render(<McpSettings />);
  fireEvent.click(screen.getByRole('button', { name: '复制接入命令' }));
  await screen.findByRole('status');
  fireEvent.change(screen.getByLabelText('Agent 客户端'), { target: { value: 'claude' } });
  expect(screen.queryByRole('status')).toBeNull();
  const expected = 'claude mcp add --transport http --scope user funscript-workbench "http://192.168.1.40:8787/mcp"';
  expect((screen.getByLabelText('一键接入命令') as HTMLTextAreaElement).value).toBe(expected);
  fireEvent.click(screen.getByRole('button', { name: '复制接入命令' }));
  await screen.findByRole('status'); expect(writeText).toHaveBeenLastCalledWith(expected);
});

it('copies the endpoint separately for other agent clients', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal('navigator', { clipboard: { writeText } }); render(<McpSettings />);
  fireEvent.click(screen.getByRole('button', { name: '复制地址' }));
  await screen.findByText('MCP 地址已复制。');
  expect(writeText).toHaveBeenCalledWith('http://192.168.1.40:8787/mcp');
});

it.each([true, false])('supports HTTP clipboard fallback and reports its actual outcome (%s)', async success => {
  vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('blocked')) } });
  const fallback = vi.fn(() => success);
  Object.defineProperty(document, 'execCommand', { configurable: true, value: fallback });
  try {
    render(<McpSettings />); fireEvent.click(screen.getByRole('button', { name: '复制接入命令' }));
    await screen.findByText(success ? '接入命令已复制，请在 Agent 的终端执行。' : '复制失败，请选中上方内容手动复制。');
    expect(fallback).toHaveBeenCalledWith('copy');
    expect(screen.getByLabelText('一键接入命令')).toBeTruthy();
    if (!success) expect(screen.queryByRole('status')).toBeNull();
  } finally { Reflect.deleteProperty(document, 'execCommand'); }
});

it('translates the setup panel while preserving executable command text', () => {
  setLanguage({ language: 'en', revision: 2 }); render(<McpSettings />);
  expect(screen.getByRole('heading', { name: 'Connect an AI Agent' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Copy setup command' })).toBeTruthy();
  expect((screen.getByLabelText('One-command setup') as HTMLTextAreaElement).value).toMatch(/^codex mcp add/);
});

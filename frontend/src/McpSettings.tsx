import { useEffect, useState } from 'react';
import { Bot, Check, Copy, Eye, EyeOff, KeyRound, LoaderCircle } from 'lucide-react';
import { ApiError, request } from './api';
import { copyText } from './clipboard';
import { translate as t, useI18n } from './i18n';

type AuthState = { enabled: boolean; can_manage: boolean; revision: number; updated_at: string | null };
type CopyTarget = 'command' | 'config' | 'url' | 'token';

export function McpSettings() {
  useI18n();
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const [token, setToken] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [copying, setCopying] = useState(false);
  const [copied, setCopied] = useState<CopyTarget | null>(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const url = `${window.location.origin}/mcp`;
  const validToken = /^[A-Za-z0-9_-]{43}$/.test(token);
  const blocked = busy || copying;
  const commandFor = (value: string) => `npx -y mcp-remote "${url}" --allow-http --transport http-only --header "Authorization: Bearer ${value}"`;
  const configFor = (value: string) => JSON.stringify({ mcpServers: { 'funscript-workbench': {
    command: 'npx', args: ['-y', 'mcp-remote', url, '--allow-http', '--transport', 'http-only', '--header', 'Authorization:${WORKBENCH_MCP_AUTH}'],
    env: { WORKBENCH_MCP_AUTH: `Bearer ${value}` },
  } } }, null, 2);
  const visibleToken = showToken && validToken ? token : '<TOKEN>';

  async function reload() {
    setLoading(true); setError('');
    try { setAuth(await request<AuthState>('/api/mcp-auth')); }
    catch { setError(t('无法读取 MCP 认证状态，请重试。')); }
    finally { setLoading(false); }
  }
  useEffect(() => { void reload(); }, []);

  async function generate() {
    if (!auth || !auth.can_manage) return;
    setBusy(true); setError(''); setMessage(''); setCopied(null);
    try {
      const result = await request<AuthState & { token: string }>('/api/mcp-auth/token', {
        method: 'POST', body: JSON.stringify({ expected_revision: auth.revision }),
      });
      // Only the public status goes into auth state; the secret stays in this component's memory.
      setAuth({ enabled: result.enabled, can_manage: result.can_manage, revision: result.revision, updated_at: result.updated_at });
      setToken(result.token); setShowToken(false); setConfirmReset(false);
      setMessage(t('新 Token 已生成，请立即复制并妥善保存；刷新页面后不会再次显示。'));
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        await reload(); setConfirmReset(false); setError(t('Token 已被其他页面更新，已刷新状态，请确认后重试。'));
      } else if (reason instanceof ApiError && reason.status === 403) {
        setError(t('仅素材所在 Windows 主机可以生成或重置 Token。'));
      } else {
        // The server may have rotated the token even if its response never reached us.
        await reload(); setConfirmReset(false);
        setError(t('未收到新的 Token。已尝试刷新认证状态；当前输入可能已失效，请重新生成并更新 Agent 配置。'));
      }
    } finally { setBusy(false); }
  }

  async function copy(target: CopyTarget) {
    if (target !== 'url' && !validToken) return;
    setCopying(true); setCopied(null); setError('');
    try { await copyText(target === 'command' ? commandFor(token) : target === 'config' ? configFor(token) : target === 'token' ? token : url); setCopied(target); }
    catch { setError(t(target === 'url' ? '复制失败，请选中上方内容手动复制。' : '复制失败，请先显示 Token，再选中内容手动复制。')); }
    finally { setCopying(false); }
  }

  return <section className="settings-card mcp-settings" aria-labelledby="mcp-settings-title">
    <div className="section-heading"><Bot size={20} aria-hidden="true" /><h2 id="mcp-settings-title">{t('连接 AI Agent')}</h2></div>
    <p className="help-text">{t('使用通用 MCP 配置连接支持 stdio 的 Agent，无需选择客户端。')}</p>
    <div className="mcp-auth-panel">
      <h3><KeyRound size={17} aria-hidden="true" />{t('MCP 访问 Token')}</h3>
      {loading ? <p className="help-text">{t('正在读取 MCP 认证状态…')}</p> : auth ? <p className="help-text">{t(auth.enabled ? 'MCP 已启用 Token 认证，所有连接都必须携带 Token。' : '尚未生成 Token，MCP 暂不可访问。')}</p> : <button type="button" className="button" onClick={() => void reload()}>{t('重试')}</button>}
      {auth?.can_manage && <div className="mcp-token-actions">
        {!confirmReset ? <button type="button" className="button" disabled={blocked || loading} onClick={() => auth.enabled ? setConfirmReset(true) : void generate()}>
          {busy ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <KeyRound size={16} aria-hidden="true" />}{t(auth.enabled ? '重置 Token' : '生成 Token')}
        </button> : <div className="mcp-reset-confirm">
          <p>{t('重置后旧 Token 立即失效，已连接的 Agent 需要更新配置。')}</p>
          <div><button type="button" className="button" disabled={blocked} onClick={() => setConfirmReset(false)}>{t('取消')}</button>
            <button type="button" className="button primary" disabled={blocked} onClick={() => void generate()}>{busy && <LoaderCircle size={16} className="spin" aria-hidden="true" />}{t('确认重置 Token')}</button></div>
        </div>}
      </div>}
      {auth && !auth.can_manage && <p className="help-text">{t('仅素材所在 Windows 主机可以生成或重置 Token；这里可以粘贴已有 Token 生成接入配置。')}</p>}
      <div className="mcp-token-field">
        <label htmlFor="mcp-token">Token</label>
        <div className="mcp-url-row"><input id="mcp-token" type={showToken ? 'text' : 'password'} value={token} autoComplete="off" spellCheck={false} placeholder={t('粘贴已保存的 Token，或生成新的 Token')} disabled={blocked} onChange={event => {
          setToken(event.target.value.trim()); setCopied(null); setMessage(''); setError('');
        }} />
          <button type="button" className="button" disabled={blocked} aria-pressed={showToken} onClick={() => setShowToken(value => !value)}>{showToken ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}{t(showToken ? '隐藏 Token' : '显示 Token')}</button>
        </div>
        <button type="button" className="button mcp-copy-token" disabled={blocked || !validToken} onClick={() => void copy('token')}><Copy size={16} aria-hidden="true" />{t('复制 Token')}</button>
        <p className="help-text">{t('Token 只在生成时返回一次，本页不会将其保存到浏览器。复制的命令和配置会包含 Token。')}</p>
        {token && !validToken && <p className="inline-error">{t('Token 格式无效，请粘贴完整的工作台 Token。')}</p>}
      </div>
    </div>
    {message && <p className="mcp-copy-feedback" role="status">{message}</p>}
    <div className="mcp-command-field">
      <label htmlFor="mcp-start-command">{t('通用启动命令')}</label>
      <textarea id="mcp-start-command" value={commandFor(visibleToken)} readOnly spellCheck={false} rows={3} />
      <button type="button" className="button primary" disabled={blocked || !validToken} onClick={() => void copy('command')}><Copy size={17} aria-hidden="true" />{t('复制启动命令')}</button>
    </div>
    <div className="mcp-command-field">
      <label htmlFor="mcp-config">{t('通用 MCP 配置')}</label>
      <textarea id="mcp-config" value={configFor(visibleToken)} readOnly spellCheck={false} rows={7} />
      <button type="button" className="button" disabled={blocked || !validToken} onClick={() => void copy('config')}><Copy size={17} aria-hidden="true" />{t('复制 MCP 配置')}</button>
    </div>
    <p className="help-text mcp-access-help">{t('需要安装 Node.js。将命令及参数或 JSON 配置添加到 Agent 的 MCP 设置中；仅在终端运行命令不会自动注册到所有 Agent。')}</p>
    <div className="mcp-url-field">
      <label htmlFor="mcp-address">{t('MCP 地址')}</label>
      <div className="mcp-url-row"><input id="mcp-address" value={url} readOnly spellCheck={false} /><button type="button" className="button" disabled={blocked} onClick={() => void copy('url')}>
        {copied === 'url' ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}{t('复制地址')}</button></div>
    </div>
    {copied && <p className="mcp-copy-feedback" role="status">{t(copied === 'command' ? '启动命令已复制，请添加到 Agent 的 MCP 设置中。' : copied === 'config' ? 'MCP 配置已复制，其中包含 Token，请妥善保管。' : copied === 'token' ? 'Token 已复制，请妥善保存。' : 'MCP 地址已复制。')}</p>}
    {error && <p className="inline-error" role="alert">{error}</p>}
    <p className="help-text mcp-access-help">{t('地址跟随当前网页，Agent 需能访问此地址；其他电脑请从局域网地址打开工作台后复制。')}</p>
    <p className="help-text">{t('接入后可读取库存与预览信息，并维护作品标题、备注、标签、链接、发布状态与日期及日历。Token 同时授权读取和资料维护。原始视频与脚本只读，不执行扫描、匹配、预览生成或网站发帖。')}</p>
  </section>;
}
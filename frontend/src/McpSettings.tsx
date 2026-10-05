import { useState } from 'react';
import { Bot, Check, Copy, LoaderCircle } from 'lucide-react';
import { copyText } from './clipboard';
import { translate as t, useI18n } from './i18n';

export function McpSettings() {
  useI18n();
  const [agent, setAgent] = useState('codex');
  const [copying, setCopying] = useState(false);
  const [copied, setCopied] = useState<'command' | 'url' | null>(null);
  const [error, setError] = useState(false);
  const url = `${window.location.origin}/mcp`;
  const command = agent === 'codex'
    ? `codex mcp add funscript-workbench --url "${url}"`
    : `claude mcp add --transport http --scope user funscript-workbench "${url}"`;

  const copy = async (target: 'command' | 'url') => {
    setCopying(true); setCopied(null); setError(false);
    try { await copyText(target === 'command' ? command : url); setCopied(target); }
    catch { setError(true); }
    finally { setCopying(false); }
  };

  return <section className="settings-card mcp-settings" aria-labelledby="mcp-settings-title">
    <div className="section-heading"><Bot size={20} aria-hidden="true" /><h2 id="mcp-settings-title">{t('连接 AI Agent')}</h2></div>
    <p className="help-text">{t('选择 Agent，复制接入命令，在已安装该客户端的电脑终端执行。')}</p>
    <div className="mcp-agent-field">
      <label htmlFor="mcp-agent">{t('Agent 客户端')}</label>
      <select id="mcp-agent" value={agent} disabled={copying} onChange={event => {
        setAgent(event.target.value); setCopied(null); setError(false);
      }}><option value="codex">Codex</option><option value="claude">Claude Code</option></select>
    </div>
    <div className="mcp-command-field">
      <label htmlFor="mcp-install-command">{t('一键接入命令')}</label>
      <textarea id="mcp-install-command" value={command} readOnly spellCheck={false} rows={3} />
      <button type="button" className="button primary" disabled={copying} onClick={() => void copy('command')}>
        {copying ? <LoaderCircle size={17} className="spin" aria-hidden="true" /> : copied === 'command' ? <Check size={17} aria-hidden="true" /> : <Copy size={17} aria-hidden="true" />}
        {t('复制接入命令')}
      </button>
    </div>
    <div className="mcp-url-field">
      <label htmlFor="mcp-address">{t('MCP 地址')}</label>
      <div className="mcp-url-row"><input id="mcp-address" value={url} readOnly spellCheck={false} />
        <button type="button" className="button" disabled={copying} onClick={() => void copy('url')}>
          {copied === 'url' ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}{t('复制地址')}
        </button>
      </div>
    </div>
    {copied && <p className="mcp-copy-feedback" role="status">{t(copied === 'command' ? '接入命令已复制，请在 Agent 的终端执行。' : 'MCP 地址已复制。')}</p>}
    {error && <p className="inline-error" role="alert">{t('复制失败，请选中上方内容手动复制。')}</p>}
    <p className="help-text mcp-access-help">{t('地址跟随当前网页，Agent 需能访问此地址；其他电脑请从局域网地址打开工作台后复制。')}</p>
    <p className="help-text">{t('接入后可读取库存、标签、发布链接、日历和预览信息。当前 MCP 为只读。')}</p>
  </section>;
}

# Funscript Workbench

个人使用的脚本与视频素材工作台。原始视频和脚本只读，库存资料保存到 SQLite，生成的封面和预览写入独立目录。

- 库存搜索、组合标签筛选、ES／Patreon 发布日期排序与无限下滑。
- 自定义标签分类、标签删除与恢复、分类及单个标签的颜色和加粗设置。
- 本机视频播放、截图裁剪封面、视频与脚本匹配、WebM／GIF／热力图生成。
- 发布信息、计划日期、发布日历、后台任务记录及可选 MCP 接入。
- 浅深色主题、可折叠侧栏和语言下拉。

本文按第一次安装编写，使用 **Windows + WSL 2 Ubuntu 24.04**。Bash 命令在 Ubuntu 执行，PowerShell 命令在 Windows 执行。

## 1. 准备 Windows 和 WSL

在管理员 PowerShell 安装 Ubuntu：

```powershell
wsl --install -d Ubuntu-24.04
```

按提示重启，打开 Ubuntu 创建 Linux 用户。安装命令见 [Microsoft WSL 安装说明](https://learn.microsoft.com/windows/wsl/install)。项目目录使用 `~/projects/script-workbench`。Windows UNC 示例中的 `<Linux用户名>` 和 `<Windows IPv4>` 必须替换为自己的实际值。

Windows 另外安装 **Python 3.12**（包含 `py` 启动器），仅用于网关，不需要安装后端依赖。在 Windows PowerShell 确认 Python 可用：

```powershell
py -3.12 -c "import sys; print(sys.executable)"
```

在 Ubuntu 检查 systemd：

```bash
systemctl --user status
```

若未启用，在 `/etc/wsl.conf` 中加入以下配置，然后在 Windows 执行 `wsl --shutdown`，重新打开 Ubuntu：

```ini
[boot]
systemd=true
```

## 2. 安装 Ubuntu 依赖

```bash
sudo apt update
sudo apt install -y git curl openssh-client ca-certificates python3.12 python3.12-venv ffmpeg build-essential cmake libegl1-mesa-dev libgl-dev libglm-dev
```

前端构建需要 **Node.js 22.12 或更高版本**。以下使用 [nvm 官方安装方式](https://github.com/nvm-sh/nvm#install--update-script) 安装 Node.js 22：

```bash
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh | bash
source ~/.bashrc
nvm install 22
node --version
npm --version
```

Node.js 和 npm 安装在 WSL 中；仅安装 Windows Node.js 不能替代这一步。

## 3. 获取代码并构建

在 WSL 从公开 GitHub 仓库获取代码：

```bash
mkdir -p ~/projects
git clone https://github.com/miffn/funscript-workbench.git ~/projects/script-workbench
cd ~/projects/script-workbench

python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt

npm --prefix frontend ci
npm --prefix frontend run build

cmake -S preview_generator -B preview_generator/build -DCMAKE_BUILD_TYPE=Release
cmake --build preview_generator/build -j4
```

构建后应存在 `.venv/bin/uvicorn`、`frontend/dist/index.html` 和 `preview_generator/build/ofs-preview-renderer`。网页由后端提供，无需另外启动 Vite。热力图工具及字体随仓库提供，Pillow 随后端依赖安装。

## 4. 初始化并启动后端

在 Ubuntu 项目目录生成本机网关密钥，并安装用户服务：

```bash
cd ~/projects/script-workbench
.venv/bin/python - <<'PY'
from pathlib import Path
import secrets

data = Path('data')
data.mkdir(exist_ok=True)
key = data / 'host.key'
if not key.exists():
    key.write_text(secrets.token_hex(32), encoding='utf-8')
key.chmod(0o600)
PY

bash scripts/install-service.sh
systemctl --user is-active script-workbench.service
curl --fail http://127.0.0.1:8789/api/health
```

服务应返回 `active`，健康接口应返回 `status: ok`。安装器启用用户 linger；SQLite 数据库 `data/workbench.sqlite3` 在首次启动时自动创建。后端只监听 WSL 本机的 `8789`。

## 5. 启动 Windows 网关

先保留 Ubuntu 终端，在 Windows PowerShell 执行。将局域网地址替换为这台 Windows 电脑的 IPv4，可用 `ipconfig` 查看：

```powershell
$projectPath = '\\wsl.localhost\Ubuntu-24.04\home\<Linux用户名>\projects\script-workbench'
$pythonPath = (& py -3.12 -c 'import sys; print(sys.executable)').Trim()

& $pythonPath "$projectPath\scripts\windows_gateway.py" `
  --lan-host '<Windows IPv4>' `
  --host-key-file "$projectPath\data\host.key"
```

网关在前台运行，首次验证期间保留这个 PowerShell 窗口。此方式不依赖 Codex 或额外的 WSL 执行脚本。

| 入口 | 地址 | 能力 |
| --- | --- | --- |
| Windows 本机 | `http://localhost:8788/` | 管理资料、播放原视频、编辑封面、打开目录 |
| 局域网 | `http://<Windows IPv4>:8787/` | 管理资料和查看生成预览；原视频、封面编辑和打开目录仅限本机 |
| 后端内部 | `http://127.0.0.1:8789/` | 网关使用 |

需要局域网访问时，在管理员 PowerShell 添加防火墙规则：

```powershell
New-NetFirewallRule -Name 'ScriptWorkbench-LAN' -DisplayName 'Script Workbench LAN' `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8787 `
  -Profile Private -RemoteAddress LocalSubnet
```

Windows 网络应设为“专用”，手机或其他电脑连接同一局域网。

## 6. 第一次使用

1. 打开 `http://localhost:8788/`，进入「设置 → 本地资料库」。
2. 添加素材目录，例如 `D:\Media\Workspace`。目录需在 WSL 中可读；新目录默认按文件夹识别，也可选择按编号识别。
3. 保存目录，回到库存点击「立即扫描」。保存目录本身不会触发扫描。
4. 扫描完成后维护标题、标签和发布信息。自定义分类在「标签管理 → 新增分类」创建。
5. 打开作品，在「预览生成与匹配」核对视频与各轴脚本，生成并查看预览。本机还可打开预览所在目录。

默认输出目录为 `data/previews/`，整个输出目录排除出库存扫描。一次任务通常生成 4 个 WebM、4 个 GIF 和 1 张完整时长热力图；短视频可能少于 4 个片段。

本机可在「素材」播放原视频，点击详情封面可选时间截图并裁剪为 16:9。语言在侧栏底部切换，时区在「设置 → 发布偏好」配置。语言、时区、标签和作品资料保存到数据库；主题及侧栏状态保存在当前浏览器。

如果需要指定预览输出位置，在 Ubuntu 执行：

```bash
systemctl --user edit script-workbench.service
```

填入并保存：

```ini
[Service]
Environment="WORKBENCH_PREVIEW_OUTPUT_ROOT=/mnt/d/funscript-previews"
```

然后执行：

```bash
systemctl --user daemon-reload
systemctl --user restart script-workbench.service
```

输出目录必须在 WSL 中可写。保留默认的 `data/` 和 `data/host.key` 位置，便于 Windows 网关与后端使用同一份密钥。

## 7. 配置登录自启动

首次验证通过后，可使用 Windows 安装器让工作台登录后自动运行。

**自启动脚本的前提：** 本机需要已有 `%USERPROFILE%\.codex\bin\Invoke-WslProject.ps1` 及其配套配置，且执行器允许访问当前项目目录。它们不在本仓库中；没有执行器时，先使用第 5 节的前台网关方式。本机执行器可独立运行，不要求打开 Codex 应用。

先在第 5 节的前台网关窗口按 `Ctrl+C` 停止网关，避免重复占用端口。再在 Windows PowerShell 执行：

```powershell
$projectPath = '\\wsl.localhost\Ubuntu-24.04\home\<Linux用户名>\projects\script-workbench'
$pythonPath = (& py -3.12 -c 'import sys; print(sys.executable)').Trim()
$runtimePath = Join-Path $env:ProgramData 'ScriptWorkbench'

New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
Copy-Item -LiteralPath "$projectPath\scripts\Install-Autostart.ps1" `
  -Destination "$runtimePath\Install-Autostart.ps1" -Force
& "$runtimePath\Install-Autostart.ps1" -ProjectPath $projectPath `
  -LanHost '<Windows IPv4>' -PythonPath $pythonPath
```

安装器创建并立即启动计划任务 `ScriptWorkbench`。以后在当前用户登录后延迟 15 秒启动，自动保持 WSL 运行并恢复异常退出。安装完成后可以关闭 Ubuntu、PowerShell、Codex 和浏览器。未登录 Windows 时，登录任务不会运行。

## 8. 验证安装与查看日志

在 Windows PowerShell 检查：

```powershell
Invoke-RestMethod 'http://localhost:8788/api/health'
Invoke-RestMethod 'http://localhost:8788/api/capabilities'
```

应返回 `status: ok`，且本机的 `can_open_folder`、`can_play_video`、`can_edit_cover` 为 `true`。局域网入口的这三项为 `false`。

最后用一部有效视频及其脚本完成扫描、生成、查看预览和打开目录的流程；使用自启动时，重启电脑并登录后确认网页可访问。

| 问题 | 检查位置 |
| --- | --- |
| 后端未启动 | Ubuntu 执行 `journalctl --user -u script-workbench.service -n 100 --no-pager` |
| 网关或自启动失败 | `%ProgramData%\ScriptWorkbench\autostart.log`、`keeper.stderr.log`；自启动网关日志位于项目 `data/gateway.stderr.log` |
| 本机正常、局域网不可达 | Windows IPv4、专用网络、防火墙 8787 规则 |
| 预览生成失败 | WSL 素材读取权限、输出目录写权限、FFmpeg 和已构建的渲染器 |

已配置自启动时，停止服务：

```powershell
$projectPath = '\\wsl.localhost\Ubuntu-24.04\home\<Linux用户名>\projects\script-workbench'
& "$env:ProgramData\ScriptWorkbench\Stop-Workbench.ps1" -ProjectPath $projectPath
```

再次启动：

```powershell
Start-ScheduledTask -TaskName 'ScriptWorkbench'
```

前台运行时，网关用 `Ctrl+C` 停止，后端在 Ubuntu 用 `systemctl --user stop script-workbench.service` 停止。

`data/` 包含数据库、本机密钥、封面缓存和运行记录，已排除出 Git。它是持久资料目录，不是可清理的构建产物。

## 9. 可选：连接 AI

MCP 随后端启动。首次接入时，从本机网页进入「设置 → MCP 接入」，生成 Token，再复制页面给出的命令或 JSON 配置到 AI 客户端。

| 客户端位置 | MCP 地址 |
| --- | --- |
| Windows 本机 | `http://localhost:8788/mcp` |
| WSL | `http://127.0.0.1:8789/mcp` |
| 同一局域网 | `http://<Windows IPv4>:8787/mcp` |

客户端必须能访问该地址；云端客户端无法直接访问这里的 localhost 或局域网。MCP 请求需携带 `Authorization: Bearer <Token>`，通用转接命令需要客户端安装 Node.js。

Token 只在生成时显示，复制后妥善保存，不提交到仓库。AI 可读取库存并维护作品资料、标签、链接和发布日期。扫描和预览生成由工作台网页发起，实际发布由自己在目标网站完成。

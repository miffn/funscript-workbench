# 脚本工作台部署指南

## 部署结构

业务后端全部运行在 WSL Ubuntu 24.04：FastAPI 提供 API 和构建后的网页，SQLite 保存数据，FFmpeg、预览渲染器和 Python/Pillow 热力图程序处理素材。Windows 保存素材，并通过轻量网关提供局域网访问和打开资源管理器窗口；计划任务负责登录后启动及保持 WSL 运行。

| 入口 | 地址 | 用途 |
| --- | --- | --- |
| WSL 后端 | `http://127.0.0.1:8789` | 网关连接的内部服务 |
| Windows 本机 | `http://localhost:8788/` | 网页操作，可打开素材和预览目录 |
| 局域网 | `http://<Windows IPv4>:8787/` | 手机及其他客户端访问，不支持打开主机文件夹 |

素材目录在网页设置中添加、删除和勾选，保存在数据库中。保存配置后点击“立即扫描”才会扫描。原视频和脚本保持只读，生成结果写入独立预览目录。

## 1. 准备环境

- Windows：WSL 2、Ubuntu 24.04、PowerShell 7、Python 3.12。Windows Python 只运行网关，无需安装后端依赖。
- WSL：Python 3.12、Node.js 22.12 或更新版本、npm、Git、FFmpeg、CMake 和 C++ 构建依赖。
- 本文以 `/home/user/projects/script-workbench` 为项目目录，WSL 用户为 `admin`；替换目录时也要调整后面的 Windows UNC 路径。

在 WSL 中检查 `systemctl --user status`。若未启用 systemd，在 `/etc/wsl.conf` 合并以下配置，再在 Windows 执行 `wsl --shutdown` 并重新打开 Ubuntu：

```ini
[boot]
systemd=true
```

在 WSL 中安装依赖：

```bash
sudo apt update
sudo apt install -y git python3.12 python3.12-venv ffmpeg build-essential cmake libegl1-mesa-dev libgl-dev libglm-dev
```

Node.js 需另行安装符合要求的版本，用 `node --version` 和 `npm --version` 确认。

当前 Windows 启动脚本还依赖 `%USERPROFILE%\.codex\bin\Invoke-WslProject.ps1` 及其配置文件。该执行器不在仓库内，换到新电脑需要另行准备并配置项目目录；它作为普通脚本运行，无需启动 Codex 应用。当前执行器支持 Ubuntu-24.04 下 `/home/user/projects` 内的项目。

## 2. 获取代码并构建

在 WSL Linux 文件系统中克隆公开仓库；也可替换成自己的 Forgejo 克隆地址：

```bash
mkdir -p /home/user/projects
git clone https://github.com/miffn/funscript-workbench.git /home/user/projects/script-workbench
cd /home/user/projects/script-workbench
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
cmake -S preview_generator -B preview_generator/build -DCMAKE_BUILD_TYPE=Release
cmake --build preview_generator/build -j4
ctest --test-dir preview_generator/build --output-on-failure
```

构建产物为 `frontend/dist` 和 `preview_generator/build/ofs-preview-renderer`。生产环境无需另外启动 Vite。

预览模型和渲染源码在 `preview_generator/`；热力图源码在 `backend/tools/heatmapgen/heatmapgen.py`，中文字体及 SIL OFL 许可随工具保存。Pillow 已包含在后端依赖中。所有生成工具均在 WSL 原生执行，业务后端无需 Windows EXE、Windows 字体或程序互操作。

一次预览任务生成 4 个 WebM、4 个 GIF 和完整时长的 `热力图.png`，可在网页中查看或下载。默认输出目录是 `data/previews/`，输出目录自动排除出库存扫描。素材目录可填写 Windows 绝对路径（例如 `E:\素材`）或 WSL 绝对路径；对应目录必须在 WSL 中可读。

## 3. 初始化数据并安装 WSL 服务

在项目目录创建主机密钥；已有密钥保持原样：

```bash
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

预期状态为 `active`，健康接口返回 `status: ok`。服务安装器启用用户 linger，后端不依赖终端会话。SQLite 数据库 `data/workbench.sqlite3` 在首次启动时自动创建。

库存资料、扫描目录、标签、链接、发布状态及日期、日历、头像和界面语言均持久化到 SQLite。`data/` 保存数据库、密钥、缓存和运行数据，已排除出 Git。

需要修改运行配置时，执行 `systemctl --user edit script-workbench.service`，例如指定项目之外的预览输出目录：

```ini
[Service]
Environment="WORKBENCH_PREVIEW_OUTPUT_ROOT=/mnt/e/素材预览"
```

保存后执行：

```bash
systemctl --user daemon-reload
systemctl --user restart script-workbench.service
```

| 环境变量 | 默认值及说明 |
| --- | --- |
| `WORKBENCH_DATA_DIR` | `<项目>/data` |
| `WORKBENCH_HOST_KEY_FILE` | `<数据目录>/host.key` |
| `WORKBENCH_ROOTS_JSON` | `[]`；仅首次初始化导入目录，之后以数据库配置为准 |
| `WORKBENCH_PREVIEW_OUTPUT_ROOT` | `<数据目录>/previews`；需要写权限 |
| `WORKBENCH_PREVIEW_RENDERER` | `<项目>/preview_generator/build/ofs-preview-renderer` |
| `WORKBENCH_HEATMAP_TOOL` | `<项目>/backend/tools/heatmapgen/heatmapgen.py`；通常无需覆盖，只支持 Python 源码 |
| `WORKBENCH_OPEN_MODE` | `gateway`；后端始终向 Windows 网关返回打开目录请求 |

自带 Windows 启动器使用 `<项目>/data/host.key`，建议保留默认数据及密钥位置。若改变这些位置，需同时调整 Windows 网关使用的密钥路径，使两端一致。

## 4. 配置 Windows 登录自启动

在 Windows PowerShell 中执行。将项目 UNC 路径、局域网 IPv4 和 Windows Python 路径替换为实际值；显式传参可覆盖脚本中的默认值。

先复制安装器到 Windows 本地，避免 UNC 来源触发脚本签名限制：

```powershell
$projectPath = '\\wsl.localhost\Ubuntu-24.04\home\user\projects\script-workbench'
$runtimePath = Join-Path $env:ProgramData 'ScriptWorkbench'
New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
Copy-Item -LiteralPath "$projectPath\scripts\Install-Autostart.ps1" -Destination "$runtimePath\Install-Autostart.ps1" -Force
& "$runtimePath\Install-Autostart.ps1" -ProjectPath $projectPath `
  -LanHost '192.168.1.100' `
  -PythonPath 'C:\Path\To\Python312\python.exe'
```

安装器创建并立即启动计划任务 `ScriptWorkbench`。以后当前用户登录 Windows 后延迟 15 秒启动，后端或网关退出时自动重试。关闭 Codex、浏览器或终端不影响运行。启动器和配置保存在 `%ProgramData%\ScriptWorkbench`，重复安装会更新它们。此任务在用户登录后启动，未登录 Windows 时不会运行。

在管理员 PowerShell 中允许私有局域网访问 8787，已有规则时跳过：

```powershell
if (-not (Get-NetFirewallRule -Name 'ScriptWorkbench-LAN' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -Name 'ScriptWorkbench-LAN' -DisplayName 'Script Workbench LAN' `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8787 `
    -Profile Private -RemoteAddress LocalSubnet
}
```

手机连接同一局域网后访问 `http://<Windows IPv4>:8787/`。局域网 IP 或 Windows Python 路径变化时，重新运行安装器。

## 5. 验证部署

1. 打开本机和局域网网页，确认两个入口的 `/api/health` 均返回 `status: ok`。
2. 检查 `/api/capabilities`：本机的 `can_open_folder` 为 `true`，局域网为 `false`。
3. 在设置中保存扫描目录，点击“立即扫描”；修改一条库存资料，刷新及重启后确认保留。
4. 选择带有效视频和脚本的作品生成预览，确认网页能够查看 WebM、GIF 和热力图。本机打开目录时应显示对应资源管理器窗口。
5. 重启电脑并登录安装任务的用户，确认网页自动恢复。

## 6. 停止、启动与日志

在 Windows PowerShell 中停止计划任务、网关和后端：

```powershell
$projectPath = '\\wsl.localhost\Ubuntu-24.04\home\user\projects\script-workbench'
& "$env:ProgramData\ScriptWorkbench\Stop-Workbench.ps1" -ProjectPath $projectPath
```

再次启动：

```powershell
Start-ScheduledTask -TaskName 'ScriptWorkbench'
```

停止后下次登录仍会自动启动。要取消自启动，执行 `Disable-ScheduledTask -TaskName 'ScriptWorkbench'`；恢复时先 `Enable-ScheduledTask -TaskName 'ScriptWorkbench'`，再启动任务。

WSL 后端日志：

```bash
journalctl --user -u script-workbench.service -n 100 --no-pager
```

Windows 日志与状态：

- 自启动日志：`%ProgramData%\ScriptWorkbench\autostart.log`。
- WSL 保活错误：`%ProgramData%\ScriptWorkbench\keeper.stderr.log`。
- 网关日志：`<项目>\data\gateway.stdout.log`、`gateway.stderr.log`。
- 计划任务状态：`Get-ScheduledTask -TaskName ScriptWorkbench`。

后端不可达时先检查服务日志和 8789 健康接口；手机不可达时检查 Windows IPv4、网络是否为“专用”以及 8787 防火墙规则。预览失败时检查 WSL 素材读取权限、输出目录写权限，以及渲染器是否已构建。

## 7. 更新及迁移到 WSL 原生热力图

先等待生成任务结束，用上面的 Windows 停止命令停止网关和后端，并按下一节备份数据。在 WSL 项目目录更新：

```bash
git pull --ff-only
.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
cmake -S preview_generator -B preview_generator/build -DCMAKE_BUILD_TYPE=Release
cmake --build preview_generator/build -j4
ctest --test-dir preview_generator/build --output-on-failure
```

从旧版 Windows 热力图工具升级时，检查 systemd 的环境覆盖。如果曾设置 `WORKBENCH_HEATMAP_TOOL` 指向 `.exe`，删除该覆盖以恢复内置 Python 程序。检查 `WORKBENCH_PREVIEW_RENDERER`、`WORKBENCH_FFMPEG` 和 `WORKBENCH_FFPROBE`，确保使用 WSL 原生程序；默认 FFmpeg / FFprobe 从 WSL 的 PATH 查找。

保留 `data/`，启动计划任务恢复服务。数据库升级自动执行；既有资料和预览文件保留，下一次生成任务按新的热力图程序指纹检查缓存。

如果启动脚本更新或项目目录移动，按第 4 节重新安装 Windows 自启动。移动项目时还需在新目录重建 `.venv`、前端和渲染器，运行 `bash scripts/install-service.sh` 更新服务路径，并检查 systemd 中自定义的绝对路径。

## 8. 备份与恢复

先停止网关及后端，在 WSL 项目目录备份整个 `data/`：

```bash
mkdir -p /home/user/backups
backup_file="/home/user/backups/script-workbench-data-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$backup_file" data
```

恢复时先停止服务，将备份的 `data/` 恢复到项目目录，保留数据库和 `host.key`，再启动计划任务。运行中的 SQLite 不应只复制主数据库文件；停机备份整个目录可同时保留 WAL 等配套文件。

项目之外的预览输出目录需另行备份，原素材也需单独备份。备份包含私有运行数据及密钥，应保存在受控位置，不提交到仓库。

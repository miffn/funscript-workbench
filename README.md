# 脚本工作台部署指南

部署方式：Windows 主机保存素材，WSL Ubuntu 24.04 运行 FastAPI、构建后的 React 页面和 SQLite，Windows 网关提供本机及局域网访问。

## 1. 准备环境

- Windows：WSL 2、Ubuntu 24.04、PowerShell 7、Python 3.12；Python 可执行文件路径需要传给网关。
- WSL：启用 systemd，安装 Python 3.12、Node.js 22.12 或更新版本、npm、Git、FFmpeg 和 C++ 构建依赖。本项目当前使用 Node.js 24。
- 端口：WSL 后端 `127.0.0.1:8789`，Windows 本机入口 `127.0.0.1:8788`，局域网入口 `<Windows IPv4>:8787`。
- 素材目录：部署后在网页设置中添加，可填写 Windows 盘符绝对路径或 WSL 绝对路径；新部署不预设个人素材目录。

在 WSL 终端检查 `systemctl --user status`、`node --version` 和 `npm --version`。若 WSL 未启用 systemd，在 `/etc/wsl.conf` 合并以下设置，再在 Windows 执行 `wsl --shutdown` 并重新打开 Ubuntu：

```ini
[boot]
systemd=true
```

在 WSL 终端安装其余依赖：

```bash
sudo apt update
sudo apt install -y git python3.12 python3.12-venv ffmpeg build-essential cmake libegl1-mesa-dev libgl-dev libglm-dev
```

Node.js 需单独准备符合版本要求的安装；不要直接使用版本过旧的系统包。

## 2. 获取代码与构建

从仓库页面复制具有访问权限的克隆地址，替换下面的 `<仓库地址>`。代码应放在 WSL Linux 文件系统中。

```bash
mkdir -p /home/user/projects
git clone <仓库地址> /home/user/projects/script-workbench
cd /home/user/projects/script-workbench
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
cmake -S preview_generator -B preview_generator/build -DCMAKE_BUILD_TYPE=Release
cmake --build preview_generator/build -j4
ctest --test-dir preview_generator/build --output-on-failure
```

`frontend/dist` 是网页产物，`preview_generator/build/ofs-preview-renderer` 是预览渲染器。生产环境由后端同时提供网页和 API，无需另外启动 Vite。

检查所添加的素材目录在 WSL 中可读。生成结果默认写入项目的 `data/previews/`，也可通过环境变量指定独立输出目录；该目录需要写权限。预览输出目录不会被作为库存素材扫描。

热力图工具及内置资源已集成在 `backend/tools/heatmapcreatorv1.0.exe`，默认按项目位置定位；预览渲染器源码和模型位于 `preview_generator/`。无需另行准备个人工具目录。WSL 需启用 Windows 程序互操作；后台自动向工具提供所选各轴脚本的临时副本，并跳过等待回车。原素材不会被改写。一次预览任务输出 4 个 WebM、4 个 GIF 和 `热力图.png`。项目移至另一位置后，在新目录重新构建渲染器并重新安装服务；系统依赖仍按上述步骤安装。

## 3. 初始化持久化目录

在 WSL 项目目录执行以下命令。已有密钥不会被覆盖。

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
```

数据库 `data/workbench.sqlite3` 在后端首次启动时自动创建。`data/` 保存运行数据与密钥，不提交到 Git。

## 4. 安装 WSL 服务

```bash
bash scripts/install-service.sh
systemctl --user is-active script-workbench.service
curl --fail http://127.0.0.1:8789/api/health
```

预期服务状态为 `active`，健康接口返回 `status: ok`。后端只监听 loopback，由 Windows 网关转发。

扫描目录在网页设置中添加、删除或启用，保存至 SQLite；升级会迁移已有目录配置，删除配置不会删除原文件、作品标签、链接或发布状态。保存目录不触发扫描，需要点击“立即扫描”。其他运行配置使用 `systemctl --user edit script-workbench.service` 添加环境变量覆盖，然后执行 `systemctl --user daemon-reload` 和 `systemctl --user restart script-workbench.service`。

| 环境变量 | 默认值 |
| --- | --- |
| `WORKBENCH_DATA_DIR` | `<项目>/data` |
| `WORKBENCH_HOST_KEY_FILE` | `<数据目录>/host.key` |
| `WORKBENCH_ROOTS_JSON` | `[]`；仅首次初始化导入目录，后续以数据库配置为准 |
| `WORKBENCH_PREVIEW_OUTPUT_ROOT` | `<项目>/data/previews` |
| `WORKBENCH_PREVIEW_RENDERER` | `<项目>/preview_generator/build/ofs-preview-renderer` |
| `WORKBENCH_HEATMAP_TOOL` | `<项目>/backend/tools/heatmapcreatorv1.0.exe`（通常无需覆盖） |
| `WORKBENCH_OPEN_MODE` | 安装服务时设为 `gateway` |

Windows 网关跟随后端授权的目录配置打开文件夹，更换素材目录无需修改源码。改变数据目录或密钥位置时，也要给 Windows 网关传入同一密钥文件。

## 5. 安装 Windows 自启动

自启动通过当前用户的 Windows 计划任务 `ScriptWorkbench` 托管，登录 Windows 后延迟 15 秒启动。启动器保存在 `%ProgramData%\ScriptWorkbench`，启动 WSL 后端、保持 WSL 运行并检查两个网页入口；后端或网关退出后自动重试。关闭 Codex、浏览器或启动命令窗口不影响计划任务。无需保存 Windows 密码，也不要求打开 Codex。

安装前先确认 Windows 可读取 WSL 项目、Windows Python 的实际路径，以及本机局域网 IPv4。以下命令在 Windows PowerShell 中运行，将示例地址和 Python 路径替换为实际值。安装器依赖本机已有的 `C:\Users\<用户名>\.codex\bin\Invoke-WslProject.ps1` 及其 WSL 配置，该执行器作为普通脚本独立工作。

先将安装器复制到 Windows 本地，避免 UNC 来源触发脚本签名限制：

```powershell
$projectPath = '\\wsl.localhost\Ubuntu-24.04\home\user\projects\script-workbench'
$runtimePath = Join-Path $env:ProgramData 'ScriptWorkbench'
New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
Copy-Item -LiteralPath "$projectPath\scripts\Install-Autostart.ps1" -Destination "$runtimePath\Install-Autostart.ps1" -Force
& "$runtimePath\Install-Autostart.ps1" -ProjectPath $projectPath `
  -LanHost '192.168.1.100' `
  -PythonPath 'C:\Path\To\Python312\python.exe'
```

安装器立即启动任务，重复安装会刷新启动器和配置。运行目录只允许当前安装用户、管理员和系统写入。任务使用普通用户权限，无最长运行时间限制，避免多份同时运行；临时网络或 WSL 启动失败会重试。WSL 服务安装器启用用户 linger，使用户服务不依赖终端会话。

这里的自动启动发生在该用户登录 Windows 后。未登录 Windows 时启动不在普通用户任务的覆盖范围内。

管理员 PowerShell 中允许私有局域网访问 8787（已有规则时跳过）：

```powershell
if (-not (Get-NetFirewallRule -Name 'ScriptWorkbench-LAN' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -Name 'ScriptWorkbench-LAN' -DisplayName 'Script Workbench LAN' `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8787 `
    -Profile Private -RemoteAddress LocalSubnet
}
```

## 6. 检查访问与持久化

- 素材所在主机：`http://localhost:8788/`，可打开素材及预览目录。
- 其他局域网设备：`http://<Windows IPv4>:8787/`，可查看和维护数据。
- 本机和局域网入口的 `/api/health` 均应返回 `status: ok`。
- `/api/capabilities` 的 `can_open_folder` 本机应为 `true`，局域网应为 `false`。

在设置页添加并保存扫描目录，点击“立即扫描”初始化库存。修改一条库存的标签或 ES / Patreon 发布状态，刷新页面并重启服务后确认保留。两个平台独立维护、独立筛选；保存非空 ES 帖子链接会自动标记 ES 已发布。旧数据库升级时，原“已发布”迁移为两个平台均已发布，原“待发布”保留为待发布，有有效 ES 链接的作品补标 ES 已发布。新部署从本地目录与自己的数据库开始，扫描不会自动发生。

电脑重启并登录安装任务的 Windows 用户后自动启动。局域网 IP 或 Python 路径改变时重新运行安装器更新配置。

## 7. 停止、更新与日志

启动已安装的任务：

```powershell
Start-ScheduledTask -TaskName 'ScriptWorkbench'
```

停止当前任务、网关与后端：

```powershell
$projectPath = '\\wsl.localhost\Ubuntu-24.04\home\user\projects\script-workbench'
& "$env:ProgramData\ScriptWorkbench\Stop-Workbench.ps1" -ProjectPath $projectPath
```

停止脚本先停止守护任务，避免网关被再次拉起；下次登录仍会自启动。如需取消以后自动启动，在停止后执行 `Disable-ScheduledTask -TaskName 'ScriptWorkbench'`；恢复时执行 `Enable-ScheduledTask -TaskName 'ScriptWorkbench'` 后再启动任务。

更新前先停止网关及服务并备份数据，然后在 WSL 项目目录执行：

```bash
git pull --ff-only
.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
cmake -S preview_generator -B preview_generator/build -DCMAKE_BUILD_TYPE=Release
cmake --build preview_generator/build -j4
systemctl --user start script-workbench.service
```

更新启动脚本后重新运行安装器，将新版本复制到 Windows 运行目录，再启动计划任务。保留 `data/`，更新代码不会重建或清空数据库。

后端日志：

```bash
journalctl --user -u script-workbench.service -n 100 --no-pager
```

自启动日志：`%ProgramData%\ScriptWorkbench\autostart.log`，WSL 保活错误：同目录 `keeper.stderr.log`；任务状态：`Get-ScheduledTask -TaskName ScriptWorkbench`。网关日志：`data/gateway.stdout.log`、`data/gateway.stderr.log`。若网关报告 WSL 服务不可用，先检查后端健康接口；若局域网不可达，检查 Windows IPv4、网络配置文件和 8787 防火墙规则。

## 8. 备份与恢复

停止网关及后端后，在 WSL 中备份整个 `data/`：

```bash
mkdir -p /home/user/backups
backup_file="/home/user/backups/script-workbench-data-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$backup_file" data
```

恢复时先停止服务，将备份的 `data/` 恢复到同一项目目录，保留数据库与 `host.key`，再启动后端及网关。运行中的 SQLite 不应只复制主数据库文件；停机备份可同时保留 WAL 等配套文件。若配置了项目之外的预览输出目录，该目录中的成品需要另行备份。备份包含私有运行数据和密钥，保存在受控位置。

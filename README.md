# 脚本工作台部署指南

部署方式：Windows 主机保存素材，WSL Ubuntu 24.04 运行 FastAPI、构建后的 React 页面和 SQLite，Windows 网关提供本机及局域网访问。

## 1. 准备环境

- Windows：WSL 2、Ubuntu 24.04、PowerShell 7、Python 3.12；Python 可执行文件路径需要传给网关。
- WSL：启用 systemd，安装 Python 3.12、Node.js 22.12 或更新版本、npm、Git、FFmpeg 和 C++ 构建依赖。本项目当前使用 Node.js 24。
- 端口：WSL 后端 `127.0.0.1:8789`，Windows 本机入口 `127.0.0.1:8788`，局域网入口 `<Windows IPv4>:8787`。
- 素材目录：`D:\Media\2026` 和 `D:\Media\workspace`，WSL 对应 `/mnt/d/Media/2026` 和 `/mnt/d/Media/workspace`。

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

检查两个素材目录在 WSL 中可读。生成结果写入 `D:\Media\workspace\预览\`，该目录需要写权限。

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

默认配置适用于上述 D 盘目录。如果需要调整配置，使用 `systemctl --user edit script-workbench.service` 添加环境变量覆盖，然后执行 `systemctl --user daemon-reload` 和 `systemctl --user restart script-workbench.service`。

| 环境变量 | 默认值 |
| --- | --- |
| `WORKBENCH_DATA_DIR` | `<项目>/data` |
| `WORKBENCH_HOST_KEY_FILE` | `<数据目录>/host.key` |
| `WORKBENCH_ROOTS_JSON` | 两个默认素材目录的 WSL/Windows 路径映射 |
| `WORKBENCH_PREVIEW_OUTPUT_ROOT` | `/mnt/d/Media/workspace/预览` |
| `WORKBENCH_PREVIEW_RENDERER` | `<项目>/preview_generator/build/ofs-preview-renderer` |
| `WORKBENCH_OPEN_MODE` | 安装服务时设为 `gateway` |

Windows 网关的目录打开白名单目前固定在 `scripts/windows_gateway.py` 的 `FOLDER_ROOTS`；更换素材目录时须与后端路径映射一并调整。改变数据目录或密钥位置时，也要给 Windows 网关传入同一密钥文件。

## 5. 启动 Windows 网关

先确认 Windows 能访问 `http://127.0.0.1:8789/api/health`。以下命令在 Windows PowerShell 7 中运行，把 `-LanHost` 换成本机实际局域网 IPv4，把 `-PythonPath` 换成实际 Python 路径。

现有启停脚本依赖当前 Windows 主机安装的 `C:\Users\<用户名>\.codex\bin\Invoke-WslProject.ps1`，执行器须配置为 Ubuntu 24.04 及当前项目根目录。

```powershell
$projectPath = '\\wsl.localhost\Ubuntu-24.04\home\user\projects\script-workbench'
& "$projectPath\scripts\Start-Workbench.ps1" `
  -LanHost '192.168.1.100' `
  -PythonPath 'C:\Path\To\Python312\python.exe'
```

没有该执行器的主机，可以在 WSL 中通过 `systemctl --user start script-workbench.service` 启动后端，再在 Windows 直接启动网关：

```powershell
$projectPath = '\\wsl.localhost\Ubuntu-24.04\home\user\projects\script-workbench'
$pythonPath = 'C:\Path\To\Python312\python.exe'
$lanHost = '192.168.1.100'
$gatewayArgs = @(
  ('"' + "$projectPath\scripts\windows_gateway.py" + '"'),
  '--lan-host', $lanHost,
  '--host-key-file', ('"' + "$projectPath\data\host.key" + '"')
)
$gatewayProcess = Start-Process -FilePath $pythonPath -ArgumentList $gatewayArgs `
  -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput "$projectPath\data\gateway.stdout.log" `
  -RedirectStandardError "$projectPath\data\gateway.stderr.log"
$gatewayProcess.Id | Set-Content -LiteralPath "$projectPath\data\gateway.pid"
```

直接启动前确认没有已有网关进程占用 8787/8788；现有 `Start-Workbench.ps1` 会自动检查并复用健康进程。

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

在设置页保存扫描目录，点击“立即扫描”初始化库存。修改一条库存的标签或发布状态，刷新页面并重启服务后确认保留。新部署从本地目录与自己的数据库开始，扫描不会自动发生。

电脑重启后打开 WSL 并重新启动 Windows 网关；服务安装脚本启用了 WSL 用户服务，Windows 网关未安装开机自启任务。局域网 IP 改变时使用新地址重新启动网关。

## 7. 停止、更新与日志

已安装执行器的 Windows 主机：

```powershell
& '\\wsl.localhost\Ubuntu-24.04\home\user\projects\script-workbench\scripts\Stop-Workbench.ps1'
```

未安装执行器时，在 Windows 按已保存 PID 停止网关，并先核实进程命令行包含当前项目的 `windows_gateway.py`；在 WSL 执行 `systemctl --user stop script-workbench.service`。

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

最后重新启动 Windows 网关。保留 `data/`，更新代码不会重建或清空数据库。

后端日志：

```bash
journalctl --user -u script-workbench.service -n 100 --no-pager
```

网关日志：`data/gateway.stdout.log`、`data/gateway.stderr.log`。若网关报告 WSL 服务不可用，先检查后端健康接口；若局域网不可达，检查 Windows IPv4、网络配置文件和 8787 防火墙规则。

## 8. 备份与恢复

停止网关及后端后，在 WSL 中备份整个 `data/`：

```bash
mkdir -p /home/user/backups
backup_file="/home/user/backups/script-workbench-data-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$backup_file" data
```

恢复时先停止服务，将备份的 `data/` 恢复到同一项目目录，保留数据库与 `host.key`，再启动后端及网关。运行中的 SQLite 不应只复制主数据库文件；停机备份可同时保留 WAL 等配套文件。预览成品位于 D 盘专用目录，需要另行备份。备份包含私有运行数据和密钥，保存在受控位置。

# 项目约定

- 项目实际执行位置是 `/home/user/projects/script-workbench`；Windows Desktop 使用全局 WSL 执行器运行项目命令。
- `scripts/Start-Workbench.ps1`、`Stop-Workbench.ps1` 与 `windows_gateway.py` 是 Windows 主机适配层，必须使用 Windows 工具运行。其他构建、Python 后端、测试、Git 均在 WSL 执行。
- 原始素材只读，不删除、移动、重命名或写入用户的视频、脚本及其他素材。用户已明确授权专用生成目录 `D:\Media\workspace\预览\<完整编号>\` 保存预览 WebM、GIF 和热力图；仅此输出目录可新建并写入生成结果，不扩大素材写权限。此目录整体排除出库存发现与素材扫描。
- 保留发布状态与人工字段，扫描不能覆盖它们。只以完整编号匹配库存；子编号独立。
- 私有凭据、数据库、历史快照、封面缓存位于被 Git 忽略的 `data/`，不得提交或打印凭据。
- 网关仅转发固定本机后端。LAN 入口不能授予打开主机文件夹的能力。
- 后端行为变化运行对应 pytest；前端变化运行 TypeScript/Vite 构建，重大交互变化需 HTTP 浏览器验证。


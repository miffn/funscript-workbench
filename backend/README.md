# Backend

FastAPI 服务由 `backend.main:app` 提供，SQLite 及封面保存在项目 `data/` 中。原始素材只读，不移动、重命名或修改。扫描 worker 仅处理用户手动提交的扫描及封面缓存，服务启动不新增扫描、没有定时调度；独立的预览 worker 串行处理生成任务，长任务期间库存扫描与网页继续可用。API 返回中文错误消息。

扫描固定为手动模式；旧的 `WORKBENCH_SCAN_INTERVAL` 环境变量不再生效。

## Configuration

| Environment | Default | Purpose |
| --- | --- | --- |
| `WORKBENCH_DATA_DIR` | `<project>/data` | 持久化数据库、封面和历史快照目录 |
| `WORKBENCH_ROOTS_JSON` | D 盘 `2026` / `workspace` 对应 WSL 路径 | `[{"path":"/mnt/d/...","windows_path":"D:\\...","label":"workspace"}]` |
| `WORKBENCH_FFMPEG` / `WORKBENCH_FFPROBE` | `ffmpeg` / `ffprobe` | 封面工具可执行文件 |
| `WORKBENCH_HOST_KEY_FILE` | `<data>/host.key` | 本机网关共享密钥；永不返回给浏览器 |
| `WORKBENCH_OPEN_MODE` | `native` | 生产设 `gateway`，由 Windows 网关启动 Explorer |
| `WORKBENCH_PREVIEW_OUTPUT_ROOT` | `/mnt/d/Media/workspace/预览` | 专用成品根目录，扫描与未编号统计排除其整个子树 |
| `WORKBENCH_PREVIEW_RENDERER` | `<project>/preview_generator/build/ofs-preview-renderer` | 已构建的 EGL/OpenGL 渲染器 |

历史快照放在 `data/import/master-pipeline.json` 和 `data/import/monthly-release-plan.json`，采用 `{"rows":[{"Script ID":"S029",...}]}`。首次导入后不再读取外部数据，导入状态按完整编号应用一次。未匹配资料产生提醒，不增加库存。

## Manual scan directory selection

`GET /api/settings` 中 `roots` 仍为配置允许的完整根目录列表，每项增加 `enabled`；`scan_roots_revision` 为选项版本，旧数据库默认全部启用、版本 0。`PUT /api/settings/scan-roots` 提交 `{enabled_paths:["/mnt/d/Media/workspace"],expected_revision:0}`，返回同 GET 的完整设置。路径必须精确匹配配置，重复项去重，至少选一个；非法请求返回 422，旧版本返回 409。选项保存在 SQLite，保存不触发扫描。

手动扫描任务在 `jobs.inputs` 保存 `enabled_paths` 和 `scan_roots_revision`。相同选定目录的活动任务可合并，目录选择变化后提交新范围任务，已有任务及重启恢复均沿用原快照。升级前无输入的旧手动任务使用当前选项；没有有效启用目录时拒绝提交任务。取消勾选只停止该目录的后续扫描，其作品、人工字段、标签、目录及素材记录、诊断和封面保留。扫描只更新所选目录，封面选源也仅使用任务选定目录；跨根共享作品的当前源生成失败时保留未选根已有可用封面。选择设置不改变主机打开和预览的完整配置根目录权限。

## Workspace profile

`GET /api/profile` 同时返回姓名、简介、头像及 `es_home` / `patreon_home` 平台主页。`PUT /api/profile` 使用 `expected_revision` 防止覆盖其他客户端的修改；主页只接受安全的 HTTP(S) 地址，可清空。未传主页字段的旧客户端保存个人资料时会保留已有主页。

## Host opening

只有携带共享密钥且 Host 为 `localhost:8788` 或 `127.0.0.1:8788` 的请求能获得本机能力。打开接口还要求对应的同源 Origin。路径只能来自已存储且当前可访问的目录 ID，解析后须位于配置根目录内且 Windows 路径映射相符。

`gateway` 模式返回内部响应头 `X-Workbench-Open-Folder`，其值为 UTF-8 Windows 路径的 URL-safe base64。Windows 网关仅在本机入口的成功打开响应上消费该头，用 `shell=False` 调用 Explorer，再剥离响应头。局域网入口必须剥离浏览器传入的所有共享密钥头。后端仅绑定 loopback，不能直接开放至局域网。

返回“已发送打开请求”代表调用已提交，不保证资源管理器窗口已经呈现。

## Preview API

- `GET /api/works/{id}/preview`：任务及可用 WebM/GIF 列表、输出路径；支持已由 CLI 生成的同编号 manifest。
- `POST /api/works/{id}/preview`：可选 `{"video_asset_id":123}`，202 返回持久任务。唯一可用视频自动选；多个视频必须显式选择。同作品相同选择的活动请求复用任务，活动任务选择不同视频返回 409。
- `GET /api/works/{id}/preview/files/{filename}`：仅提供 `预览视频1.webm`～`预览视频4.webm` 和 `预览gif1.gif`～`预览gif4.gif`；不提供原片、脚本、manifest 或任意路径。
- `POST /api/works/{id}/preview/open-folder`：与库存目录相同的主机密钥及同源限制，Windows 路径必须映射到已配置库存根目录。

只接受作品关联的视频素材 ID。提交前及执行前均核实当前真实路径、可访问性、根目录边界及所有路径组件，拒绝符号链接；脚本只按视频精确同名的实际六轴发现并完整校验，不静默跳过损坏轴。默认使用 builtin 模型及 HUD，在 20/40/60/80% 各取 10 秒，正常输出四对独立媒体；短视频依照生成器既有规则去重并保留原片段编号。

任务 `type=preview`、输入和进度保存在 SQLite，浏览器关闭不会取消。服务关闭向生成器传递取消信号，正常终止子进程；中断任务重启后保持原编号与视频选择，继续执行幂等生成。遗留进程锁只在其 PID 匹配该任务记录的上次生成 PID 且已确认进程不存在时回收，外部 CLI 或活跃进程的锁保留。

成功 manifest 存一份服务端恢复记录。失败及重试期间仍展示完整旧成品，当前任务失败原因单独返回；新重试活动时清除旧失败提示。成品按固定目录、完整编号、文件名单、元数据、大小与 SHA-256 校验；哈希按文件 stat 缓存，轮询不会重复读取未变化的完整媒体。

## Classification tags

`GET /api/tags` 返回共享词典和实际导入报告；`POST /api/tags` 创建分类，`PATCH /api/tags/{id}` 编辑名称及作者地址，必须提交当前 `expected_revision`。标签按照类别和名称 `casefold()` 唯一；共享标签改名保持 ID 及关联，所有绑定库存立即读取新名称和资料。共享改名还会原子保护已有绑定为人工维护，并提升各库存 `tags_revision`，避免重新导入撤销改名和旧编辑窗覆盖。

`GET /api/works/{id}/tags` 返回当前绑定与 `tags_revision`；`PUT` 提交完整 `{tag_ids:[...],expected_revision:...}`。作者、视频类型、发布类型和档位每类单选，自定义分类可多选。`Free Sample` 可配 `Free`；`Paid` 可配 `Main Tier` / `Extra Tier`；缺一允许待补充。过期版本返回 409，失败事务不改变原选择。人工清空也记录保护状态，扫描或重导入不会填回。

作者支持地址状态为 `unknown`、`none` 或 `url`，仅 `url` 状态允许非空且有效的 http(s) 地址，禁止 URL 用户名密码。其他类别不保存支持地址。作者地址属于共享词典，修改一次即供全部绑定作品复用。

库存列表和详情增加 `tags`、`tags_revision`，视频类型优先读取当前分类。人工移除视频类型后不会回退旧快照，原历史 metadata 保留。`GET /api/works` 支持 `tag_id`、逗号分隔的 `tag_ids`、`untagged_only=true|false`，搜索可匹配标签名称；组合标签同类别 OR、跨类别 AND，无标签筛选不计自动轴字段。默认 `sort_platform=es&sort_direction=desc` 按 ES 实际发布日期降序排列；可切换 `patreon` 和 `asc`，空日期始终置后，排序先于分页。

发布快编的 `GET/PATCH /api/works/{id}/links` 包含平台状态、实际日期、独立计划日期与四个链接。链接写入保留 `expected_revision`；修改状态、日期或计划还需 `expected_publication_revision`，所有修改在一个事务内完成。新增或更改非空 ES / Patreon 帖子链接时，未发布的平台自动记已发布与北京时间当天；已发布平台保留原日期，包括人工清空的日期。移除链接不撤回发布，视频／脚本链接不改变平台状态。

## Explicit historical tag import

在 WSL 项目内运行（Windows Codex 使用全局 WSL 执行器）：

```bash
.venv/bin/python -m backend.import_tags \
  --monthly data/tag-integration-check/monthly-release-plan.json \
  --master data/tag-integration-check/master-pipeline.json \
  --data-dir data --dry-run --report data/tag-import-plan.json
```

去掉 `--dry-run` 才执行导入。快照实际文件名由调用者指定；没有数据库时拒绝创建空库存。dry-run 只读打开已有数据库，即使旧库尚无标签表也不会迁移或写入。指定 `--report` 时完整计划保存到该文件，stdout 仅输出统计摘要；未指定时 stdout 输出完整 JSON。

两表只按完整编号匹配已经扫描的作品。视频类型、发布类型和档位优先月表，作者优先主表；来源内部的不同有效值不猜选，空值和 TBD 不成为标签。历史未匹配作者可建立词典，但不创建作品。同名作者多个支持地址保留候选，状态为 unknown；指向 ES 帖子的原支持地址保留并列入用途核对提醒。

报告包含 `matched/skipped/created/bindings`、`matched_by_source`、`conflicts`、`warnings`、每作品 `plans` 和词典 `tag_plans`。执行时在一个事务内重读人工保护状态，保留人工绑定、清空、共享改名和作者支持地址。重复导入不会重复创建或重绑未变化的标签。导入只写标签、绑定、版本/保护状态和报告，不改变作品 metadata、标题、备注、发布状态、ES/Patreon/视频链接，也不调用外部资料源或修改素材。

## Checks

在项目 Linux 环境运行 `.venv/bin/python -m pytest backend/tests -q`。Windows Codex 中必须经 `Invoke-WslProject.ps1` 执行。测试使用临时目录及测试密钥，不读写真实库存、不打开 Explorer。

覆盖完整编号与子编号、重复目录、素材缺失、未编号素材、人工字段保留、目录移动和断根、一次性历史迁移、同源与主机密钥限制、路径映射及符号链接防护、封面失败缓存/重试、重启恢复及强制刷新合并；另覆盖预览 API、活动任务并发去重/选择冲突、独立扫描线程、恢复原始输入、错误与旧成品保留、媒体权限/路径边界、专用输出树排除及真实 tiny FFmpeg/EGL 端到端生成。

标签检查另覆盖分类单选/组合、URL 状态与非法输入、Unicode casefold 去重、共享资料即时复用、并发 409、无标签/标签筛选、完整子编号、两表优先级和冲突、只读旧库 dry-run、幂等导入、手工清空及共享改名保护、扫描保留和人工字段不变。

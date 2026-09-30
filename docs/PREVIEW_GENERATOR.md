# 独立预览生成程序与 S064 验证

日期：2026-09-30。程序位于 `/home/user/projects/script-workbench/preview_generator`。独立预览与模型/Pitch HUD 已通过用户验收；随后接入工作台详情与后台持久任务，接入说明见下文。热力图与发帖资料不在本次范围。

## 输出与输入

选择一个原视频及明确对应的全部轴脚本。默认按视频流实际时长的 20%、40%、60%、80% 起点各截 10 秒，分别生成：

- 四个 `预览视频N.webm`：1920×1080、30 fps、VP9、原音频 Opus，原视频画面叠加脚本驱动的 3D 模型。
- 四个 `预览gifN.gif`：192×108、10 fps、无限循环、只有原视频画面，无模拟器及音频。
- `manifest.json`：输入指纹、轴关联、模型、工具及参数、区间、输出校验和、完成或失败状态，供未来后端读取。

成品统一放到 `D:\Media\workspace\预览\<完整编号>\`。临时 RGBA 和取样文件位于项目内部 `data/preview-generator/`，任务结束清理。源视频与脚本不写入。后端接入同步排除专用预览根目录及其子树，不作为库存、源视频或未编号素材提示。

同名视频的 `.funscript` 与 `.pitch.funscript` 等精确同名轴文件可自动关联；其他命名或冲突使用显式轴与路径选择。每帧按原视频绝对时间对所有轴线性插值，一起驱动同一个模型。缺失轴中立值为 50。坏脚本、空脚本、重复动作时间等会报错。首点前和末点后保持端点位置，并记录区间覆盖提醒，修正 OFS 首点前取末点的边界问题。

## 渲染器

从用户的自定义 OFS 4.0.8 中提取 Mesh、SceneGraph、SceneShader、GLTF loader、六轴映射、数学函数与内置模型逻辑。现已进一步核对真正源仓库 [miffn/OFS-custom](https://github.com/miffn/OFS-custom)，固定提交 `d341a387649a3a9cdd5757ad9f0b1a3ca83af7d8`。保留颜色、着色与变换顺序，替换 OFS 应用状态、SDL 回调为 EGL 无界面渲染；保留源实现的轴数值 HUD，通过离屏绘制输出。渲染每帧透明 RGBA，由 FFmpeg 合成到原视频右下方；不进行桌面录屏，不要求桌面会话或正在运行的 OFS。

默认模型修正为源实现 `selectedModel` 为空时的白色圆柱和两侧彩色 Twist 标记，使用 `model="builtin"`。先前选用的人物 GLB 并非此次要求的默认模型，只保留为显式可选资源。镜头 yaw/pitch=0、distance=8.4、FOV=45、scale=0.8、PitchRange=60。默认透明画布 480×480，右下边距 32。可通过接口指定其他 GLB 及镜头、缩放、画布尺寸；运行不依赖原 ES post 源目录。

本版本保留模型、运动和源实现的数值条、Stroke 位移徽标及引线，去掉 OFS 编辑控件和曲线。HUD 只显示实际关联脚本的轴：S064 为 Stroke/Pitch，必须包含 Pitch 数值。Python 将 `--axes-present stroke,pitch` 显式传给渲染器，缺失轴的中立值不代表轴实际存在。默认 HUD 文字缩放为 1.1，支持 `--hud-text-scale` 和 `--no-axis-hud`。GIF 不包含模型或 HUD。`manifest.model_source` 记录源码仓库及提交，`manifest.hud` 记录显示轴和参数，均参与缓存指纹。当前 WSL 使用 Mesa llvmpipe 软件离屏渲染已实测成功，未来 GPU 加速性能未验证。

HUD 几何、颜色和标签取自源实现，使用 18 像素基础字体适配独立输出画布；此适配不代表与原桌面 GUI 逐像素一致。

来源和许可见 `preview_generator/vendor/PROVENANCE.md` 及保留的 GPLv3/第三方许可证。原 ES post 项目未修改。

## 程序接口

- Python：`generate(Config(...), on_progress=callback, cancel_event=event)`，返回完整 manifest。
- 命令行：`python -m preview_generator --work-id S064 --video <Linux绝对路径>`，支持 JSON 配置和显式轴映射。
- stdout 为 JSON Lines，进度、完成结果与错误分开标记。支持取消、失败清理、输出锁和已有成功成品保留。
- 同输入、模型、工具、参数且成品校验和匹配时复用文件，支持强制重新生成。

构建、调用及完整参数说明见 `preview_generator/README.md`。Windows Desktop 调用统一通过 `Invoke-WslProject.ps1`，并指定此实际 WSL 项目；无需新建服务。

## S064 验证

### 工作台接入

作品详情提供“一键生成预览”，多个原视频时须选择视频素材 ID；后端按库存记录重新验证文件和精确同名轴脚本，不接受浏览器传入路径。已生成的命令行结果也能在网页直接显示。结果未变化时复用，失败可重试。

- `GET /api/works/{work_id}/preview`：最新任务、固定成品列表与目录。
- `POST /api/works/{work_id}/preview`：提交可选 `video_asset_id`，返回持久 preview 任务。同作品重复提交复用活动任务；不同源视频的同时提交提示冲突。
- `GET /api/works/{work_id}/preview/files/{filename}`：只提供该编号固定 WebM/GIF 成品，可下载并支持 Range。
- `POST /api/works/{work_id}/preview/open-folder`：沿用本机能力与同源验证，通过 Windows 网关打开该编号预览目录；局域网不支持打开。

扫描和预览有独立执行线程，预览串行处理，不阻塞库存扫描。进度、失败和输入选择存入 SQLite；关闭网页后继续运行，服务重启恢复未完成任务。输出根目录及渲染器位置可由 `WORKBENCH_PREVIEW_OUTPUT_ROOT` / `WORKBENCH_PREVIEW_RENDERER` 配置，生产保持用户授权的默认目录。历史完整 manifest 另存 `data/preview-state`，重试过程中可继续使用仍校验通过的旧成品。

已部署并通过 S064 网页按钮验证：任务 `208` 完成，进度 100%，八个文件因输入未变全部复用，后台耗时 5.035 秒。关闭再打开详情可读回结果；八个下载接口支持 Range，局域网打开目录返回 403。接入没有改变 58 个库存的人工字段。具体报告见 `data/integration-check/S064-backend-deployment.json`，测试及部署记录见 `IMPLEMENTATION.md`。

### 独立阶段媒体验证

以下为白圆柱和 Pitch HUD 修正后的实际重新生成证据。此前人物 GLB 的验证报告只保留为历史，不能证明本次模型和 HUD 正确。

新版渲染器 `ofs-preview-renderer/0.2.0` 已通过 2 项 C++/离屏渲染测试，Python **47 项通过（5.78 秒）**。新增验证覆盖默认白圆柱、实际轴 HUD 协议、源码版本 manifest、HUD 参数边界及参数改变后的缓存失效；真实 FFmpeg/EGL 四对小样仍检查音频和纯原视频 GIF。

输入 `D:\Media\workspace\S064\sample.mp4`（3840×2160、60 fps）及对应 Stroke/Pitch 两轴。视频流时长 212.05 秒（容器时长 212.091066 秒，选段使用实际视频流）。

四段起点为 42.41、84.82、127.23、169.64 秒，均 10 秒。新版完整生成 52.162 秒，输入指纹约 4.4 秒，输入文件已有系统缓存；这不是冷启动性能保证。实际成品位于 `D:\Media\workspace\预览\S064`。

- 八个文件均独立完整解码通过。
- 每个 WebM 300 帧、30 fps、1920×1080，包含 Opus 音频；容器时长 10.008 秒，音频编码包造成的毫秒级偏差不增加视频帧。
- 每个 GIF 100 帧、10 fps、192×108、10 秒，无音频。
- 四张 GIF 与旧版纯原视频 GIF 的 SHA-256 全部相同，模型和 HUD 修正未进入 GIF。
- 四段首帧按对应绝对时间重新计算 Stroke/Pitch，并以相同模型、HUD 参数独立渲染，与成品模型区域像素校对一致；平均 RGB 误差分别为 1.176、1.079、1.066、0.965（0–255 范围，有损编码误差）。
- 离屏图像检查验证右侧 Pitch 数字在 20/80 时发生变化，关闭 HUD 后该区域消失；S064 第一段首帧数值为 Pitch 43。模拟器独立截图为 `data/preview-generator-check/S064-simulator-corrected.png`。
- 生成后重新计算原视频及两轴脚本 SHA-256，与开始时一致。
- 独立验证脚本：`python -m preview_generator.renderer.verify_release '<manifest绝对路径>'`。此脚本针对本次 S064 的两轴、四段默认参数；不是通用生产入口。
- 验证报告保存在工作台内部 `data/preview-generator-check/S064-verification-corrected.json`。

新版 S064 用 Python API 重复调用 4.268 秒，八个媒体全部复用，修改时间不变，渲染和编码调用次数为零；证据见 `data/preview-generator-check/S064-cache-check.json`。新版完整生成清单另存 `S064-generated-manifest-corrected.json`，避免复用更新 manifest 的耗时字段后丢失生成证据。旧人物版本的 `S064-verification.json` 与 `S064-generated-manifest.json` 为历史记录。


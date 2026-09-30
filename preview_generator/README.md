# 独立预览生成程序

本程序不启动 OFS、ScreenToGif 或桌面录屏，不依赖工作台后端。输入一个原视频及其关联轴脚本，通过无窗口 EGL/OpenGL 渲染器驱动 3D 模型，使用 FFmpeg 截取原视频、叠加模型并编码。Python 部分只使用标准库；渲染器源码、所需头文件和当前使用的 GLB 模型随程序保留。

默认从视频实际时长的 20%、40%、60%、80% 起，各取 10 秒，分别输出 **四个 WebM 和四个 GIF，不拼接**：

| 文件 | 内容 | 默认规格 |
| --- | --- | --- |
| `预览视频1.webm`～`预览视频4.webm` | 原视频 + 3D 模拟器；保留原音频 | 1920×1080，30 fps，VP9/Opus |
| `预览gif1.gif`～`预览gif4.gif` | 只有原视频，不含模型 | 192×108，10 fps，循环 GIF |
| `manifest.json` | 输入 SHA-256、轴关联、模型与工具指纹、实际时间段、参数、成品校验值、任务状态与错误 | UTF-8 JSON |

编号 1～4 与百分比顺序对应。非 16:9 原视频等比例缩放、补黑边，不拉伸。模拟器画面默认 480×480、右下角留 32 像素边距，透明背景，无曲线、编辑界面、水印。默认使用 OFS 源码的**白色圆柱及彩色 Twist 标记**（`model="builtin"`），保留 Stroke 位移徽标、引线和实际关联轴的数值 HUD；S064 关联 Stroke/Pitch 时显示 Pitch 数值条，不显示其他未关联轴。HUD 默认文字缩放 1.1。镜头和轴运动依据已核查的 OFS 源码与当前配置，参数可以显式调整。可选 GLB 模型保留顶点颜色及材质颜色因子，不实现完整纹理/PBR 渲染。输入没有音频时也能生成 WebM。GIF 始终只压制原视频，不包含模型或 HUD。

模型、运动与 HUD 的代码依据为 [miffn/OFS-custom](https://github.com/miffn/OFS-custom)，固定版本 `d341a387649a3a9cdd5757ad9f0b1a3ca83af7d8`。`manifest.json` 的 `model_source` 记录默认模型来源与版本，`hud` 记录显示开关、文字缩放及实际轴；这些设置和渲染器二进制都参与缓存指纹。人物 GLB 仍保留在 `models/` 作为显式可选模型，不再默认使用。

## 构建与运行

在 **当前选定的 WSL 项目** `/home/user/projects/script-workbench` 内执行。Windows Codex Desktop 项目命令必须通过全局 `Invoke-WslProject.ps1`；下面 Bash 命令供 WSL 终端使用：

```bash
sudo apt install cmake libegl1-mesa-dev libgl-dev libglm-dev ffmpeg
cmake -S preview_generator -B preview_generator/build -DCMAKE_BUILD_TYPE=Release
cmake --build preview_generator/build -j4
ctest --test-dir preview_generator/build --output-on-failure
```

Mesa 的 EGL 驱动可使用软件渲染，不需要 OFS 窗口或桌面会话。渲染器最终位置是 `preview_generator/build/ofs-preview-renderer`。Python 不需要安装新的依赖。运行 S064：

```bash
.venv/bin/python -m preview_generator \
  --work-id S064 \
  --video '/mnt/d/Media/workspace/S064/sample.mp4' \
  --script 'stroke=/mnt/d/Media/workspace/S064/sample.funscript' \
  --script 'pitch=/mnt/d/Media/workspace/S064/sample.pitch.funscript'
```

等价的 Windows 调用（不切换到同名其他项目）：

```powershell
& "$env:USERPROFILE\.codex\bin\Invoke-WslProject.ps1" `
  -WorkingDirectory '\\wsl.localhost\Ubuntu-24.04\home\user\projects\script-workbench' `
  -FilePath '.venv/bin/python' -ArgumentList @(
    '-m', 'preview_generator', '--work-id', 'S064',
    '--video', '/mnt/d/Media/workspace/S064/sample.mp4',
    '--script', 'stroke=/mnt/d/Media/workspace/S064/sample.funscript',
    '--script', 'pitch=/mnt/d/Media/workspace/S064/sample.pitch.funscript'
  )
```

默认成品保存到 `D:\Media\workspace\预览\S064\`。素材树内只允许写这个专用根目录下的对应完整编号目录；不能把 `--output-dir` 指到原视频或脚本文件夹。`S025_001` 等子编号原样保留。中间文件放在项目 `data/preview-generator/`，正常退出、错误或取消时清理。程序不生成热力图、不修改原素材、不改工作台数据库或发布状态。

省略全部 `--script` 时，会使用当前视频的 **精确同名** `video.funscript`、`video.pitch.funscript` 等文件。仅支持 Stroke、Surge、Sway、Twist、Roll、Pitch；不会混入目录内的其他视频脚本。不同版本请用 `--script axis=path` 明确选择，同一个轴不能指定两次；文件名仅大小写不同造成的重复也会报错。缺失轴使用中立位置 0.5；损坏文件、空动作、重复时间戳、越界位置会报错，不静默丢弃。

## 后端可复用接口

后续后端可以导入 Python API，无需拼接 shell 命令：

```python
from pathlib import Path
from threading import Event
from preview_generator import Config, generate, GenerationCancelled, GenerationError

cancel = Event()
config = Config(
    work_id="S064",
    video=Path("/mnt/d/Media/workspace/S064/sample.mp4"),
    scripts={
        "stroke": "/mnt/d/Media/workspace/S064/sample.funscript",
        "pitch": "/mnt/d/Media/workspace/S064/sample.pitch.funscript",
    },
)
manifest = generate(config, on_progress=lambda event: print(event), cancel_event=cancel)
# 其他线程调用 cancel.set() 可请求取消。
```

`Config` 字段均可通过 `--config config.json` 传入，CLI 显式参数覆盖 JSON。CLI 的 stdout 是逐行 JSON，工具诊断不会混入 stdout；日志事件带 `event=progress`、`work_id`、`stage`、单调不减的 `progress`（0～1）、`message` 和 `elapsed_seconds`，必要时含 `clip_index`。成功最后输出 `event=result`、manifest 路径、成品记录与 warnings；失败输出 `event=error`。退出码：成功 0、失败 1、取消 130。Ctrl+C 或 SIGTERM 请求取消并终止正在运行的工具。

配置示例：

```json
{
  "work_id": "S064",
  "video": "/mnt/d/Media/workspace/S064/sample.mp4",
  "scripts": {
    "stroke": "/mnt/d/Media/workspace/S064/sample.funscript",
    "pitch": "/mnt/d/Media/workspace/S064/sample.pitch.funscript"
  },
  "percentages": [0.2, 0.4, 0.6, 0.8],
  "clip_seconds": 10,
  "include_audio": true,
  "camera_distance": 8.4,
  "model_scale": 0.8,
  "model": "builtin",
  "show_axis_hud": true,
  "hud_text_scale": 1.1,
  "simulator_width": 480,
  "simulator_height": 480,
  "margin": 32
}
```

`--model /path/model.glb` 切换模型，`--model builtin` 使用默认白色圆柱。若需要保留原可选人物模型，显式指定 `--model preview_generator/models/blonde-chibi-simulator-ofs.glb`。`--hud-text-scale 1.1` 调整 HUD 文字大小（0.7～1.8），`--no-axis-hud` 隐藏数值条及位移徽标。`--force` 强制重新生成；`--no-audio` 输出无声 WebM。预览镜头范围与渲染器一致：yaw/pitch -180～180°、distance 1.5～20、FOV 15～80°、model-scale 0.1～5、pitch-range 0～90°；渲染面尺寸 16～4096。其他参数及默认值见 `python -m preview_generator --help` 和 `Config`。

渲染器协议保持独立：`--frames-json` 传入逐帧六元素数组，轴顺序为 `[stroke, surge, sway, twist, roll, pitch]`，各值 0～1；`--axes-present stroke,pitch` 标记实际有文件关联的轴，不能通过中立数值猜测存在状态；`--hud-text-scale` 与 `--no-axis-hud` 控制 HUD。`--output` 生成顶部起逐行、透明 RGBA 原始帧。Python 根据 `起点 + 帧序号/fps` 的**原视频绝对时间**，同时采样所有轴，传入同一个模拟器。输出字节数与预期帧数必须完全匹配。

## 边界、复用与失败处理

- 四个片段都用原视频时间驱动脚本，无论脚本动作点间距是否一致。正常区间沿用 OFS 线性插值；早于首动作时保持首位置、晚于末动作时保持末位置。首点前采用首位置是针对 OFS 源码首点前返回末位置的边界修正；manifest 会提示脚本覆盖不足。
- 末尾不足 10 秒时把起点向前移，视频本身不足 10 秒时按实际长度生成。完全相同的区间去重，保留原来的百分比序号，manifest 列出实际区间及提示；部分重叠的区间继续分别生成。
- 输入视频、全部选中轴脚本、模型、渲染器二进制、Python 生成/采样源码和工具版本都参与指纹。输入和配置相同且现有文件 SHA-256 完整时复用。任一轴变化会使预览重新生成。源文件大小或修改时间在任务中变化会报错，避免混用任务中变更的素材。
- 每个媒体先在成品目录隐藏临时文件中完整生成并用 ffprobe 校验格式、尺寸、音频策略和时长，校验成功后原子替换。失败不会把原有成功媒体换成半成品；各文件独立提交，manifest 精确记录本轮已完成的文件。未完成的旧文件仍保留，不会被当作本轮成功结果。
- 同一输出目录有进程锁，第二个任务会报错。进程被强制杀死后若遗留 `.preview-generator.lock`，先核实其中 PID 对应的任务已经退出，再移除锁；不会自动删除可能仍在使用的锁。
- 生成只保证确定的同步和编码流程；镜头、模型占比等视觉外观仍应由用户查看 S064 成品确认。软件渲染速度取决于机器、模型和视频尺寸。

## 验证

```bash
.venv/bin/python -m pytest preview_generator/tests -q
ctest --test-dir preview_generator/build --output-on-failure
```

Python 测试覆盖多轴绝对时间采样、首末边界、输入错误、精确关联与歧义、短视频去重、CLI JSON 合约；真实 FFmpeg + 离屏渲染集成覆盖四对独立输出、音频保留、GIF 不含模型、输入只读、复用、错误/取消保留旧文件及锁。

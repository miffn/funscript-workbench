#!/usr/bin/env python3
import argparse
import json
import math
import os
import struct
import sys
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


DEFAULT_WIDTH = 2000
DEFAULT_HEIGHT = 50
DEFAULT_MAX_SPEED = 2000.0
DEFAULT_REPORT_WIDTH = 2048
VERSION = "2026.07.22-ofs-extra-axes"


PALETTES = {
    "ofs": [
        (0.00, (0, 0, 0)),
        (0.025, (0, 128, 122)),
        (0.05, (0, 255, 138)),
        (0.075, (0, 247, 0)),
        (0.10, (120, 224, 0)),
        (0.125, (232, 189, 0)),
        (0.15, (255, 140, 0)),
        (0.175, (255, 64, 0)),
        (0.20, (255, 0, 0)),
        (0.225, (255, 0, 30)),
        (0.25, (255, 0, 171)),
        (0.275, (255, 0, 196)),
        (0.30, (150, 0, 197)),
        (0.325, (119, 0, 249)),
        (0.35, (82, 0, 255)),
        (0.375, (0, 0, 255)),
        (0.40, (0, 3, 254)),
        (0.425, (0, 90, 155)),
        (0.45, (0, 87, 88)),
        (0.475, (0, 88, 68)),
        (0.50, (4, 87, 45)),
        (0.525, (50, 82, 16)),
        (0.55, (74, 76, 0)),
        (0.575, (92, 68, 0)),
        (0.60, (105, 60, 0)),
        (0.625, (113, 52, 10)),
        (0.65, (116, 46, 39)),
        (0.675, (115, 45, 62)),
        (0.70, (109, 46, 82)),
        (0.725, (98, 50, 100)),
        (0.75, (84, 55, 114)),
        (0.775, (66, 62, 123)),
        (0.80, (42, 69, 125)),
        (0.825, (0, 76, 120)),
        (0.85, (0, 82, 110)),
        (0.875, (0, 86, 93)),
        (0.90, (0, 88, 74)),
        (0.925, (0, 87, 51)),
        (0.95, (41, 84, 25)),
        (0.975, (68, 78, 0)),
        (1.00, (87, 70, 0)),
    ],
    "classic": [
        (0.00, (18, 18, 20)),
        (0.18, (24, 55, 160)),
        (0.36, (0, 170, 210)),
        (0.54, (20, 210, 80)),
        (0.72, (245, 215, 45)),
        (0.88, (240, 105, 30)),
        (1.00, (235, 35, 35)),
    ],
    "gray": [
        (0.00, (0, 0, 0)),
        (1.00, (255, 255, 255)),
    ],
}

DEFAULT_SPEED_RESOLUTION = 4096
BUNDLED_FONT = "fonts/NotoSansCJKsc-Regular.otf"
_font_cache = {}
_font_source_logged = False
_font_source = None

LINE_COLOR_STOPS = [
    (0.00, (0, 238, 255)),
    (0.025, (0, 255, 243)),
    (0.05, (0, 255, 138)),
    (0.075, (0, 247, 0)),
    (0.10, (120, 224, 0)),
    (0.125, (232, 189, 0)),
    (0.15, (255, 140, 0)),
    (0.175, (255, 64, 0)),
    (0.20, (255, 0, 0)),
    (0.225, (255, 0, 30)),
    (0.25, (255, 0, 171)),
    (0.275, (255, 0, 196)),
    (0.30, (150, 0, 197)),
    (0.325, (119, 0, 249)),
    (0.35, (82, 0, 255)),
    (0.375, (0, 0, 255)),
    (0.40, (0, 3, 254)),
    (0.425, (0, 90, 155)),
    (0.45, (0, 87, 88)),
    (0.475, (0, 88, 68)),
    (0.50, (4, 87, 45)),
    (0.525, (50, 82, 16)),
    (0.55, (74, 76, 0)),
    (0.575, (92, 68, 0)),
    (0.60, (105, 60, 0)),
    (0.625, (113, 52, 10)),
    (0.65, (116, 46, 39)),
    (0.675, (115, 45, 62)),
    (0.70, (109, 46, 82)),
    (0.725, (98, 50, 100)),
    (0.75, (84, 55, 114)),
    (0.775, (66, 62, 123)),
    (0.80, (42, 69, 125)),
    (0.825, (0, 76, 120)),
    (0.85, (0, 82, 110)),
    (0.875, (0, 86, 93)),
    (0.90, (0, 88, 74)),
    (0.925, (0, 87, 51)),
    (0.95, (41, 84, 25)),
    (0.975, (68, 78, 0)),
    (1.00, (87, 70, 0)),
]

AXES = [
    ("L0", (213, 126, 18), ("l0", "stroke")),
    ("L1", (16, 196, 53), ("l1", "surge")),
    ("L2", (218, 88, 18), ("l2", "sway")),
    ("R0", (205, 166, 0), ("r0", "twist")),
    ("R1", (53, 153, 154), ("r1", "roll")),
    ("R2", (20, 205, 20), ("r2", "pitch")),
    ("vib", (155, 98, 220), ("vib",)),
    ("pump", (48, 178, 214), ("pump",)),
    ("raw", (150, 150, 150), ("raw",)),
]

AXIS_BY_TOKEN = {}
for axis_name, _axis_color, axis_tokens in AXES:
    for axis_token in axis_tokens:
        AXIS_BY_TOKEN[axis_token] = axis_name

AXIS_SUFFIX_SEPARATORS = (".", "_", "-", " ")


def safe_text(value):
    text = str(value)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding, errors="replace")


def log(message):
    print(safe_text(message))


def resource_path(relative_path):
    base_path = Path(__file__).resolve().parent
    return base_path / relative_path


def clamp(value, low, high):
    return max(low, min(high, value))


def lerp(a, b, t):
    return a + (b - a) * t


def smoothstep(edge0, edge1, value):
    if edge0 == edge1:
        return 0.0
    t = clamp((value - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def color_for_intensity(value, palette):
    value = clamp(value, 0.0, 1.0)
    stops = PALETTES[palette]
    return color_from_stops(value, stops, smooth=(palette == "ofs"))


def color_from_stops(value, stops, smooth=False):
    value = clamp(value, 0.0, 1.0)

    if value <= stops[0][0]:
        return stops[0][1]

    for idx in range(1, len(stops)):
        prev_pos, prev_color = stops[idx - 1]
        next_pos, next_color = stops[idx]
        if value <= next_pos:
            span = next_pos - prev_pos
            t = 0.0 if span <= 0 else (value - prev_pos) / span
            if smooth:
                t = smoothstep(0.0, 1.0, t)
            return tuple(int(round(lerp(prev_color[i], next_color[i], t))) for i in range(3))

    return stops[-1][1]


def png_chunk(chunk_type, data):
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def write_png(path, width, height, rgb_rows):
    raw = bytearray()
    for row in rgb_rows:
        raw.append(0)
        raw.extend(row)

    data = b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            png_chunk(b"IDAT", zlib.compress(bytes(raw), 9)),
            png_chunk(b"IEND", b""),
        ]
    )
    path.write_bytes(data)


def parse_duration_ms(data, actions):
    metadata = data.get("metadata")
    duration = None
    if isinstance(metadata, dict):
        duration = metadata.get("duration")
    if not isinstance(duration, (int, float)):
        duration = data.get("duration")
    if not isinstance(duration, (int, float)):
        return infer_duration_ms(actions)

    # Funscript metadata.duration is usually seconds. Some tools store ms.
    if duration > 10000:
        return float(duration)
    return float(duration) * 1000.0


def normalize_action(action):
    at = action.get("at")
    pos = action.get("pos")
    if not isinstance(at, (int, float)) or not isinstance(pos, (int, float)):
        return None
    return (float(max(0, round(at))), float(max(0, min(100, round(pos)))))


def read_funscript(path):
    with path.open("r", encoding="utf-8-sig") as file:
        data = json.load(file)

    actions = data.get("actions")
    if not isinstance(actions, list):
        raise ValueError("missing actions array")

    parsed = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        normalized = normalize_action(action)
        if normalized is not None:
            parsed.append(normalized)

    parsed.sort(key=lambda item: item[0])
    # funscript-utils computes duration from the last sorted action.
    return parsed, infer_duration_ms(parsed)


def split_axis(path):
    stem = path.stem
    lowered = stem.casefold()

    for token, axis in sorted(AXIS_BY_TOKEN.items(), key=lambda item: len(item[0]), reverse=True):
        for sep in AXIS_SUFFIX_SEPARATORS:
            suffix = f"{sep}{token}"
            if lowered.endswith(suffix):
                group = stem[: -len(suffix)].rstrip(" ._-")
                if group:
                    return axis, group

    return "L0", stem


def group_funscripts(paths):
    groups = {}
    for path in paths:
        axis, group = split_axis(path)
        groups.setdefault(group, {})[axis] = path
    return groups


def collect_reference_group_inputs(input_path):
    axis, group = split_axis(input_path)
    group_key = group.casefold()
    siblings = find_inputs(input_path.parent, False)
    inputs = [path for path in siblings if split_axis(path)[1].casefold() == group_key]
    if input_path not in inputs:
        inputs.append(input_path)
    return sorted(inputs), group


def infer_duration_ms(actions):
    if not actions:
        return 0.0
    return max(at for at, _ in actions)


def build_speed_texture(actions, duration_ms, max_speed, speed_resolution):
    intensities = [0.0] * speed_resolution
    counts = [0] * speed_resolution

    if len(actions) < 2:
        return intensities

    if duration_ms <= 0:
        return intensities

    time_step = duration_ms / speed_resolution
    if time_step <= 0:
        return intensities

    for idx in range(1, len(actions)):
        prev_at, prev_pos = actions[idx - 1]
        next_at, next_pos = actions[idx]
        delta_ms = next_at - prev_at
        if delta_ms <= 0:
            continue

        speed = abs(next_pos - prev_pos) / (delta_ms / 1000.0)
        intensity = clamp(speed / max_speed, 0.0, 1.0)

        start_px = int(prev_at / time_step)
        end_px = int(next_at / time_step)

        if end_px < start_px:
            start_px, end_px = end_px, start_px

        if start_px == end_px:
            if 0 <= start_px < speed_resolution:
                intensities[start_px] += intensity
                counts[start_px] += 1
        elif 0 <= start_px < speed_resolution and 0 <= end_px < speed_resolution:
            for x in range(start_px, end_px):
                intensities[x] += intensity
                counts[x] += 1

    for x, count in enumerate(counts):
        if count > 0:
            intensities[x] /= count

    return intensities


def sample_linear(values, u):
    if not values:
        return 0.0
    x = clamp(u, 0.0, 1.0) * (len(values) - 1)
    left = int(math.floor(x))
    right = min(left + 1, len(values) - 1)
    t = x - left
    return lerp(values[left], values[right], t)


def render_heatmap(actions, duration_ms, width, height, max_speed, palette, speed_resolution):
    speeds = build_speed_texture(actions, duration_ms, max_speed, speed_resolution)
    colors = [color_for_intensity(sample_linear(speeds, x / max(width - 1, 1)), palette) for x in range(width)]
    rows = []
    for y in range(height):
        # OFS shader mixes from black to the heat color over Frag_UV.y.
        vertical = y / max(height - 1, 1)
        row = bytearray()
        for color in colors:
            row.extend(int(round(channel * vertical)) for channel in color)
        rows.append(bytes(row))
    return rows


def heatmap_image(actions, duration_ms, width, height, max_speed, palette, speed_resolution):
    rows = render_heatmap(actions, duration_ms, width, height, max_speed, palette, speed_resolution)
    return Image.frombytes("RGB", (width, height), b"".join(rows))


def speed_summary_band_image(actions, duration_ms, width, height, max_speed, palette, bucket_px=10):
    if len(actions) < 2 or duration_ms <= 0 or width <= 0 or height <= 0:
        return Image.new("RGBA", (width, height), (0, 0, 0, 0))

    bucket_count = max(1, math.ceil(width / bucket_px))
    intensities = [0.0] * bucket_count
    occupied = [False] * bucket_count
    bucket_ms = duration_ms / bucket_count

    for idx in range(1, len(actions)):
        prev_at, prev_pos = actions[idx - 1]
        next_at, next_pos = actions[idx]
        delta_ms = next_at - prev_at
        if delta_ms <= 0:
            continue

        speed = abs(next_pos - prev_pos) / (delta_ms / 1000.0)
        intensity = clamp(speed / max_speed, 0.0, 1.0)
        start = max(0, int(math.floor(prev_at / bucket_ms)))
        end = min(bucket_count - 1, int(math.floor(next_at / bucket_ms)))
        if end < start:
            start, end = end, start

        for bucket in range(start, end + 1):
            intensities[bucket] = max(intensities[bucket], intensity)
            occupied[bucket] = True

    low = Image.new("RGBA", (bucket_count, height), (0, 0, 0, 0))
    low_draw = ImageDraw.Draw(low)
    for bucket, intensity in enumerate(intensities):
        if not occupied[bucket]:
            continue
        color = color_for_intensity(intensity, palette)
        alpha = int(round(92 + 118 * math.sqrt(intensity)))
        low_draw.rectangle((bucket, 0, bucket, height), fill=(*color, alpha))

    return low.resize((width, height), Image.Resampling.BICUBIC)


def action_speeds(actions):
    speeds = []
    for idx in range(1, len(actions)):
        prev_at, prev_pos = actions[idx - 1]
        next_at, next_pos = actions[idx]
        delta_ms = next_at - prev_at
        if delta_ms > 0:
            speeds.append((abs(next_pos - prev_pos) / (delta_ms / 1000.0), delta_ms))
    return speeds


def js_round(value):
    return int(math.floor(value + 0.5))


def speed_stats(actions, max_speed_min_interval_ms=100):
    speeds = action_speeds(actions)
    values = [speed for speed, _delta_ms in speeds]
    max_values = [speed for speed, delta_ms in speeds if delta_ms >= max_speed_min_interval_ms]
    if not max_values:
        max_values = values
    max_speed = js_round(max(max_values)) if max_values else 0
    avg_speed = js_round(sum(values) / len(values)) if values else 0
    return max_speed, avg_speed


def font_candidates(bold=False):
    # The same CJK font travels with the renderer; no Windows fonts are needed.
    return [resource_path(BUNDLED_FONT)]


def load_font(size, bold=False):
    global _font_source

    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]

    for candidate in font_candidates(bold):
        try:
            font = ImageFont.truetype(str(candidate), size)
            _font_cache[key] = font
            if _font_source is None:
                _font_source = candidate
            return font
        except OSError:
            pass

    font = ImageFont.load_default()
    _font_cache[key] = font
    if _font_source is None:
        _font_source = Path("Pillow default bitmap font")
    return font


def log_font_source():
    global _font_source_logged
    if _font_source_logged:
        return
    load_font(12)
    source = safe_text(_font_source)
    if Path(str(_font_source)).name == Path(BUNDLED_FONT).name:
        log(f"Using bundled font: {source}")
    else:
        log(f"Using fallback font: {source}")
    _font_source_logged = True


def fit_text(draw, text, font, max_width):
    if draw.textlength(text, font=font) <= max_width:
        return text
    if max_width <= draw.textlength("...", font=font):
        return "..."
    out = text
    while out and draw.textlength(out + "...", font=font) > max_width:
        out = out[:-1]
    return out + "..."


def fit_font(draw, text, start_size, max_width, min_size=18, bold=False):
    size = start_size
    while size > min_size:
        font = load_font(size, bold=bold)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 2
    return load_font(min_size, bold=bold)


def draw_soft_text(image, xy, text, font, fill, shadow=(0, 0, 0), anchor=None, blur=1.0, offset=(1, 1), shadow_alpha=170):
    x, y = xy
    shadow_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    shadow_fill = (*shadow, shadow_alpha)
    shadow_draw.text((x + offset[0], y + offset[1]), text, font=font, fill=shadow_fill, anchor=anchor)
    if blur > 0:
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(blur))
    image.alpha_composite(shadow_layer)

    text_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)
    text_draw.text((x, y), text, font=font, fill=(*fill, 255), anchor=anchor)
    image.alpha_composite(text_layer)


def draw_glow_text(image, xy, text, font, fill=(18, 18, 18), glow=(255, 255, 255), anchor=None, blur=1.35, glow_alpha=210, stroke_width=0):
    x, y = xy
    glow_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer)
    glow_draw.text((x, y), text, font=font, fill=(*glow, glow_alpha), anchor=anchor)
    if blur > 0:
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(blur))
    image.alpha_composite(glow_layer)

    text_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)
    text_draw.text((x, y), text, font=font, fill=(*fill, 255), anchor=anchor, stroke_width=stroke_width, stroke_fill=(*glow, 230))
    image.alpha_composite(text_layer)


def draw_speed_band_frost(image, box, alpha=58, blur=3.0):
    frost = Image.new("RGBA", image.size, (0, 0, 0, 0))
    frost_draw = ImageDraw.Draw(frost)
    frost_draw.rectangle(box, fill=(255, 255, 255, alpha))
    if blur > 0:
        frost = frost.filter(ImageFilter.GaussianBlur(blur))
    image.alpha_composite(frost)


def draw_translucent_panel(image, box, radius=4, fill=(0, 0, 0, 115)):
    panel = Image.new("RGBA", image.size, (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle(box, radius=radius, fill=fill)
    image.alpha_composite(panel)


def format_duration(duration_ms):
    total = int(math.ceil(duration_ms / 1000.0))
    minutes = total // 60
    seconds = total % 60
    return f"{minutes}:{seconds:02d}"


def draw_stats(image, x, y, w, h, actions, duration_ms, is_first):
    max_speed, avg_speed = speed_stats(actions)
    stats = [("Actions", len(actions)), ("MaxSpeed", max_speed), ("AvgSpeed", avg_speed)]
    if is_first:
        stats.insert(0, ("Duration", format_duration(duration_ms)))

    font_label = load_font(16, bold=True)
    font_value = load_font(28, bold=True)
    cell_w = w // len(stats)
    for idx, (label, value) in enumerate(stats):
        cx = x + idx * cell_w + cell_w // 2
        draw_glow_text(image, (cx, y), label, font_label, fill=(18, 18, 18), glow=(255, 255, 255), anchor="mt", blur=1.35, glow_alpha=230, stroke_width=1)
        draw_glow_text(image, (cx, y + 16), str(value), font_value, fill=(12, 12, 12), glow=(255, 255, 255), anchor="mt", blur=1.6, glow_alpha=245, stroke_width=2)


def draw_action_curve(base_image, actions, duration_ms, plot_x, y, width, height, max_speed, palette, curve_color, scale=3):
    if len(actions) < 2 or duration_ms <= 0:
        return
    overlay = Image.new("RGBA", (base_image.size[0] * scale, base_image.size[1] * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    def point(action):
        at, pos = action
        x = plot_x + clamp(at / duration_ms, 0.0, 1.0) * width
        py = y + (1.0 - clamp(pos / 100.0, 0.0, 1.0)) * height
        return (round(x * scale), round(py * scale))

    for idx in range(1, len(actions)):
        prev = actions[idx - 1]
        current = actions[idx]
        delta_ms = current[0] - prev[0]
        if delta_ms <= 0:
            continue

        if curve_color == "speed":
            speed = abs(current[1] - prev[1]) / (delta_ms / 1000.0)
            rgb = color_from_stops(speed / max_speed, LINE_COLOR_STOPS, smooth=False)
            color = (rgb[0], rgb[1], rgb[2], 185)
        else:
            color = (0, 220, 230, 145)

        draw.line((point(prev), point(current)), fill=color, width=max(1, scale))

    overlay = overlay.resize(base_image.size, Image.Resampling.LANCZOS)
    base_image.alpha_composite(overlay)


def generate_axes_report(group_name, axis_paths, out_dir, args, single_axis_only=False):
    left_w = 92
    top = 8
    bottom = 58
    row_h = 104
    heat_h = 44
    gap = 0
    width = args.report_width
    plot_x = left_w
    plot_w = max(256, width - left_w - 38)
    # Keep the workbench's six-row report; extra rows appear only when supplied.
    standard_axes = {"L0", "L1", "L2", "R0", "R1", "R2"}
    axes_to_render = [axis for axis in AXES if axis[0] in standard_axes or axis[0] in axis_paths]
    if single_axis_only:
        axes_to_render = [axis for axis in AXES if axis[0] in axis_paths]
    height = top + bottom + row_h * len(axes_to_render) + gap * (len(axes_to_render) - 1)
    image = Image.new("RGBA", (width, height), (24, 24, 24, 255))
    draw = ImageDraw.Draw(image)

    font_footer = load_font(26, bold=True)

    durations = []
    loaded = {}
    for axis_name, path in axis_paths.items():
        actions, duration_ms = read_funscript(path)
        loaded[axis_name] = (path, actions, duration_ms)
        if duration_ms:
            durations.append(duration_ms)
    duration_ms = max(durations) if durations else 0

    title_path = axis_paths.get("L0") or next(iter(axis_paths.values()))
    for row_idx, (axis_name, axis_color, _tokens) in enumerate(axes_to_render):
        y = top + row_idx * (row_h + gap)
        draw.rectangle((0, y, width, y + row_h), fill=(20, 20, 20))
        draw.rectangle((left_w, y, width, y + heat_h), fill=(82, 82, 82))
        draw.rectangle((0, y, left_w, y + row_h), fill=axis_color)
        font_axis = fit_font(draw, axis_name, 56, left_w - 8)
        draw.text((left_w / 2, y + row_h / 2), axis_name, font=font_axis, fill=(0, 0, 0), anchor="mm")

        draw.line((plot_x, y + row_h - 1, plot_x + plot_w, y + row_h - 1), fill=(0, 150, 170), width=1)

        loaded_axis = loaded.get(axis_name)
        if loaded_axis:
            _path, actions, axis_duration = loaded_axis
            use_duration = duration_ms or axis_duration
            band = speed_summary_band_image(actions, use_duration, plot_w, heat_h, args.max_speed, args.palette)
            image.alpha_composite(band, (plot_x, y))
            draw_action_curve(
                image,
                actions,
                use_duration,
                plot_x,
                y + heat_h + 6,
                plot_w,
                row_h - heat_h - 18,
                args.max_speed,
                args.palette,
                args.curve_color,
            )
            include_duration = row_idx == 0
            stats_w = 400 if include_duration else 330
            draw_stats(image, plot_x + plot_w - stats_w - 4, y + 3, stats_w, heat_h, actions, use_duration, include_duration)
        else:
            draw.rectangle((plot_x, y, plot_x + plot_w, y + heat_h), fill=(85, 85, 85))

    footer_name = fit_text(draw, title_path.name, font_footer, width - 160)
    draw_soft_text(image, (44, height - 40), footer_name, font_footer, (236, 47, 160), shadow=(0, 0, 0), blur=1.1, offset=(1, 1), shadow_alpha=190)

    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"{group_name}_Axes.png"
    if output.exists() and not args.overwrite:
        return "skipped", f"{output.name} already exists"
    image.convert("RGB").save(output)
    return "ok", output.name


def safe_output_name(path, suffix):
    return f"{path.stem}{suffix}.png"


def find_inputs(input_path, recursive):
    if input_path.is_file():
        if input_path.suffix.lower() != ".funscript":
            raise ValueError(f"input file is not a .funscript: {input_path}")
        return [input_path]

    if not input_path.is_dir():
        raise ValueError(f"input path does not exist: {input_path}")

    pattern = "**/*.funscript" if recursive else "*.funscript"
    return sorted(input_path.glob(pattern))


def generate_one(path, out_dir, width, height, max_speed, palette, suffix, overwrite, speed_resolution):
    output = out_dir / safe_output_name(path, suffix)
    if output.exists() and not overwrite:
        return "skipped", f"{output.name} already exists"

    actions, duration_ms = read_funscript(path)
    if len(actions) < 2:
        return "failed", "not enough actions"

    rows = render_heatmap(actions, duration_ms, width, height, max_speed, palette, speed_resolution)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_png(output, width, height, rows)
    return "ok", output.name


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="heatmapgen",
        description="Generate PNG heatmaps from .funscript files.",
    )
    parser.add_argument("input_positional", nargs="?", help="Input .funscript file or folder.")
    parser.add_argument("-i", "--input", dest="input_option", help="Input .funscript file or folder.")
    parser.add_argument("-o", "--out", help="Output folder. Defaults to the input file/folder.")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH, help=f"Image width. Default: {DEFAULT_WIDTH}.")
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help=f"Image height. Default: {DEFAULT_HEIGHT}.")
    parser.add_argument("--max-speed", type=float, default=DEFAULT_MAX_SPEED, help=f"Speed mapped to max color. Default: {DEFAULT_MAX_SPEED}.")
    parser.add_argument("--palette", choices=sorted(PALETTES.keys()), default="ofs", help="Color palette.")
    parser.add_argument("--suffix", default="_Heatmap", help="Output filename suffix. Default: _Heatmap.")
    parser.add_argument("--recursive", action="store_true", help="Scan folders recursively.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output files.")
    parser.add_argument("--mode", choices=("heatmap", "axes"), default="heatmap", help="Output mode. Default: heatmap.")
    parser.add_argument("--report-width", type=int, default=DEFAULT_REPORT_WIDTH, help=f"Axes report width. Default: {DEFAULT_REPORT_WIDTH}.")
    parser.add_argument("--curve-color", choices=("speed", "cyan"), default="speed", help="Axes report curve color mode. Default: speed.")
    parser.add_argument("--speed-resolution", type=int, default=DEFAULT_SPEED_RESOLUTION, help=f"Internal speed sampling resolution. Default: {DEFAULT_SPEED_RESOLUTION}.")
    return parser.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    log(f"heatmapgen {VERSION}")
    input_value = args.input_option or args.input_positional
    if not input_value:
        print("error: provide an input file or folder", file=sys.stderr)
        return 2

    input_path = Path(input_value).expanduser().resolve()
    if args.width < 1 or args.height < 1:
        print("error: width and height must be positive", file=sys.stderr)
        return 2
    if args.max_speed <= 0:
        print("error: --max-speed must be positive", file=sys.stderr)
        return 2
    if args.speed_resolution < 32:
        print("error: --speed-resolution must be at least 32", file=sys.stderr)
        return 2
    if args.report_width < 640:
        print("error: --report-width must be at least 640", file=sys.stderr)
        return 2

    single_axis_only = False
    try:
        if args.mode == "axes" and input_path.is_file():
            inputs = find_inputs(input_path, False)
            single_axis_only = True
        else:
            inputs = find_inputs(input_path, args.recursive)
    except ValueError as exc:
        print(safe_text(f"error: {exc}"), file=sys.stderr)
        return 2

    if args.out:
        out_dir = Path(args.out).expanduser().resolve()
    elif input_path.is_file():
        out_dir = input_path.parent
    else:
        out_dir = input_path

    if not inputs:
        log("No .funscript files found.")
        return 1

    log_font_source()

    counts = {"ok": 0, "skipped": 0, "failed": 0}
    if args.mode == "axes":
        groups = group_funscripts(inputs)
        for group_name, axis_paths in sorted(groups.items()):
            try:
                status, detail = generate_axes_report(group_name, axis_paths, out_dir, args, single_axis_only=single_axis_only)
            except Exception as exc:
                status, detail = "failed", str(exc)
            counts[status] += 1
            log(f"[{status}] {group_name}: {detail}")

        log(
            f"Done. generated={counts['ok']} skipped={counts['skipped']} failed={counts['failed']} output={out_dir}"
        )
        return 0 if counts["failed"] == 0 else 1

    for path in inputs:
        try:
            status, detail = generate_one(
                path,
                out_dir,
                args.width,
                args.height,
                args.max_speed,
                args.palette,
                args.suffix,
                args.overwrite,
                args.speed_resolution,
            )
        except Exception as exc:
            status, detail = "failed", str(exc)

        counts[status] += 1
        log(f"[{status}] {path.name}: {detail}")

    log(
        f"Done. generated={counts['ok']} skipped={counts['skipped']} failed={counts['failed']} output={out_dir}"
    )
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

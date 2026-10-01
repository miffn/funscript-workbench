from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PureWindowsPath
import re

PROJECT_DIR = Path(__file__).resolve().parent.parent


def normalize_id(value: object) -> str | None:
    """Normalize an exact identifier, never silently discard a child identifier."""
    match = re.fullmatch(r"S(\d+)(?:_(\d+))?", str(value).strip(), re.I)
    if not match:
        return None
    result = f"S{int(match[1]):03d}"
    return result + (f"_{int(match[2]):03d}" if match[2] is not None else "")


def parse_folder(name: str) -> tuple[str, str] | None:
    match = re.match(r"S\d+(?:_\d+)?", name, re.I)
    if not match:
        return None
    # A numeric child followed by letters is malformed, not a parent title.
    remainder = name[match.end():]
    if remainder and remainder[0] not in "_ -":
        return None
    script_id = normalize_id(match[0])
    return (script_id, remainder.lstrip("_ -") or script_id) if script_id else None


@dataclass(frozen=True)
class Root:
    path: Path
    windows_path: str
    label: str

    def windows_directory(self, directory: Path) -> str:
        relative = directory.resolve().relative_to(self.path.resolve())
        return str(PureWindowsPath(self.windows_path).joinpath(*relative.parts))


@dataclass(frozen=True)
class Config:
    data_dir: Path
    roots: tuple[Root, ...]
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    host_key_file: Path | None = None
    frontend_dist: Path = PROJECT_DIR / "frontend" / "dist"
    open_mode: str = "native"
    preview_output_root: Path = Path("/mnt/d/Media/workspace/预览")
    preview_renderer: Path = PROJECT_DIR / "preview_generator" / "build" / "ofs-preview-renderer"
    heatmap_tool: Path = PROJECT_DIR / 'backend' / 'tools' / 'heatmapcreatorv1.0.exe'

    @classmethod
    def from_environment(cls) -> "Config":
        data_dir = Path(os.environ.get("WORKBENCH_DATA_DIR", str(PROJECT_DIR / "data")))
        definitions = json.loads(os.environ.get("WORKBENCH_ROOTS_JSON", json.dumps([
            {"path": "/mnt/d/Media/2026", "windows_path": "D:\\Media\\2026", "label": "2026"},
            {"path": "/mnt/d/Media/workspace", "windows_path": "D:\\Media\\workspace", "label": "workspace"},
        ])))
        roots = []
        for definition in definitions:
            if isinstance(definition, str):
                path = Path(definition)
                windows_path = str(path)
                mounted = re.fullmatch(r"/mnt/([a-z])/(.+)", str(path), re.I)
                if mounted:
                    windows_path = mounted[1].upper() + ":\\" + mounted[2].replace("/", "\\")
                roots.append(Root(path, windows_path, path.name))
            else:
                path = Path(definition["path"])
                roots.append(Root(path, definition.get("windows_path", str(path)), definition.get("label", path.name)))
        return cls(data_dir=data_dir, roots=tuple(roots),
                   ffmpeg=os.environ.get("WORKBENCH_FFMPEG", "ffmpeg"),
                   ffprobe=os.environ.get("WORKBENCH_FFPROBE", "ffprobe"),
                   open_mode=os.environ.get("WORKBENCH_OPEN_MODE", "native"),
                   preview_output_root=Path(os.environ.get("WORKBENCH_PREVIEW_OUTPUT_ROOT", "/mnt/d/Media/workspace/预览")),
                   preview_renderer=Path(os.environ.get("WORKBENCH_PREVIEW_RENDERER", str(PROJECT_DIR / "preview_generator" / "build" / "ofs-preview-renderer"))),
                   heatmap_tool=Path(os.environ.get('WORKBENCH_HEATMAP_TOOL', str(PROJECT_DIR / 'backend' / 'tools' / 'heatmapcreatorv1.0.exe'))),
                   host_key_file=Path(os.environ.get("WORKBENCH_HOST_KEY_FILE", str(data_dir / "host.key"))))

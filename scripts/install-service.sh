#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/script-workbench.service" <<EOF
[Unit]
Description=Funscript inventory workbench
After=network.target

[Service]
Type=simple
WorkingDirectory=$project_dir
ExecStart=$project_dir/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8789
Environment=PYTHONUNBUFFERED=1
Environment=WORKBENCH_OPEN_MODE=gateway
Restart=on-failure
RestartSec=5
TimeoutStopSec=15

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now script-workbench.service
systemctl --user is-active script-workbench.service

"""Explicit offline import of an ES template and recorded preview uploads.

Input paths are supplied by the caller. Private uploads and links stay in SQLite;
this module neither contacts a publishing site nor changes publication records.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Config
from .es_posts import DEFAULT_BODY, ESPosts, TemplateEdit, import_preview_history
from .previews import PreviewService
from .store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="Import local ES template and preview history into SQLite")
    parser.add_argument('--data-dir', type=Path, help='Existing workbench database directory')
    parser.add_argument('--history', type=Path, help='Skill history JSON containing a previews mapping')
    parser.add_argument('--template', type=Path, help='Skill template config JSON, or workbench template export')
    parser.add_argument('--replace-template', action='store_true', help='Explicitly replace an existing edited template')
    args = parser.parse_args()
    if not args.history and not args.template:
        parser.error('Specify --history and/or --template')
    config = Config.from_environment()
    store = Store(args.data_dir or config.data_dir)
    service = ESPosts(store, config, PreviewService(store, config))
    summary = {}
    if args.history:
        history = json.loads(args.history.read_text(encoding='utf-8-sig'))
        result = import_preview_history(store, history)
        summary['history'] = result
    if args.template:
        current = service.template()
        if current['revision'] > 1 and not args.replace_template:
            summary['template'] = 'Existing edited template retained; use --replace-template to replace it'
        else:
            source = json.loads(args.template.read_text(encoding='utf-8-sig'))
            exported = isinstance(source.get('config'), dict)
            imported = source['config'] if exported else source
            # Keep local art references portable; ES image addresses remain as supplied.
            for button in imported.get('promoButtons', []):
                if button.get('localAsset'):
                    filename = button['localAsset'].replace('\\', '/').rsplit('/', 1)[-1]
                    candidate = config.frontend_dist.parent / 'public' / 'es-theme' / filename
                    button['localAsset'] = '/es-theme/' + filename if candidate.is_file() else ''
            imported.setdefault('recentPinnedIds', ['S046'])
            saved = service.save_template(TemplateEdit(
                name=source.get('name', 'ES · Liora Garden') if exported else 'ES · Liora Garden',
                body=source.get('body', DEFAULT_BODY) if exported else DEFAULT_BODY,
                config=imported, expected_revision=current['revision']))
            summary['template'] = {'name': saved['name'], 'revision': saved['revision']}
    # Print counts and identifiers only, never upload URLs or personal public links.
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()

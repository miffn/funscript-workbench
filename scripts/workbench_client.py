"""Codex bridge to the existing workbench API; never contacts a publishing site."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ClientError(ValueError):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def normalize_id(value: str) -> str:
    match = re.fullmatch(r'S(\d+)(?:_(\d+))?', value.strip(), re.I)
    if not match:
        raise ClientError('请使用完整编号，例如 S064 或 S025_001')
    return f'S{int(match[1]):03d}' + (f'_{int(match[2]):03d}' if match[2] else '')


class WorkbenchClient:
    def __init__(self, base_url: str):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {'', '/'}):
            raise ClientError('工作台地址须为 HTTP(S) 服务地址，不含路径、查询或凭据')
        self.base_url = base_url.rstrip('/')
        self.opener = build_opener(NoRedirects())

    def request(self, path: str, method='GET', payload=None):
        if not path.startswith('/api/'):
            raise ClientError('只允许调用工作台 API')
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        req = Request(self.base_url + path, data=data, method=method,
                      headers={'Accept': 'application/json', 'Content-Type': 'application/json'})
        try:
            with self.opener.open(req, timeout=120) as response:
                return json.load(response)
        except HTTPError as error:
            try:
                detail = json.load(error).get('detail', f'HTTP {error.code}')
            except (ValueError, OSError):
                detail = f'HTTP {error.code}'
            raise ClientError(f'工作台拒绝操作：{detail}') from error
        except (URLError, TimeoutError, OSError) as error:
            message = '无法连接工作台，请检查服务和配置地址'
            if method != 'GET':
                message += '；写入结果尚未确认，请先 inspect 读回，勿直接重复写入'
            raise ClientError(message) from error
        except ValueError as error:
            raise ClientError('工作台返回的内容不是有效 JSON，请检查服务地址') from error

    def work(self, script_id: str):
        script_id = normalize_id(script_id)
        page = 1
        while True:
            result = self.request('/api/works?' + urlencode({'q': script_id, 'page_size': 100, 'page': page}))
            match = next((item for item in result['items'] if item['script_id'] == script_id), None)
            if match:
                return self.request(f'/api/works/{match["id"]}')
            if page * result['page_size'] >= result['total']:
                raise ClientError(f'工作台未找到 {script_id}，请先在网页手动扫描库存')
            page += 1

    def inspect(self, script_id: str):
        work = self.work(script_id)
        post = self.request(f'/api/works/{work["id"]}/es-post')
        # Internal notes/import metadata are not publishing inputs.
        fields = ('id', 'script_id', 'title', 'tags', 'tags_revision', 'links', 'links_revision',
                  'es_published', 'patreon_published', 'es_published_date', 'patreon_published_date',
                  'duration_minutes', 'duration_status', 'directories', 'issues')
        return {'work': {key: work[key] for key in fields}, 'post': post,
                'template': self.request('/api/es-template'),
                'detail_url': self.base_url + '/#/inventory'}

    def save_inputs(self, work_id: int, state: dict, changes: dict):
        if not isinstance(changes, dict):
            raise ClientError('贴文输入必须是 JSON 对象')
        return self.request(f'/api/works/{work_id}/es-post', 'PUT',
                            {'inputs': {**state['inputs'], **changes}, 'expected_revision': state['revision']})

    def post_operation(self, script_id: str, operation: str, changes=None):
        work = self.work(script_id)
        work_id = work['id']
        state = self.request(f'/api/works/{work_id}/es-post')
        if changes is not None:
            state = self.save_inputs(work_id, state, changes)
        if operation == 'save-inputs':
            return state
        suffix = 'generate' if operation == 'generate' else 'cover'
        return self.request(f'/api/works/{work_id}/es-post/{suffix}', 'POST',
                            {'expected_revision': state['revision']})

    def record_es_link(self, script_id: str, link: str):
        if not isinstance(link, str) or not link.strip():
            raise ClientError('请提供已实际发布的 ES 帖子链接')
        work = self.work(script_id)
        current = self.request(f'/api/works/{work["id"]}/links')
        return self.request(f'/api/works/{work["id"]}/links', 'PATCH',
                            {'links': {'es': link}, 'expected_revision': current['links_revision']})


def export_post(state: dict, output_path: Path):
    output = state.get('output')
    if not output:
        raise ClientError('当前作品没有生成稿，请先 generate')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output['body'] + '\n', encoding='utf-8')
    return {key: output[key] for key in ('title', 'status', 'missing', 'warnings', 'stale')} | {
        'post_path': str(output_path.resolve()), 'revision': state['revision']}


def main(argv=None):
    parser = argparse.ArgumentParser(description='读取工作台资料并生成本地 ES 贴文；发布由用户手动完成')
    parser.add_argument('--base-url', default=os.environ.get('WORKBENCH_API_URL', 'http://127.0.0.1:8789'))
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('health')
    commands.add_parser('template')
    for name in ('inspect', 'save-inputs', 'generate', 'save-cover', 'export', 'record-es-link'):
        command = commands.add_parser(name)
        command.add_argument('--script-id', required=True)
        if name in {'save-inputs', 'generate', 'save-cover'}:
            command.add_argument('--inputs-json', type=Path, required=name == 'save-inputs',
                                 help='仅需提供修改字段，未提供的已保存字段保留')
        if name in {'generate', 'export'}:
            command.add_argument('--output', type=Path, help='正文 Markdown 导出路径，标题单独返回')
        if name == 'record-es-link':
            command.add_argument('--es-url', required=True)
    args = parser.parse_args(argv)
    try:
        client = WorkbenchClient(args.base_url)
        changes = json.loads(args.inputs_json.read_text(encoding='utf-8-sig')) if getattr(args, 'inputs_json', None) else None
        if args.command == 'health':
            result = client.request('/api/health')
        elif args.command == 'template':
            result = client.request('/api/es-template')
        elif args.command == 'inspect':
            result = client.inspect(args.script_id)
        elif args.command == 'record-es-link':
            result = client.record_es_link(args.script_id, args.es_url)
        elif args.command == 'export':
            work = client.work(args.script_id)
            result = client.request(f'/api/works/{work["id"]}/es-post')
        else:
            result = client.post_operation(args.script_id, args.command, changes)
        if args.command in {'export', 'generate'}:
            path = args.output or Path('data') / 'generated-posts' / f'{normalize_id(args.script_id)}-es-post.md'
            result = export_post(result, path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ClientError, OSError, ValueError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

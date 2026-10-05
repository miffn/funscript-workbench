"""One revocable MCP bearer token; only its digest is persisted."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .store import now


class TokenReset(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: StrictInt = Field(ge=0)


class MCPAuth:
    def __init__(self, store):
        self.store = store

    def state(self, db):
        row = db.execute("SELECT value FROM settings WHERE key='mcp_auth'").fetchone()
        try:
            state = json.loads(row['value']) if row else {}
            if (not isinstance(state, dict) or not re.fullmatch(r'[a-f0-9]{64}', state.get('digest', ''))
                    or type(state.get('revision')) is not int or state['revision'] < 1):
                return {}
            return state
        except (ValueError, TypeError):
            return {}

    def accepts(self, authorization):
        match = re.fullmatch(r'Bearer ([A-Za-z0-9_-]{43})', authorization, re.IGNORECASE)
        if not match:
            return False
        with self.store.connection() as db:
            state = self.state(db)
        digest = hashlib.sha256(match[1].encode('ascii')).hexdigest()
        return bool(state) and hmac.compare_digest(digest, state['digest'])

    def public_state(self, state, can_manage):
        return {'enabled': bool(state), 'can_manage': can_manage,
                'revision': state.get('revision', 0), 'updated_at': state.get('updated_at')}

    def reset(self, expected_revision):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = self.state(db)
            if previous.get('revision', 0) != expected_revision:
                raise HTTPException(409, 'MCP Token 已变更，请刷新后重试')
            token = secrets.token_urlsafe(32)
            state = {'digest': hashlib.sha256(token.encode('ascii')).hexdigest(),
                     'revision': expected_revision + 1, 'updated_at': now()}
            db.execute("INSERT INTO settings(key,value) VALUES('mcp_auth',?) "
                       'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (json.dumps(state),))
        return {**self.public_state(state, True), 'token': token}


def register_mcp_auth_routes(app, auth, host_capability):
    @app.get('/api/mcp-auth')
    def auth_state(request: Request):
        with auth.store.connection() as db:
            state = auth.state(db)
        return JSONResponse(auth.public_state(state, host_capability(request)),
                            headers={'Cache-Control': 'no-store'})

    @app.post('/api/mcp-auth/token')
    def reset_token(edit: TokenReset, request: Request):
        host = request.headers.get('host', '').lower()
        if not host_capability(request) or request.headers.get('origin') != f'http://{host}':
            raise HTTPException(403, 'MCP Token 仅允许素材所在 Windows 主机的同源网页管理')
        return JSONResponse(auth.reset(edit.expected_revision),
                            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

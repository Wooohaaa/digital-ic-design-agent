"""Opt-in, short-lived loopback normalization for Anthropic-compatible gateways."""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def normalize_messages(payload: dict) -> dict:
    """Keep system instructions at system priority using the standard top-level field."""
    result=dict(payload)
    def text(value):
        if isinstance(value,str):
            return value
        if not isinstance(value,list) or not all(isinstance(block,dict) and block.get('type')=='text' and isinstance(block.get('text'),str) for block in value):
            raise ValueError('Gateway compatibility supports text system blocks only.')
        return '\n\n'.join(block['text'] for block in value)
    parts=[text(result['system'])] if 'system' in result else []
    messages=result.get('messages',[])
    if not isinstance(messages,list) or any(not isinstance(m,dict) for m in messages):
        raise ValueError('Messages must be an array of objects.')
    for message in messages:
        if message.get('role')=='system':
            parts.append(text(message.get('content',[])))
    if parts:
        result['system']='\n\n'.join(parts)
    if 'messages' in result:
        result['messages']=[m for m in messages if m.get('role')!='system']
    return result


class ClaudeGateway:
    def __init__(self,base_url: str,key: str,env: dict):
        parsed=urlsplit(base_url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Claude gateway upstream must be an HTTPS base URL without credentials/query.')
        self.base_url=base_url.rstrip('/').removesuffix('/v1')
        self.key=key
        self.env=env
        self.token=secrets.token_urlsafe(32)
        self.server=None
        self.thread=None

    @classmethod
    def configured(cls,env: dict):
        path=Path.home()/'.icforge/claude-gateway.json'
        if not path.is_file():
            return None
        config=json.loads(path.read_text(encoding='utf-8'))
        if config.get('system_string') is not True:
            return None
        base=env.get('ANTHROPIC_BASE_URL') or config.get('base_url','')
        key=env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY') or config.get('api_key','')
        if not key:
            raise ValueError('Claude gateway credential is missing.')
        return cls(base,key,env)

    def start(self) -> dict:
        owner=self
        proxy={scheme:self.env.get(scheme.upper()+'_PROXY','') for scheme in ('http','https')}
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({k:v for k,v in proxy.items() if v}))
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass

            def do_POST(self):
                if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+owner.token):
                    self.send_error(401);return
                if urlsplit(self.path).path not in {'/v1/messages','/v1/messages/count_tokens'}:
                    self.send_error(404);return
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=8_000_000:
                        self.send_error(413);return
                    payload=json.loads(self.rfile.read(size))
                    if not isinstance(payload,dict):
                        raise ValueError('Messages body must be an object.')
                    body=json.dumps(normalize_messages(payload),ensure_ascii=False).encode('utf-8')
                    headers={k:v for k,v in self.headers.items() if k.lower() not in {
                        'host','content-length','authorization','x-api-key','connection','transfer-encoding','accept-encoding'}}
                    headers.update(Authorization='Bearer '+owner.key,**{'Content-Type':'application/json','Accept-Encoding':'identity'})
                    request=urllib.request.Request(owner.base_url+self.path,data=body,headers=headers)
                    try:
                        response=opener.open(request,timeout=120)
                    except urllib.error.HTTPError as exc:
                        response=exc
                    with response:
                        self.send_response(response.code)
                        self.send_header('Content-Type',response.headers.get('Content-Type','application/json'))
                        self.send_header('Connection','close')
                        self.end_headers()
                        while chunk:=response.read1(8192):
                            self.wfile.write(chunk);self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError):
                    pass
                except (ValueError,OSError,urllib.error.URLError):
                    # Never include upstream credentials, request bodies or URLs in errors.
                    self.send_error(502,'Gateway request failed')
                finally:
                    self.close_connection=True
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        return {'ANTHROPIC_BASE_URL':f'http://127.0.0.1:{self.server.server_port}',
                'ANTHROPIC_AUTH_TOKEN':self.token,'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1'}

    def close(self):
        if self.server:
            self.server.shutdown();self.server.server_close()
            self.thread.join(timeout=2)
            self.server=None

"""共有 httpx.AsyncClient シングルトン。接続プーリングを効率的に再利用する。"""

import httpx

# モジュールレベルのシングルトン。アプリケーションのライフサイクルと一致する。
# httpx.AsyncClient は内部で接続プールを管理し、HTTP/2 も対応する。
_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """共有 httpx.AsyncClient を取得する。初回呼び出し時にインスタンス化。"""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def close_http_client() -> None:
    """アプリケーション終了時にクライアントを閉じる。"""
    global _client
    if _client:
        await _client.aclose()
        _client = None

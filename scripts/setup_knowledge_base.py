"""Foundry IQ Knowledge Base セットアップスクリプト。

Azure AI Search にレギュレーション文書のインデックスを作成し、
regulations/ ディレクトリの Markdown ファイルをアップロードする。

使い方:
    uv run python scripts/setup_knowledge_base.py

必要な環境変数:
    AZURE_AI_PROJECT_ENDPOINT: Foundry プロジェクトの endpoint
    （または SEARCH_ENDPOINT + SEARCH_API_KEY で直接指定）
"""

import json
import os
import sys
import urllib.request

# インデックス名（regulation_check.py と一致させること）
INDEX_NAME = "regulations-index"

# regulations/ ディレクトリのパス
REGULATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "regulations")


def get_search_connection() -> tuple[str, str]:
    """Azure AI Search 接続情報を取得する。

    優先順位:
    1. SEARCH_ENDPOINT + SEARCH_API_KEY 環境変数
    2. Foundry プロジェクトから AIProjectClient 経由で取得
    """
    # 直接指定
    endpoint = os.environ.get("SEARCH_ENDPOINT", "")
    api_key = os.environ.get("SEARCH_API_KEY", "")
    if endpoint and api_key:
        return endpoint.rstrip("/"), api_key

    # Foundry プロジェクト経由
    project_endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
    if not project_endpoint:
        print("❌ SEARCH_ENDPOINT+SEARCH_API_KEY または AZURE_AI_PROJECT_ENDPOINT を設定してください")
        sys.exit(1)

    try:
        from azure.ai.projects import AIProjectClient
        from azure.ai.projects.models import ConnectionType
        from azure.identity import DefaultAzureCredential

        project = AIProjectClient(
            endpoint=project_endpoint,
            credential=DefaultAzureCredential(),
        )
        conn = project.connections.get_default(
            connection_type=ConnectionType.AZURE_AI_SEARCH,
            include_credentials=True,
        )
        if conn is None:
            print("❌ Azure AI Search 接続が見つかりません。")
            print("   Foundry ポータルで Azure AI Search 接続を追加するか、")
            print("   SEARCH_ENDPOINT と SEARCH_API_KEY 環境変数を設定してください。")
            sys.exit(1)

        return conn.endpoint_url.rstrip("/"), conn.properties.credentials.key
    except Exception as e:
        print(f"❌ Foundry プロジェクトからの接続取得に失敗: {e}")
        print("   SEARCH_ENDPOINT と SEARCH_API_KEY 環境変数を設定してください。")
        sys.exit(1)


def create_index(search_endpoint: str, api_key: str) -> None:
    """Azure AI Search にインデックスを作成する"""
    index_def = {
        "name": INDEX_NAME,
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
            {"name": "title", "type": "Edm.String", "searchable": True, "filterable": True},
            {"name": "content", "type": "Edm.String", "searchable": True},
            {"name": "category", "type": "Edm.String", "filterable": True, "facetable": True},
            {"name": "source_file", "type": "Edm.String", "filterable": True},
        ],
    }

    url = f"{search_endpoint}/indexes/{INDEX_NAME}?api-version=2024-07-01"
    body = json.dumps(index_def).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "api-key": api_key,
        },
        method="PUT",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"✅ インデックス '{INDEX_NAME}' を作成/更新しました (HTTP {resp.status})")
    except urllib.error.HTTPError as e:
        if e.code == 204:
            print(f"✅ インデックス '{INDEX_NAME}' は既に存在します（更新済み）")
        else:
            print(f"❌ インデックス作成に失敗: {e.code} {e.reason}")
            sys.exit(1)


def load_regulations() -> list[dict]:
    """regulations/ ディレクトリから Markdown ファイルを読み込む"""
    docs = []
    abs_dir = os.path.abspath(REGULATIONS_DIR)

    if not os.path.exists(abs_dir):
        print(f"❌ regulations/ ディレクトリが見つかりません: {abs_dir}")
        sys.exit(1)

    for filename in os.listdir(abs_dir):
        if not filename.endswith(".md"):
            continue
        filepath = os.path.join(abs_dir, filename)
        with open(filepath, encoding="utf-8") as f:
            content = f.read()

        # ファイル名からカテゴリを推定
        category_map = {
            "travel_industry_law.md": "旅行業法",
            "advertising_guidelines.md": "景品表示法・広告規制",
            "brand_guidelines.md": "ブランドガイドライン",
        }
        category = category_map.get(filename, "その他")

        # 長いドキュメントはセクションごとにチャンク分割
        sections = content.split("\n## ")
        for i, section in enumerate(sections):
            doc_id = f"{filename.replace('.md', '')}-{i}"
            title = section.split("\n")[0].strip("# ").strip() if section.strip() else filename
            docs.append(
                {
                    "id": doc_id,
                    "title": title,
                    "content": section.strip()[:8000],
                    "category": category,
                    "source_file": filename,
                }
            )

    return docs


def upload_documents(search_endpoint: str, api_key: str, docs: list[dict]) -> None:
    """ドキュメントをインデックスにアップロードする"""
    url = f"{search_endpoint}/indexes/{INDEX_NAME}/docs/index?api-version=2024-07-01"
    payload = {
        "value": [{"@search.action": "mergeOrUpload", **doc} for doc in docs],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "api-key": api_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            success = sum(1 for v in result.get("value", []) if v.get("status"))
            print(f"✅ {success}/{len(docs)} ドキュメントをアップロードしました")
    except urllib.error.HTTPError as e:
        print(f"❌ ドキュメントアップロードに失敗: {e.code} {e.reason}")
        print(e.read().decode())
        sys.exit(1)


def main():
    # Azure AI Search 接続情報を取得
    search_endpoint, api_key = get_search_connection()
    print(f"🔍 Search endpoint: {search_endpoint}")

    # インデックス作成
    create_index(search_endpoint, api_key)

    # ドキュメント読み込み・アップロード
    docs = load_regulations()
    print(f"📄 {len(docs)} チャンクを読み込みました")
    upload_documents(search_endpoint, api_key, docs)

    print("\n✅ Foundry IQ Knowledge Base セットアップ完了！")
    print(f"   Agent3 の search_knowledge_base ツールでインデックス '{INDEX_NAME}' を検索できます。")


if __name__ == "__main__":
    main()

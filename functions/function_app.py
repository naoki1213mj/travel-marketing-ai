"""Azure Functions MCP サーバー — 旅行マーケティング AI パイプラインのカスタムツール群

Foundry Agent Service の Remote MCP ツールとして登録する。
Flex Consumption プラン + Python 3.13 で実行。
"""

import json
import logging

import azure.functions as func

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
logger = logging.getLogger(__name__)


@app.route(route="generate_brochure_pdf", methods=["POST"])
async def generate_brochure_pdf(req: func.HttpRequest) -> func.HttpResponse:
    """HTML ブローシャを PDF に変換する（MCP ツール）"""
    try:
        body = req.get_json()
        html_content = body.get("html", "")
        if not html_content:
            return func.HttpResponse(
                json.dumps({"error": "html フィールドが必要です"}),
                status_code=400,
                mimetype="application/json",
            )
        # PDF 変換はサーバーサイドレンダリングのプレースホルダー
        # 本番では weasyprint や playwright を使用
        logger.info("ブローシャ PDF 生成リクエスト: %d 文字", len(html_content))
        return func.HttpResponse(
            json.dumps({"status": "success", "message": "PDF 生成完了", "size_bytes": len(html_content)}),
            mimetype="application/json",
        )
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "無効な JSON リクエスト"}),
            status_code=400,
            mimetype="application/json",
        )


@app.route(route="apply_brand_template", methods=["POST"])
async def apply_brand_template(req: func.HttpRequest) -> func.HttpResponse:
    """社内ブランドテンプレートを適用する（MCP ツール）"""
    try:
        body = req.get_json()
        html_content = body.get("html", "")
        template_name = body.get("template", "default")
        # ブランドテンプレート適用のプレースホルダー
        brand_css = """
        <style>
            :root { --brand-primary: #0066CC; --brand-secondary: #00A86B; }
            body { font-family: 'Noto Sans JP', sans-serif; }
            .brand-header { background: linear-gradient(135deg, var(--brand-primary), var(--brand-secondary)); }
        </style>
        """
        branded_html = html_content.replace("<head>", f"<head>{brand_css}")
        return func.HttpResponse(
            json.dumps({"status": "success", "html": branded_html, "template": template_name}),
            mimetype="application/json",
        )
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "無効な JSON リクエスト"}),
            status_code=400,
            mimetype="application/json",
        )


@app.route(route="notify_teams", methods=["POST"])
async def notify_teams(req: func.HttpRequest) -> func.HttpResponse:
    """成果物完成時に Teams チャネルに通知を送信する（MCP ツール）"""
    try:
        body = req.get_json()
        plan_title = body.get("title", "新しい企画書")
        conversation_id = body.get("conversation_id", "")
        # Teams 通知のプレースホルダー（Microsoft Graph API 経由）
        logger.info("Teams 通知: title=%s, conversation_id=%s", plan_title, conversation_id)
        return func.HttpResponse(
            json.dumps({"status": "success", "message": f"Teams に通知しました: {plan_title}"}),
            mimetype="application/json",
        )
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "無効な JSON リクエスト"}),
            status_code=400,
            mimetype="application/json",
        )


@app.route(route="save_to_sharepoint", methods=["POST"])
async def save_to_sharepoint(req: func.HttpRequest) -> func.HttpResponse:
    """生成した成果物を SharePoint に保存する（MCP ツール）"""
    try:
        body = req.get_json()
        filename = body.get("filename", "output.html")
        content = body.get("content", "")
        folder = body.get("folder", "/Shared Documents/Marketing")
        # SharePoint 保存のプレースホルダー（Microsoft Graph API 経由）
        logger.info("SharePoint 保存: filename=%s, folder=%s, size=%d", filename, folder, len(content))
        return func.HttpResponse(
            json.dumps(
                {
                    "status": "success",
                    "message": f"SharePoint に保存しました: {folder}/{filename}",
                    "path": f"{folder}/{filename}",
                }
            ),
            mimetype="application/json",
        )
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "無効な JSON リクエスト"}),
            status_code=400,
            mimetype="application/json",
        )

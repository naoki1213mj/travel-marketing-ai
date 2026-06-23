"""Work IQ デモデータを M365 (MOD Administrator) にシードする委任 (device-code) スクリプト。

このスクリプトは 4 つのデモシナリオ（沖縄ファミリー / 北海道カップル / 京都シニア / ハワイ学生）に
対応する「メール・Teams 投稿・OneDrive 文書」を、サインインしたユーザー本人の M365 に作成する。
Work IQ (WorkIQCopilot / M365 Copilot OBO) は「サインイン中ユーザー自身の M365」を検索するため、
ここで投入したデータがデモ中の Work IQ retrieval に乗り、社内ナレッジ活用の価値を可視化できる。

────────────────────────────────────────────────────────────────────────
前提条件（実行前に一度だけ）
────────────────────────────────────────────────────────────────────────
1. Entra ID に **public client** アプリ登録を作成（または既存を流用）し、次の **Delegated** 権限を付与＋
   テナント管理者同意:
     - Mail.Send           （メール作成）
     - ChannelMessage.Send （Teams チャネル投稿）
     - Files.ReadWrite     （OneDrive 文書アップロード）
     - User.Read / Team.ReadBasic.All / Channel.ReadBasic.All（自分/チーム/チャネル解決用）
   public client の「パブリック クライアント フロー」を有効化（device code を使うため）。
2. 実行ユーザー = MOD Administrator (admin@M365CPI20751765.onmicrosoft.com)。**M365 Copilot ライセンス必須**。
3. Teams 投稿先は既定で「自分が参加する最初のチーム / General チャネル」を自動検出。
   明示するなら --team-id / --channel-id を指定。Teams をスキップするなら --skip-teams。

────────────────────────────────────────────────────────────────────────
使い方
────────────────────────────────────────────────────────────────────────
  # まず内容だけ確認（M365 には何も作らない）
  uv run python scripts/seed_workiq_demo.py --client-id <APP_ID> --tenant-id <TENANT_ID> --dry-run

  # 実投入（device code が表示される → ブラウザで MOD Admin サインイン）
  uv run python scripts/seed_workiq_demo.py --client-id <APP_ID> --tenant-id <TENANT_ID>

  # 一部シナリオだけ
  uv run python scripts/seed_workiq_demo.py ... --scenarios okinawa hokkaido

注意:
  - 実データを作成する。**再実行すると重複作成**される（冪等ではない）ので注意。
  - 作成後、M365 Copilot/Substrate のインデックス反映に時間がかかる（数分〜最大 1 日）。
    デモ前日までに投入し、Copilot で retrieval されるか事前検証すること。
  - 会議メモは Graph で会議体を作るのが難しいため OneDrive のテキスト文書として代替している。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

import httpx
from azure.identity import DeviceCodeCredential

_GRAPH = "https://graph.microsoft.com/v1.0"
_GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]


@dataclass
class ScenarioContent:
    """1 シナリオぶんの投入コンテンツ。"""

    key: str
    title: str
    emails: list[dict[str, str]] = field(default_factory=list)  # {subject, body}
    teams_messages: list[str] = field(default_factory=list)
    documents: list[dict[str, str]] = field(default_factory=list)  # {name, content}


# 各シナリオに「覚えやすい固有事実」を仕込む（生成プランへ表出させ Work IQ の価値を可視化）。
SCENARIOS: dict[str, ScenarioContent] = {
    "okinawa": ScenarioContent(
        key="okinawa",
        title="沖縄ファミリー",
        emails=[
            {
                "subject": "【振り返り】2024春 沖縄ファミリーキャンペーン 実績共有",
                "body": (
                    "企画チームより共有です。\n"
                    "・早期予約割引（45日前）で予約数が前年比 +30%。次回も継続推奨。\n"
                    "・『子供1名無料』施策がファミリー層に好評。\n"
                    "・美ら海水族館チケット込みプランが決め手。問い合わせの約4割が水族館目当て。\n"
                    "・主反応は3〜4月の30〜40代ファミリー。Instagram 広告のCTRが他チャネルの2倍。"
                ),
            },
            {
                "subject": "ファミリー向け企画 価格・承認ルール（2026年度）",
                "body": (
                    "・価格上限: 1人あたり 8万円（超過は部長承認）。\n"
                    "・利益率 20% 以上を必須。\n"
                    "・訴求トーンは安心・安全・家族の思い出。誇大表現（最安値/業界No.1）は禁止。\n"
                    "・旅行業登録番号をフッターに必ず記載。"
                ),
            },
        ],
        teams_messages=[
            "沖縄ファミリーは美ら海水族館を前面に出すと反応が良いです。前回も問い合わせの決め手でした。",
            "レンタカー込み・子連れ向け（チャイルドシート無料）が人気。移動の不安を消すのが効きます。",
            "SNS は Instagram 中心。ビーチ×子供の動画クリエイティブが伸びます。",
        ],
        documents=[
            {
                "name": "ブランドガイドライン_ファミリー.txt",
                "content": "トーン: 安心 / 安全 / 思い出。配色: 明るいブルー＆サンゴ。NG表現: 最安値, 業界No.1, 絶対安全。",
            },
            {
                "name": "会議メモ_沖縄ファミリーキックオフ.txt",
                "content": (
                    "次期 沖縄ファミリープラン キックオフ MTG メモ\n"
                    "ターゲット: 30〜40代の子連れファミリー。予算上限 1人8万円・利益率20%以上。\n"
                    "差別化: 体験重視（美ら海水族館・マリン）。KPI: 予約200件 / CSAT 4.5以上。"
                ),
            },
        ],
    ),
    "hokkaido": ScenarioContent(
        key="hokkaido",
        title="北海道カップル",
        emails=[
            {
                "subject": "【振り返り】2024冬 北海道カップルキャンペーン",
                "body": (
                    "・雪景色×温泉が予約の決め手。夜の星空ツアーが満足度トップ。\n"
                    "・流氷シーズンは早期完売（2か月前で満室）。"
                ),
            },
            {
                "subject": "カップル向け 価格・承認ルール",
                "body": "・価格上限 1人12万円、利益率 25%以上。記念日演出は原価管理を徹底。",
            },
        ],
        teams_messages=[
            "カップルは個室露天＋記念日演出（ケーキ/写真）が刺さります。",
            "予約の山は12〜2月、早割が効きます。流氷は早期完売注意。",
        ],
        documents=[
            {
                "name": "ブランドガイドライン_カップル.txt",
                "content": "トーン: ロマンティック / 特別感 / 二人だけの時間。",
            },
            {
                "name": "会議メモ_北海道カップル.txt",
                "content": "ターゲット20〜30代カップル。予算上限1人12万円・利益率25%。KPI: 予約150件 / CSAT4.6。差別化=星空・流氷の今だけ体験。",
            },
        ],
    ),
    "kyoto": ScenarioContent(
        key="kyoto",
        title="京都シニア",
        emails=[
            {
                "subject": "【振り返り】2024秋 京都シニアプラン",
                "body": (
                    "・紅葉名所＋ゆったり日程が好評。バリアフリー宿の需要増。\n"
                    "・早朝拝観が満足度高。リピート率が高い。"
                ),
            },
            {
                "subject": "シニア向け 価格・承認ルール",
                "body": "・価格上限 1人10万円、利益率 22%以上。移動負担軽減（タクシー観光）を標準化。",
            },
        ],
        teams_messages=[
            "シニアは健康・ゆとり訴求と少人数・ゆったり移動が効きます。",
            "リピート率が高いので、次回案内の同意取得が重要です。早朝拝観が人気。",
        ],
        documents=[
            {
                "name": "ブランドガイドライン_シニア.txt",
                "content": "トーン: 健康 / ゆとり / 本物志向。",
            },
            {
                "name": "会議メモ_京都シニア.txt",
                "content": "ターゲット60代以上。予算上限1人10万円・利益率22%。KPI: 予約120件 / リピート率30%。差別化=本物の文化体験＋負担の少ない行程。",
            },
        ],
    ),
    "hawaii": ScenarioContent(
        key="hawaii",
        title="ハワイ学生",
        emails=[
            {
                "subject": "【振り返り】2024夏 ハワイ学生旅行",
                "body": (
                    "・グループ割引と分割払いが予約を後押し。SNS映えが集客の鍵。\n"
                    "・卒業旅行需要が最大。"
                ),
            },
            {
                "subject": "学生向け 価格・承認ルール",
                "body": "・価格上限 1人18万円、利益率 18%以上（数で稼ぐ）。早割＋グループ割を併用可。",
            },
        ],
        teams_messages=[
            "学生はコスパ＋映えスポット（ダイヤモンドヘッド/ビーチ）が刺さります。",
            "TikTok/Instagram 経由の予約が大半。UGC施策が伸びます。卒業旅行需要が大きい。",
        ],
        documents=[
            {
                "name": "ブランドガイドライン_学生.txt",
                "content": "トーン: 自由 / 冒険 / コスパ。",
            },
            {
                "name": "会議メモ_ハワイ学生.txt",
                "content": "ターゲット大学生グループ。予算上限1人18万円・利益率18%。KPI: 予約180件。差別化=映え×コスパ。販促=SNS＋早割＆グループ割。",
            },
        ],
    ),
}


def _acquire_token(client_id: str, tenant_id: str) -> str:
    """device-code フローで委任トークンを取得する（ブラウザで対話サインイン）。"""
    credential = DeviceCodeCredential(
        client_id=client_id,
        tenant_id=tenant_id,
        prompt_callback=lambda verification_uri, user_code, _expires_on: print(
            f"\n[サインイン] {verification_uri} を開き、コード {user_code} を入力して "
            "MOD Administrator でサインインしてください。\n"
        ),
    )
    token = credential.get_token(*_GRAPH_SCOPES)
    return token.token


def _client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=_GRAPH,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=30.0,
    )


def _get_my_address(client: httpx.Client) -> str:
    resp = client.get("/me", params={"$select": "userPrincipalName,mail"})
    resp.raise_for_status()
    data = resp.json()
    return data.get("mail") or data.get("userPrincipalName")


def _resolve_team_channel(client: httpx.Client) -> tuple[str | None, str | None]:
    """参加チームの最初のチーム / General チャネルを返す（見つからなければ None）。"""
    teams = client.get("/me/joinedTeams").json().get("value", [])
    if not teams:
        return None, None
    team_id = teams[0]["id"]
    channels = client.get(f"/teams/{team_id}/channels").json().get("value", [])
    if not channels:
        return team_id, None
    general = next((c for c in channels if c.get("displayName") == "General"), channels[0])
    return team_id, general["id"]


def _send_email(client: httpx.Client, to_address: str, subject: str, body: str) -> None:
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to_address}}],
        },
        "saveToSentItems": True,
    }
    resp = client.post("/me/sendMail", json=payload)
    resp.raise_for_status()


def _post_teams_message(client: httpx.Client, team_id: str, channel_id: str, text: str) -> None:
    payload = {"body": {"contentType": "html", "content": text}}
    resp = client.post(f"/teams/{team_id}/channels/{channel_id}/messages", json=payload)
    resp.raise_for_status()


def _upload_document(client: httpx.Client, folder: str, name: str, content: str) -> None:
    path = f"/me/drive/root:/{folder}/{name}:/content"
    resp = client.put(
        path,
        content=content.encode("utf-8"),
        headers={"Authorization": client.headers["Authorization"], "Content-Type": "text/plain"},
    )
    resp.raise_for_status()


def _ensure_folder(client: httpx.Client, parent_path: str, name: str) -> None:
    """OneDrive にフォルダを作成する（既存なら 409 を握りつぶす）。

    Graph の simple upload (`PUT .../{path}:/content`) は中間フォルダを自動生成しないため、
    文書アップロード前に WorkIQ-Demo/{scenario} を用意する。
    """
    url = f"/me/drive/root:/{parent_path}:/children" if parent_path else "/me/drive/root/children"
    resp = client.post(
        url,
        json={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"},
    )
    if resp.status_code == 409:
        return  # 既に存在
    resp.raise_for_status()


def _seed(
    client: httpx.Client,
    scenarios: list[ScenarioContent],
    *,
    dry_run: bool,
    skip_teams: bool,
    team_id: str | None,
    channel_id: str | None,
) -> tuple[int, int]:
    """シナリオを順に投入する。(成功件数, 失敗件数) を返す。"""
    ok = 0
    fail = 0
    to_address = "(dry-run)" if dry_run else _get_my_address(client)
    if not dry_run and not skip_teams and (team_id is None or channel_id is None):
        team_id, channel_id = _resolve_team_channel(client)

    for scenario in scenarios:
        print(f"\n=== {scenario.title} ({scenario.key}) ===")
        for email in scenario.emails:
            label = f"📧 メール: {email['subject']}"
            try:
                if not dry_run:
                    _send_email(client, to_address, email["subject"], email["body"])
                print(f"  {'[dry-run] ' if dry_run else ''}{label}")
                ok += 1
            except Exception as exc:  # noqa: BLE001 - スクリプトなので集計して継続
                print(f"  [FAIL] {label}: {exc}")
                fail += 1
        if skip_teams:
            print("  💬 Teams: スキップ (--skip-teams)")
        elif dry_run or (team_id and channel_id):
            for message in scenario.teams_messages:
                try:
                    if not dry_run:
                        _post_teams_message(client, team_id, channel_id, message)
                    print(f"  {'[dry-run] ' if dry_run else ''}💬 Teams: {message[:40]}...")
                    ok += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"  [FAIL] 💬 Teams: {exc}")
                    fail += 1
        else:
            print("  💬 Teams: 投稿先チーム/チャネル未解決のためスキップ（--team-id/--channel-id を指定）")
        if not dry_run and scenario.documents:
            try:
                _ensure_folder(client, "", "WorkIQ-Demo")
                _ensure_folder(client, "WorkIQ-Demo", scenario.key)
            except Exception as exc:  # noqa: BLE001
                print(f"  [WARN] フォルダ作成に失敗（文書アップロードも失敗する可能性）: {exc}")
        for doc in scenario.documents:
            label = f"📄 文書: WorkIQ-Demo/{scenario.key}/{doc['name']}"
            try:
                if not dry_run:
                    _upload_document(client, f"WorkIQ-Demo/{scenario.key}", doc["name"], doc["content"])
                print(f"  {'[dry-run] ' if dry_run else ''}{label}")
                ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  [FAIL] {label}: {exc}")
                fail += 1
    return ok, fail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Work IQ デモデータを M365 にシードする (委任/device-code)")
    parser.add_argument("--client-id", required=True, help="Entra public client アプリの Application (client) ID")
    parser.add_argument("--tenant-id", required=True, help="テナント ID（または onmicrosoft.com ドメイン）")
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=sorted(SCENARIOS.keys()),
        default=sorted(SCENARIOS.keys()),
        help="投入するシナリオ（既定: 全4本）",
    )
    parser.add_argument("--team-id", default=None, help="Teams 投稿先チーム ID（未指定なら自動検出）")
    parser.add_argument("--channel-id", default=None, help="Teams 投稿先チャネル ID（未指定なら General を自動検出）")
    parser.add_argument("--skip-teams", action="store_true", help="Teams 投稿をスキップ")
    parser.add_argument("--dry-run", action="store_true", help="M365 には作成せず投入内容のみ表示")
    args = parser.parse_args(argv)

    selected = [SCENARIOS[key] for key in args.scenarios]

    if args.dry_run:
        print("=== DRY-RUN: M365 には何も作成しません。投入予定の内容のみ表示します ===")
        ok, fail = _seed(
            httpx.Client(base_url=_GRAPH),  # token 不要（dry-run は API を呼ばない）
            selected,
            dry_run=True,
            skip_teams=args.skip_teams,
            team_id=args.team_id,
            channel_id=args.channel_id,
        )
    else:
        token = _acquire_token(args.client_id, args.tenant_id)
        with _client(token) as client:
            ok, fail = _seed(
                client,
                selected,
                dry_run=False,
                skip_teams=args.skip_teams,
                team_id=args.team_id,
                channel_id=args.channel_id,
            )

    print(f"\n完了: 成功 {ok} 件 / 失敗 {fail} 件")
    if not args.dry_run:
        print(
            "注意: M365 Copilot/Substrate のインデックス反映に時間がかかります（数分〜最大1日）。\n"
            "デモ前に Copilot で retrieval されるか事前検証してください。"
        )
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())

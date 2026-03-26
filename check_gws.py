"""
Google Workspace API 接続チェッカー

OAuth クライアント認証情報を使って Google Sheets / Drive API に接続し、
テスト用スプレッドシートの作成・書き込み・読み込み・シート追加・削除を実行して
正常動作を確認する。

config.env の GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET を使用。
初回実行時にブラウザで OAuth 認証フローを実行し、トークンを保存する。
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(SCRIPT_DIR, "google_token.json")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def load_env():
    """config.env から環境変数を読み込む"""
    env_path = os.path.join(SCRIPT_DIR, "config.env")
    if not os.path.exists(env_path):
        return {}
    result = {}
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                result[key.strip()] = val.strip()
    return result


def get_credentials(env):
    """OAuth 認証情報を取得する。トークンがなければ認証フローを実行"""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    creds = None

    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
        except Exception as e:
            print(f"  Token refresh failed: {e}")
            creds = None

    if not creds or not creds.valid:
        client_id = env.get("GOOGLE_OAUTH_CLIENT_ID", "")
        client_secret = env.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
        client_config = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
        print("")
        print("  *** OAuth authentication required ***")
        print("  A browser window will open. Sign in and grant access.")
        print("")
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        print(f"  Token saved: {TOKEN_PATH}")

    return creds


def run_spreadsheet_test(creds):
    """テスト用スプレッドシートを作成→書き込み→読み込み→シート追加→削除して検証する"""
    from googleapiclient.discovery import build

    sheets = build("sheets", "v4", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    test_title = "_GWS_API_TEST_ (delete me)"
    sheet_id = None

    try:
        # 1. 新規スプレッドシート作成
        print("[4] Create test spreadsheet")
        spreadsheet = sheets.spreadsheets().create(
            body={"properties": {"title": test_title}},
            fields="spreadsheetId",
        ).execute()
        sheet_id = spreadsheet["spreadsheetId"]
        print(f"  Created: {sheet_id}")

        # 2. 書き込み
        print("[5] Write test data")
        sheets.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range="Sheet1!A1:B2",
            valueInputOption="RAW",
            body={"values": [["test_key", "test_value"], ["hello", "world"]]},
        ).execute()
        print("  Write: OK")

        # 3. 読み込み
        print("[6] Read test data")
        result = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range="Sheet1!A1:B2",
        ).execute()
        values = result.get("values", [])
        if values == [["test_key", "test_value"], ["hello", "world"]]:
            print(f"  Read: OK (verified {len(values)} rows)")
        else:
            print(f"  Read: MISMATCH {values}")
            return False

        # 4. シート追加
        print("[7] Add new sheet")
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": "TestSheet2"}}}]},
        ).execute()
        print("  Add sheet: OK")

        # 5. 削除
        print("[8] Delete test spreadsheet")
        drive.files().delete(fileId=sheet_id).execute()
        sheet_id = None
        print("  Delete: OK")

        return True

    except Exception as e:
        print(f"  FAILED: {e}")
        return False

    finally:
        # テスト失敗時もクリーンアップ
        if sheet_id:
            try:
                drive.files().delete(fileId=sheet_id).execute()
                print(f"  Cleanup: deleted {sheet_id}")
            except Exception:
                print(f"  Cleanup FAILED: manually delete '{test_title}' from Google Drive")


def check():
    """Google Workspace API の接続チェックを実行する"""
    env = load_env()

    # --- 1. config.env チェック ---
    print("[1] Config")
    email = env.get("GOOGLE_EMAIL", "")
    client_id = env.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = env.get("GOOGLE_OAUTH_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        print("  NOT SET")
        print("")
        print("  config.env に以下を追加してください:")
        print("    GOOGLE_EMAIL=your-email@example.com")
        print("    GOOGLE_OAUTH_CLIENT_ID=xxxx.apps.googleusercontent.com")
        print("    GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-xxxx")
        return

    print(f"  Email: {email or '(not set)'}")
    print(f"  Client ID: {client_id[:20]}...")
    print("")

    # --- 2. ライブラリチェック ---
    print("[2] Python Libraries")
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        print("  google-api-python-client: OK")
        print("  google-auth-oauthlib: OK")
    except ImportError as e:
        print(f"  MISSING: {e}")
        print("  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        return
    print("")

    # --- 3. 認証 ---
    print("[3] Authentication")
    if os.path.exists(TOKEN_PATH):
        print(f"  Token file: {TOKEN_PATH}")
    else:
        print("  Token file: not found (will start OAuth flow)")

    try:
        creds = get_credentials(env)
        print(f"  Auth: OK")
    except Exception as e:
        print(f"  Auth FAILED: {e}")
        return
    print("")

    # --- 4-8. スプレッドシート CRUD テスト ---
    success = run_spreadsheet_test(creds)
    print("")

    if success:
        print("=== All checks passed (create/write/read/add-sheet/delete OK) ===")
    else:
        print("=== Some checks FAILED ===")


if __name__ == "__main__":
    print("==========================================")
    print("  Google Workspace API Checker")
    print("==========================================")
    print()
    check()

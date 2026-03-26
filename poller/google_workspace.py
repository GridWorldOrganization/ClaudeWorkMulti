"""
Google Workspace URL 検出・内容取得

メッセージ内の Google Docs/Sheets/Slides/Drive URL を検出し、
API で内容を取得してテキストとして返す。
"""

import logging
import os
import re
from typing import Any

from poller.config import (
    GOOGLE_API_SCOPES,
    GOOGLE_DOC_MAX_CHARS,
    GOOGLE_SHEET_MAX_ROWS,
    GOOGLE_TOKEN_PATH,
)

log = logging.getLogger(__name__)

# Google URL パターン: ドキュメント ID を抽出
_URL_PATTERNS = [
    (re.compile(r'https?://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)'), "spreadsheet"),
    (re.compile(r'https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)'), "document"),
    (re.compile(r'https?://docs\.google\.com/presentation/d/([a-zA-Z0-9_-]+)'), "presentation"),
    (re.compile(r'https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)'), "drive_file"),
    (re.compile(r'https?://drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)'), "drive_file"),
]


def detect_urls(message: str) -> list[tuple[str, str, str]]:
    """メッセージから Google Workspace の URL を検出し、[(file_id, file_type, url)] を返す"""
    found: list[tuple[str, str, str]] = []
    seen_ids: set[str] = set()
    for pattern, file_type in _URL_PATTERNS:
        for match in pattern.finditer(message):
            file_id = match.group(1)
            if file_id not in seen_ids:
                seen_ids.add(file_id)
                found.append((file_id, file_type, match.group(0)))
    return found


def _get_credentials() -> Any:
    """OAuth トークンから認証情報を取得する。失敗時は None"""
    if not os.path.exists(GOOGLE_TOKEN_PATH):
        return None
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_PATH, GOOGLE_API_SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return creds
    except Exception as e:
        log.error(f"Google認証エラー: {e}")
        return None


def fetch_content(file_id: str, file_type: str) -> str | None:
    """Google API でファイルの内容を取得する。失敗時は None"""
    creds = _get_credentials()
    if not creds:
        return None

    try:
        if file_type == "spreadsheet":
            return _fetch_spreadsheet(creds, file_id)
        elif file_type == "document":
            return _fetch_document(creds, file_id)
        elif file_type == "presentation":
            return _fetch_presentation(creds, file_id)
        elif file_type == "drive_file":
            return _fetch_drive_file(creds, file_id)
    except Exception as e:
        log.error(f"Google内容取得エラー ({file_type} {file_id}): {e}")
        return f"[取得エラー: {str(e)[:200]}]"
    return None


def _fetch_spreadsheet(creds: Any, file_id: str) -> str:
    """スプレッドシートの全シート内容をテキストで返す"""
    from googleapiclient.discovery import build

    sheets = build("sheets", "v4", credentials=creds)
    meta = sheets.spreadsheets().get(spreadsheetId=file_id).execute()
    title = meta["properties"]["title"]
    sheet_names = [s["properties"]["title"] for s in meta["sheets"]]

    parts = [f"スプレッドシート: {title}"]
    for sheet_name in sheet_names:
        result = sheets.spreadsheets().values().get(
            spreadsheetId=file_id, range=f"'{sheet_name}'",
        ).execute()
        values = result.get("values", [])
        if values:
            parts.append(f"\n[シート: {sheet_name}] ({len(values)}行)")
            for row in values[:GOOGLE_SHEET_MAX_ROWS]:
                parts.append("\t".join(str(cell) for cell in row))
            if len(values) > GOOGLE_SHEET_MAX_ROWS:
                parts.append(f"  ... 以下省略（全{len(values)}行）")
        else:
            parts.append(f"\n[シート: {sheet_name}] (空)")
    return "\n".join(parts)


def _fetch_document(creds: Any, file_id: str) -> str:
    """Google ドキュメントの内容をテキストで返す"""
    from googleapiclient.discovery import build

    drive = build("drive", "v3", credentials=creds)
    content = drive.files().export(fileId=file_id, mimeType="text/plain").execute()
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    meta = drive.files().get(fileId=file_id, fields="name").execute()
    title = meta.get("name", "不明")
    if len(content) > GOOGLE_DOC_MAX_CHARS:
        content = content[:GOOGLE_DOC_MAX_CHARS] + f"\n\n... 以下省略（全{len(content)}文字）"
    return f"ドキュメント: {title}\n\n{content}"


def _fetch_presentation(creds: Any, file_id: str) -> str:
    """Google スライドの内容をテキストで返す"""
    from googleapiclient.discovery import build

    slides_service = build("slides", "v1", credentials=creds)
    presentation = slides_service.presentations().get(presentationId=file_id).execute()
    title = presentation.get("title", "不明")

    parts = [f"プレゼンテーション: {title}"]
    for i, slide in enumerate(presentation.get("slides", []), 1):
        texts: list[str] = []
        for element in slide.get("pageElements", []):
            shape = element.get("shape", {})
            text_content = shape.get("text", {})
            for text_elem in text_content.get("textElements", []):
                run = text_elem.get("textRun", {})
                if run.get("content", "").strip():
                    texts.append(run["content"].strip())
        if texts:
            parts.append(f"\n[スライド {i}]")
            parts.append("\n".join(texts))
    return "\n".join(parts)


def _fetch_drive_file(creds: Any, file_id: str) -> str:
    """Drive ファイルのメタ情報を返す（Workspace ファイルは適切なハンドラに委譲）"""
    from googleapiclient.discovery import build

    drive = build("drive", "v3", credentials=creds)
    meta = drive.files().get(fileId=file_id, fields="name, mimeType, size").execute()
    name = meta.get("name", "不明")
    mime = meta.get("mimeType", "不明")
    size = meta.get("size", "不明")

    if "spreadsheet" in mime:
        return _fetch_spreadsheet(creds, file_id)
    elif "document" in mime:
        return _fetch_document(creds, file_id)
    elif "presentation" in mime:
        return _fetch_presentation(creds, file_id)
    elif mime.startswith("text/"):
        content = drive.files().get_media(fileId=file_id).execute()
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        if len(content) > GOOGLE_DOC_MAX_CHARS:
            content = content[:GOOGLE_DOC_MAX_CHARS] + "\n\n... 以下省略"
        return f"ファイル: {name} ({mime})\n\n{content}"
    else:
        return f"ファイル: {name}\n  MIMEタイプ: {mime}\n  サイズ: {size}バイト\n  ※ テキスト以外のファイルは内容を取得できません"


def resolve_urls(message: str) -> str:
    """メッセージ内の Google URL を検出し、内容を取得してテキストを返す。なければ空文字"""
    urls = detect_urls(message)
    if not urls:
        return ""
    parts: list[str] = []
    for file_id, file_type, url in urls:
        log.info(f"Google URL検出: type={file_type} id={file_id}")
        content = fetch_content(file_id, file_type)
        if content:
            parts.append(f"=== 参照ファイル: {url} ===\n{content}")
        else:
            parts.append(f"=== 参照ファイル: {url} ===\n[内容を取得できませんでした]")
    return "\n\n".join(parts)

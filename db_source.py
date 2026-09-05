"""配信用DB (serve.db) を Cloudflare R2 から取得する.

クラウド (Streamlit Community Cloud) で動かすとき、DBはリポジトリに入れず R2 から取る。
serve.db は 54MB。集計テーブルのみで生データを含まないため、全国展開しても
ほぼ肥大しない設計になっている (2026-09-01)。

取得の考え方:
- 起動時に1回だけ取る。以後は同じファイルを使う
- ETag を保存しておき、変わっていなければダウンロードしない (毎晩の更新を拾うため)
- ダウンロード中に落ちても壊れたDBを掴まないよう、一時ファイルに書いてから差し替える
- **取得できなければ黙って続けず、原因を明示して止める**
  (初版は失敗時にローカルパスを返していたため、sqlite が空DBを新規作成してしまい
   「テーブルが無い」という無関係なエラーになった。2026-09-01 修正)

保存先:
  リポジトリ配下は読み取り専用のことがあるため、書ける場所を順に試す。

Streamlit の Secrets に以下を入れる:
  R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET / R2_KEY
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
DB_NAME = "serve.db"


def _cache_dir() -> Path:
    """書き込める場所を順に試す。Streamlit Cloud ではリポジトリ配下が使えないことがある."""
    for cand in (ROOT / "data",
                 Path(tempfile.gettempdir()) / "hallscore",
                 Path.home() / ".cache" / "hallscore"):
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / ".w"
            probe.write_text("1")
            probe.unlink()
            return cand
        except Exception:
            continue
    raise RuntimeError("書き込めるディレクトリが見つかりません")


def _secret(name: str, default: str = "") -> str:
    """Streamlit Secrets → 環境変数 の順に見る."""
    try:
        import streamlit as st
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return os.environ.get(name, default).strip()


def _healthy(path: Path) -> bool:
    """空DBや壊れたDBを掴んでいないかを確かめる."""
    if not path.exists() or path.stat().st_size < 100_000:
        return False
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        n = con.execute("SELECT COUNT(*) FROM halls").fetchone()[0]
        con.close()
        return n > 0
    except Exception:
        return False


def ensure_db() -> Path:
    """serve.db を用意して、そのパスを返す。用意できなければ例外を投げる."""
    cache = _cache_dir()
    local = cache / DB_NAME
    etag_file = cache / ".serve_etag"
    why: list[str] = []

    acct = _secret("R2_ACCOUNT_ID")
    key_id = _secret("R2_ACCESS_KEY_ID")
    secret = _secret("R2_SECRET_ACCESS_KEY")
    bucket = _secret("R2_BUCKET", "hallscore")
    obj_key = _secret("R2_KEY", DB_NAME)

    missing = [n for n, v in (("R2_ACCOUNT_ID", acct),
                              ("R2_ACCESS_KEY_ID", key_id),
                              ("R2_SECRET_ACCESS_KEY", secret)) if not v]
    if missing:
        why.append(f"Secrets が未設定: {', '.join(missing)}")
    else:
        try:
            import boto3
            from botocore.config import Config
            cli = boto3.client(
                "s3",
                endpoint_url=f"https://{acct}.r2.cloudflarestorage.com",
                aws_access_key_id=key_id,
                aws_secret_access_key=secret,
                config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
                region_name="auto",
            )
            head = cli.head_object(Bucket=bucket, Key=obj_key)
            etag = head.get("ETag", "").strip('"')
            prev = etag_file.read_text().strip() if etag_file.exists() else ""
            if not (_healthy(local) and prev == etag and etag):
                fd, tmp = tempfile.mkstemp(dir=str(cache), suffix=".part")
                os.close(fd)
                try:
                    cli.download_file(bucket, obj_key, tmp)
                    if not _healthy(Path(tmp)):
                        raise RuntimeError("ダウンロードしたDBに halls テーブルがありません")
                    shutil.move(tmp, local)
                    etag_file.write_text(etag)
                finally:
                    Path(tmp).unlink(missing_ok=True)
        except ImportError as e:
            why.append(f"boto3 を読み込めません: {e}")
        except Exception as e:
            why.append(f"R2 から取得できません ({bucket}/{obj_key}): {type(e).__name__}: {e}")

    if _healthy(local):
        return local

    # ここに来たら黙って続けない。空DBを掴ませると無関係なエラーになる
    detail = "\n".join(f"  - {w}" for w in why) or "  - 原因不明"
    raise RuntimeError(
        "配信用DB (serve.db) を用意できませんでした。\n"
        f"保存先: {cache}\n{detail}\n"
        "Streamlit Cloud の Manage app → Settings → Secrets に "
        "R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET / R2_KEY を "
        'TOML 形式 (値はダブルクォートで囲む) で設定してください。'
    )

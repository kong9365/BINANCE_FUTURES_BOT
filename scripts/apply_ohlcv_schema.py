#!/usr/bin/env python3
"""Binance-MS 에 ohlcv/funding_history DDL 적용.

방법 1 (권장): Dashboard SQL Editor 에 db/migrations/supabase_ohlcv_bundle.sql 붙여넣기

방법 2: .env 에 SUPABASE_DB_PASSWORD 설정 후 본 스크립트 실행
  (Dashboard → Settings → Database → Database password)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

SQL_PATH = PROJECT_ROOT / "db" / "migrations" / "supabase_ohlcv_bundle.sql"


def main() -> None:
    sql = SQL_PATH.read_text(encoding="utf-8")
    pwd = os.environ.get("SUPABASE_DB_PASSWORD") or os.environ.get("POSTGRES_PASSWORD")
    url = os.environ.get("SUPABASE_OHLCV_URL") or os.environ.get("SUPABASE_URL")
    if not pwd or not url:
        print("SUPABASE_DB_PASSWORD not set - run SQL manually in Dashboard:")
        print(f"  파일: {SQL_PATH}")
        sys.exit(2)
    ref = url.split("//")[1].split(".")[0]
    host = f"aws-0-ap-southeast-1.pooler.supabase.com"
    conn_str = f"postgresql://postgres.{ref}:{pwd}@{host}:6543/postgres"
    try:
        import psycopg
    except ImportError:
        print("psycopg 미설치: pip install psycopg[binary]")
        sys.exit(1)
    with psycopg.connect(conn_str) as conn:
        conn.execute(sql)
        conn.commit()
    print("DDL 적용 완료 (ohlcv + funding_history)")


if __name__ == "__main__":
    main()

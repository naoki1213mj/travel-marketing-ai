"""Step 1 — Query DISTINCT values from v2 lakehouse SQL endpoint.

Saves to distinct_values.json for use by Step 2 (aiInstructions authoring).
"""
from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path

import pyodbc

ENDPOINT = "pabkxzbptdhuzf2qxkx52ftsp4-fl3w6clumg5evdymcqcfj6tmh4.datawarehouse.fabric.microsoft.com"
DATABASE = "lh_travel_marketing_v2"
SQL_COPT_SS_ACCESS_TOKEN = 1256

QUERIES: list[tuple[str, str]] = [
    ("destination_region", "SELECT DISTINCT destination_region AS v, COUNT(*) AS cnt FROM dbo.booking GROUP BY destination_region ORDER BY cnt DESC"),
    ("destination_country", "SELECT DISTINCT destination_country AS v, COUNT(*) AS cnt FROM dbo.booking GROUP BY destination_country ORDER BY cnt DESC"),
    ("destination_city", "SELECT DISTINCT destination_city AS v, COUNT(*) AS cnt FROM dbo.booking GROUP BY destination_city ORDER BY cnt DESC"),
    ("destination_type", "SELECT DISTINCT destination_type AS v, COUNT(*) AS cnt FROM dbo.booking GROUP BY destination_type ORDER BY cnt DESC"),
    ("season", "SELECT DISTINCT season AS v, COUNT(*) AS cnt FROM dbo.booking GROUP BY season ORDER BY cnt DESC"),
    ("product_type", "SELECT DISTINCT product_type AS v, COUNT(*) AS cnt FROM dbo.booking GROUP BY product_type ORDER BY cnt DESC"),
    ("booking_status", "SELECT DISTINCT booking_status AS v, COUNT(*) AS cnt FROM dbo.booking GROUP BY booking_status ORDER BY cnt DESC"),
    ("customer_segment", "SELECT DISTINCT customer_segment AS v, COUNT(*) AS cnt FROM dbo.customer GROUP BY customer_segment ORDER BY cnt DESC"),
    ("age_band", "SELECT DISTINCT age_band AS v, COUNT(*) AS cnt FROM dbo.customer GROUP BY age_band ORDER BY cnt DESC"),
    ("loyalty_tier", "SELECT DISTINCT loyalty_tier AS v, COUNT(*) AS cnt FROM dbo.customer GROUP BY loyalty_tier ORDER BY cnt DESC"),
    ("acquisition_channel", "SELECT DISTINCT acquisition_channel AS v, COUNT(*) AS cnt FROM dbo.customer GROUP BY acquisition_channel ORDER BY cnt DESC"),
    ("gender", "SELECT DISTINCT gender AS v, COUNT(*) AS cnt FROM dbo.customer GROUP BY gender ORDER BY cnt DESC"),
    ("prefecture", "SELECT TOP 25 prefecture AS v, COUNT(*) AS cnt FROM dbo.customer GROUP BY prefecture ORDER BY cnt DESC"),
    ("cancellation_reason", "SELECT DISTINCT cancellation_reason AS v, COUNT(*) AS cnt FROM dbo.cancellation GROUP BY cancellation_reason ORDER BY cnt DESC"),
    ("payment_method", "SELECT DISTINCT payment_method AS v, COUNT(*) AS cnt FROM dbo.payment GROUP BY payment_method ORDER BY cnt DESC"),
    ("payment_status", "SELECT DISTINCT payment_status AS v, COUNT(*) AS cnt FROM dbo.payment GROUP BY payment_status ORDER BY cnt DESC"),
    ("currency", "SELECT DISTINCT currency AS v, COUNT(*) AS cnt FROM dbo.payment GROUP BY currency ORDER BY cnt DESC"),
    ("campaign_type", "SELECT DISTINCT campaign_type AS v, COUNT(*) AS cnt FROM dbo.campaign GROUP BY campaign_type ORDER BY cnt DESC"),
    ("inquiry_channel", "SELECT DISTINCT channel AS v, COUNT(*) AS cnt FROM dbo.inquiry GROUP BY channel ORDER BY cnt DESC"),
    ("inquiry_type", "SELECT DISTINCT inquiry_type AS v, COUNT(*) AS cnt FROM dbo.inquiry GROUP BY inquiry_type ORDER BY cnt DESC"),
    ("itinerary_item_type", "SELECT DISTINCT item_type AS v, COUNT(*) AS cnt FROM dbo.itinerary_item GROUP BY item_type ORDER BY cnt DESC"),
    ("hotel_category", "SELECT DISTINCT category AS v, COUNT(*) AS cnt FROM dbo.hotel GROUP BY category ORDER BY cnt DESC"),
    ("flight_class", "SELECT DISTINCT flight_class AS v, COUNT(*) AS cnt FROM dbo.flight GROUP BY flight_class ORDER BY cnt DESC"),
    ("airline", "SELECT TOP 15 airline_code AS v, COUNT(*) AS cnt FROM dbo.flight GROUP BY airline_code ORDER BY cnt DESC"),
    ("review_sentiment", "SELECT DISTINCT sentiment AS v, COUNT(*) AS cnt FROM dbo.tour_review GROUP BY sentiment ORDER BY cnt DESC"),
    ("plan_top", "SELECT TOP 30 plan_name AS v, COUNT(*) AS cnt FROM dbo.booking GROUP BY plan_name ORDER BY cnt DESC"),
    ("year_range", "SELECT MIN(YEAR(booking_date)) AS min_yr, MAX(YEAR(booking_date)) AS max_yr, COUNT(*) AS total FROM dbo.booking"),
    ("departure_year_range", "SELECT MIN(YEAR(departure_date)) AS min_yr, MAX(YEAR(departure_date)) AS max_yr FROM dbo.booking"),
    ("payment_year_range", "SELECT MIN(YEAR(paid_at)) AS min_yr, MAX(YEAR(paid_at)) AS max_yr, COUNT(*) AS total FROM dbo.payment WHERE paid_at IS NOT NULL"),
    ("avg_fx_by_year", "SELECT YEAR(paid_at) AS yr, currency, AVG(exchange_rate_to_jpy) AS avg_rate FROM dbo.payment WHERE currency != 'JPY' AND paid_at IS NOT NULL GROUP BY YEAR(paid_at), currency ORDER BY currency, yr"),
    ("revenue_by_year", "SELECT YEAR(departure_date) AS yr, COUNT(*) AS bookings, SUM(total_revenue_jpy) AS revenue_jpy FROM dbo.booking WHERE booking_status IN ('confirmed','completed') GROUP BY YEAR(departure_date) ORDER BY yr"),
    ("inbound_share_by_year", "SELECT YEAR(departure_date) AS yr, SUM(CASE WHEN destination_type='inbound' THEN total_revenue_jpy ELSE 0 END) AS inbound, SUM(total_revenue_jpy) AS total, CAST(SUM(CASE WHEN destination_type='inbound' THEN total_revenue_jpy ELSE 0 END) AS FLOAT) / NULLIF(SUM(total_revenue_jpy),0) AS share FROM dbo.booking GROUP BY YEAR(departure_date) ORDER BY yr"),
]


def get_token():
    r = subprocess.run(
        ["az", "account", "get-access-token", "--resource",
         "https://database.windows.net", "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, shell=True, check=True
    )
    return r.stdout.strip()


def main():
    token = get_token()
    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    conn_str = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server={ENDPOINT},1433;"
        f"Database={DATABASE};"
        "Encrypt=yes;TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )
    conn = pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})
    cur = conn.cursor()
    out: dict = {}
    for name, sql in QUERIES:
        print(f"[{name}] querying...")
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            data = [dict(zip(cols, [str(c) if not isinstance(c, (int, float)) else c for c in r])) for r in rows]
            out[name] = data
            preview = ", ".join(str(d.get("v") or d) for d in data[:8])
            print(f"  {len(data)} rows. preview: {preview}")
        except Exception as e:
            print(f"  ERROR: {e}")
            out[name] = {"error": str(e)}
    cur.close()
    conn.close()

    out_path = Path(__file__).parent / "distinct_values.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()

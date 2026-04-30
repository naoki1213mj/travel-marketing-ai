"""Spot-check whether 'パリ' + 'spring' really has 0 bookings (P08 result)."""
import struct, subprocess
import pyodbc

ENDPOINT = "pabkxzbptdhuzf2qxkx52ftsp4-fl3w6clumg5evdymcqcfj6tmh4.datawarehouse.fabric.microsoft.com"
DB = "lh_travel_marketing_v2"
SQL_COPT_SS_ACCESS_TOKEN = 1256

t = subprocess.run(
    ["az", "account", "get-access-token", "--resource", "https://database.windows.net",
     "--query", "accessToken", "-o", "tsv"],
    capture_output=True, text=True, shell=True, check=True
).stdout.strip()
tb = t.encode("utf-16-le")
ts = struct.pack(f"<I{len(tb)}s", len(tb), tb)
c = pyodbc.connect(
    f"Driver={{ODBC Driver 18 for SQL Server}};Server={ENDPOINT},1433;Database={DB};Encrypt=yes;",
    attrs_before={SQL_COPT_SS_ACCESS_TOKEN: ts}
)
cur = c.cursor()

print("--- パリ + season ---")
cur.execute("SELECT season, COUNT(*) FROM dbo.booking WHERE destination_region='パリ' GROUP BY season ORDER BY 2 DESC")
for r in cur.fetchall():
    print(r)

print()
print("--- パリ + spring + booking_status confirmed/completed ---")
cur.execute("SELECT COUNT(*), SUM(total_revenue_jpy), AVG(price_per_person_jpy) FROM dbo.booking WHERE destination_region='パリ' AND season='spring' AND booking_status IN ('confirmed','completed')")
for r in cur.fetchall():
    print(r)

print()
print("--- パリ + spring (any booking_status) ---")
cur.execute("SELECT booking_status, COUNT(*) FROM dbo.booking WHERE destination_region='パリ' AND season='spring' GROUP BY booking_status")
for r in cur.fetchall():
    print(r)

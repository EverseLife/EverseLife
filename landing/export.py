"""Выгрузка заявок в CSV на стандартный вывод.

    docker compose exec landing python export.py > signups.csv
"""

import csv
import os
import sqlite3
import sys

db = os.environ.get("LANDING_DB", "/data/signups.db")
conn = sqlite3.connect(db)
writer = csv.writer(sys.stdout, lineterminator="\n")
writer.writerow(["email", "created_at", "ip"])
for row in conn.execute("SELECT email, created_at, ip FROM signups ORDER BY id"):
    writer.writerow(row)
conn.close()

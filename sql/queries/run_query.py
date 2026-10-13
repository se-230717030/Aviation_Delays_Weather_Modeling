import sqlite3
import os

# Veritabanına bağlan
db_path = os.path.join("data", "aviation.db")
con = sqlite3.connect(db_path)

# SQL dosyasını oku
with open("sql/queries/analytical.sql", "r", encoding="utf-8") as f:
    query = f.read()

# Sorguyu çalıştır ve sonuçları çek
cur = con.cursor()
cur.execute(query)
rows = cur.fetchall()
columns = [description[0] for description in cur.description]

# Tablo başlıklarını yazdır
print(f"| {columns[0]:<20} | {columns[1]:<25} | {columns[2]:<20} |")
print("|" + "-"*22 + "|" + "-"*27 + "|" + "-"*22 + "|")

# Sonuçları satır satır yazdır
for row in rows:
    print(f"| {str(row[0]):<20} | {str(row[1]):<25} | {str(row[2]):<20} |")

con.close()
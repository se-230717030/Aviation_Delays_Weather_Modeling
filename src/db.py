import sqlite3
import os

def connect(db_path="data/aviation.db"):
    """
    Veritabanına bağlanır ve M2 gereksinimlerine göre ayarları yapar.
    """
    # Veritabanının bulunduğu klasörün var olduğundan emin ol
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    con = sqlite3.connect(db_path)
    
    # Sütun isimlerine sözlük gibi erişebilmek için
    con.row_factory = sqlite3.Row
    
    # Yabancı anahtar (foreign key) ilişkilerini zorunlu kıl (M2 Kriteri: Zorunlu)
    con.execute("PRAGMA foreign_keys = ON")
    
    return con
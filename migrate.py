import sqlite3

conn = sqlite3.connect("database.db")
c = conn.cursor()

# Create table if it doesn't exist
c.execute("""
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY,
    email TEXT,
    intent TEXT,
    reply TEXT,
    purpose TEXT,
    important INTEGER DEFAULT 0
)
""")

# Add sentiment column if it doesn't exist
try:
    c.execute("ALTER TABLE emails ADD COLUMN sentiment TEXT")
    print("✅ Sentiment column added")
except Exception as e:
    print("⚠️", e)

conn.commit()
conn.close()

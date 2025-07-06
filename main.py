from fastapi import FastAPI
import psycopg2
import os
import sys
import traceback

app = FastAPI()

# Read DATABASE_URL from env var
DATABASE_URL = os.getenv("DATABASE_URL")

db_connection = None
db_error = None

@app.on_event("startup")
async def startup_event():
    global db_connection, db_error
    try:
        db_connection = psycopg2.connect(DATABASE_URL)
        db_error = None
        print("✅ Database connection successful")
    except Exception as e:
        db_error = str(e)
        print("❌ Database connection failed:")
        traceback.print_exc()

@app.get("/")
async def root():
    if db_error:
        return {"status": "error", "detail": "DB connection failed", "error": db_error}
    return {"status": "ok", "detail": "Backend and DB connected"}

@app.get("/python-version")
async def python_version():
    return {"version": sys.version}




























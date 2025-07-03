from fastapi import FastAPI
import os
import logging
import psycopg2

logging.basicConfig(level=logging.DEBUG)

app = FastAPI()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

logging.debug(f"DATABASE_URL: {repr(DATABASE_URL)}")

try:
    conn = psycopg2.connect(DATABASE_URL)
    logging.debug("Database connection successful")
except Exception as e:
    logging.error(f"Database connection failed: {e}")
    raise

@app.get("/")
async def root():
    return {"message": "Rentonomic backend is running"}

@app.get("/health")
async def health_check():
    return {"status": "ok"}
















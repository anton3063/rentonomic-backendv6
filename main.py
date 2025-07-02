from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import psycopg2
import cloudinary

app = FastAPI()

# CORS setup (adjust origins as needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://rentonomic.com",
        "https://www.rentonomic.com",
        "https://rentonomic.netlify.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Environment variables
DATABASE_URL = os.getenv("DATABASE_URL")
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
    raise RuntimeError("Cloudinary environment variables are not set properly")

# Configure Cloudinary
cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET,
)

# Connect to Postgres database
try:
    # Strip whitespace to avoid hidden newline issues
    conn = psycopg2.connect(DATABASE_URL.strip())
except Exception as e:
    raise RuntimeError(f"Error connecting to the database: {e}")

@app.get("/")
async def root():
    return {"message": "Rentonomic backend is running"}

@app.get("/debug-env")
async def debug_env():
    def show_whitespace(s):
        return s.replace(" ", "[space]").replace("\n", "[\\n]").replace("\t", "[\\t]")
    return {
        "DATABASE_URL": show_whitespace(DATABASE_URL or ""),
        "CLOUDINARY_CLOUD_NAME": show_whitespace(CLOUDINARY_CLOUD_NAME or ""),
        "CLOUDINARY_API_KEY": show_whitespace(CLOUDINARY_API_KEY or ""),
        "CLOUDINARY_API_SECRET": show_whitespace(CLOUDINARY_API_SECRET or ""),
    }

# Add other endpoints below as needed














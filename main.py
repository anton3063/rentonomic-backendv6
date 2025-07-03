from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import psycopg2
import cloudinary

app = FastAPI()

# Setup CORS - add your frontend URLs here
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://rentonomic.com", "https://www.rentonomic.com", "https://rentonomic.netlify.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load environment variables
DATABASE_URL = os.getenv("DATABASE_URL")
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")

# Debug prints (remove or comment out in production)
print(f"DEBUG: DATABASE_URL: {DATABASE_URL}")
print(f"DEBUG: CLOUDINARY_CLOUD_NAME: {CLOUDINARY_CLOUD_NAME}")

# Connect to the database
try:
    conn = psycopg2.connect(DATABASE_URL)
    print("DEBUG: Database connection successful")
except Exception as e:
    raise RuntimeError(f"Error connecting to the database: {e}")

# Configure Cloudinary
cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET,
)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/debug-env")
async def debug_env():
    # Return key env vars for debugging (avoid returning secrets here in production)
    return {
        "DATABASE_URL": DATABASE_URL,
        "CLOUDINARY_CLOUD_NAME": CLOUDINARY_CLOUD_NAME,
        "CLOUDINARY_API_KEY": CLOUDINARY_API_KEY,
    }

















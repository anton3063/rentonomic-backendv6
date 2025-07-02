import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

print(f"DEBUG: DATABASE_URL is: {repr(DATABASE_URL)}")

try:
    conn = psycopg2.connect(DATABASE_URL)
except Exception as e:
    raise RuntimeError(f"Error connecting to the database: {e}")
import os
print("DATABASE_URL raw value:")
print(repr(os.getenv("DATABASE_URL")))
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os
import psycopg2
import cloudinary

app = FastAPI()

# CORS setup (adjust origins as needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://rentonomic.com", "https://www.rentonomic.com", "https://rentonomic.netlify.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Check required environment variables
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
    conn = psycopg2.connect(DATABASE_URL)
except Exception as e:
    raise RuntimeError(f"Error connecting to the database: {e}")

@app.get("/")
async def root():
    return {"message": "Rentonomic backend is running"}

# Add your other API endpoints here, e.g., listings, uploads, etc.

# Example:
@app.get("/health")
async def health_check():
    return {"status": "ok"}













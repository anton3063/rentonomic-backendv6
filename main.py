import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
import cloudinary

app = FastAPI()

# CORS - allow your frontend domains
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

# Load env vars (optional - if using .env locally)
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")

# Check environment variables
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is missing")

if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
    raise RuntimeError("Cloudinary environment variables are not set properly")

# Configure Cloudinary
cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET,
)

# Test DB connection once on startup
try:
    conn = psycopg2.connect(DATABASE_URL)
    conn.close()
except Exception as e:
    raise RuntimeError(f"Cannot connect to DB: {e}")

# Sample listings — replace with DB fetch later
sample_listings = [
    {
        "id": 1,
        "title": "Cordless Drill",
        "description": "Powerful cordless drill, great for home projects.",
        "location": "YO8",
        "price_per_day": 10,
        "image_url": "https://via.placeholder.com/300x200?text=Drill"
    },
    {
        "id": 2,
        "title": "Lawn Mower",
        "description": "Reliable electric lawn mower for your garden.",
        "location": "YO7",
        "price_per_day": 15,
        "image_url": "https://via.placeholder.com/300x200?text=Lawn+Mower"
    },
    {
        "id": 3,
        "title": "Camera Tripod",
        "description": "Sturdy tripod for photography or video recording.",
        "location": "YO8",
        "price_per_day": 7,
        "image_url": "https://via.placeholder.com/300x200?text=Tripod"
    }
]

@app.get("/")
async def root():
    return {"message": "Rentonomic backend is running"}

@app.get("/listings")
async def get_listings():
    return sample_listings





























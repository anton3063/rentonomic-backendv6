from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import cloudinary.uploader
import psycopg2
import os

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://rentonomic.com",
        "https://www.rentonomic.com",
        "https://rentonomic.netlify.app"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# PostgreSQL config (Railway)
DB_HOST = os.getenv("DB_HOST", "your-db-host")  # replace if needed
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "Concrete-0113xyz")

conn = psycopg2.connect(
    host=DB_HOST,
    port=DB_PORT,
    dbname=DB_NAME,
    user=DB_USER,
    password=DB_PASSWORD
)

# Cloudinary config
cloudinary.config(
    cloud_name="dwpwl5xnj",
    api_key="478748579539389",
    api_secret="bdli1k8T4Uac6JPW8nufZSvtds4"
)

@app.post("/listing")
async def create_listing(
    title: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: float = Form(...),
    image: UploadFile = File(...)
):
    try:
        upload_result = cloudinary.uploader.upload(image.file)
        image_url = upload_result.get("secure_url")

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO listings (title, location, description, price_per_day, image_url)
                VALUES (%s, %s, %s, %s, %s)
            """, (title, location, description, price_per_day, image_url))
            conn.commit()

        return {"message": "Listing created successfully", "image_url": image_url}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/listings")
def get_listings():
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT title, location, description, price_per_day, image_url FROM listings")
            listings = cur.fetchall()
            results = []
            for row in listings:
                results.append({
                    "title": row[0],
                    "location": row[1],
                    "description": row[2],
                    "price_per_day": row[3],
                    "image_url": row[4]
                })
        return results

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})




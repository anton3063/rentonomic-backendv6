from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import cloudinary
import cloudinary.uploader
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://rentonomic.com", "https://www.rentonomic.com", "https://rentonomic.netlify.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cloudinary config
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)

# PostgreSQL config
DATABASE_URL = os.getenv("DATABASE_URL")

@app.get("/")
def read_root():
    return {"message": "Rentonomic backend running!"}

@app.post("/listing")
async def create_listing(
    title: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: float = Form(...),
    image: UploadFile = File(...)
):
    try:
        # Upload image to Cloudinary
        upload_result = cloudinary.uploader.upload(image.file)
        image_url = upload_result.get("secure_url")

        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO listings (title, location, description, price_per_day, image_url)
            VALUES (%s, %s, %s, %s, %s)
        """, (title, location, description, price_per_day, image_url))

        conn.commit()
        cur.close()
        conn.close()

        return {"message": "Listing created successfully", "image_url": image_url}
    except Exception as e:
        return {"error": str(e)}

@app.get("/listings")
def get_listings():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        cur.execute("SELECT title, location, description, price_per_day, image_url FROM listings")
        listings = cur.fetchall()

        cur.close()
        conn.close()

        return [
            {
                "title": row[0],
                "location": row[1],
                "description": row[2],
                "price_per_day": row[3],
                "image_url": row[4],
            }
            for row in listings
        ]
    except Exception as e:
        return {"error": str(e)}

# ✅ Ensure Railway sees the correct entry point
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080)



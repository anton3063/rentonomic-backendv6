from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import cloudinary.uploader
import psycopg2

app = FastAPI()

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

conn = psycopg2.connect(
    "postgresql://postgres:UoiETFVckuSWSjGMLjjJnXNLgsUfwFKd@switchback.proxy.rlwy.net:27985/railway"
)

cloudinary.config(
    cloud_name="dzd5v9ggu",
    api_key="815282963778522",
    api_secret="JRXqWrZoY1ibmiPyDWW_TpQ4D4c"
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

        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO listings (title, location, description, price_per_day, image_url)
                    VALUES (%s, %s, %s, %s, %s)
                """, (title, location, description, price_per_day, image_url))

        return {"message": "Listing created successfully", "image_url": image_url}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/listings")
def get_listings():
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT title, location, description, price_per_day, image_url FROM listings ORDER BY id DESC")
                rows = cur.fetchall()
                listings = [
                    {
                        "title": row[0],
                        "location": row[1],
                        "description": row[2],
                        "price_per_day": row[3],
                        "image_url": row[4]
                    }
                    for row in rows
                ]
        return listings
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})






from fastapi import FastAPI, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uuid
import psycopg2
import cloudinary
import cloudinary.uploader

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

# Cloudinary setup
cloudinary.config(
    cloud_name="dzb5y4kig",
    api_key="496265773265798",
    api_secret="7zTnNhdPTAlxl9sdtslBAhsu7XA"
)

# PostgreSQL setup
conn = psycopg2.connect("postgresql://postgresanthony_user:pGgZJxg32gWiUgFshwpFVleNw3RwcLxs@dpg-d1lafv7diees73fefak0-a.oregon-postgres.render.com/postgresanthony")
cursor = conn.cursor()

@app.get("/")
def read_root():
    return {"message": "Rentonomic backend is live"}

@app.get("/listings")
def get_listings():
    try:
        cursor.execute("SELECT * FROM listings")
        rows = cursor.fetchall()
        listings = []
        for row in rows:
            listings.append({
                "id": row[0],
                "name": row[1],
                "location": row[2],
                "description": row[3],
                "price_per_day": row[4],
                "image_url": row[5],
            })
        return listings
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/listing")
async def create_listing(
    title: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: int = Form(...),
    image: UploadFile = Form(...)
):
    try:
        # Upload image to Cloudinary
        result = cloudinary.uploader.upload(image.file)
        image_url = result.get("secure_url")
        if not image_url:
            raise Exception("Image upload failed.")

        # Create UUID
        item_id = str(uuid.uuid4())

        # Insert into database
        cursor.execute(
            "INSERT INTO listings (id, name, location, description, price_per_day, image_url) VALUES (%s, %s, %s, %s, %s, %s)",
            (item_id, title, location, description, price_per_day, image_url)
        )
        conn.commit()

        return {"message": "Listing created successfully!"}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})











































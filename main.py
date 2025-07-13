from fastapi import FastAPI, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uuid
import psycopg2
import cloudinary.uploader

app = FastAPI()

# CORS settings
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

# Cloudinary config
cloudinary.config(
    cloud_name="dkzwvm3hh",
    api_key="538411894574491",
    api_secret="BI_MCFrVICVQZWUzJVYTe1GmWfs"
)

# PostgreSQL config
db_url = "postgresql://postgresanthony_user:pGgZJxg32gWiUgFshwpFVleNw3RwcLxs@dpg-d1lafv7diees73fefak0-a.oregon-postgres.render.com/postgresanthony"
conn = psycopg2.connect(db_url)
cursor = conn.cursor()

@app.post("/list")
async def list_item(
    name: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: int = Form(...),
    image: UploadFile = File(...)
):
    try:
        upload_result = cloudinary.uploader.upload(image.file)
        image_url = upload_result["secure_url"]
        item_id = str(uuid.uuid4())

        cursor.execute("""
            INSERT INTO listings (id, name, location, description, price_per_day, image_url)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (item_id, name, location, description, price_per_day, image_url))
        conn.commit()

        return {"status": "success", "image_url": image_url}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@app.get("/listings")
def get_listings():
    cursor.execute("SELECT id, name, location, description, price_per_day, image_url FROM listings ORDER BY id DESC")
    rows = cursor.fetchall()
    listings = []
    for row in rows:
        listings.append({
            "id": row[0],
            "name": row[1],
            "location": row[2],
            "description": row[3],
            "price_per_day": row[4],
            "image_url": row[5]
        })
    return listings


















































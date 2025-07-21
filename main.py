from fastapi import FastAPI, UploadFile, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uuid
import psycopg2
import httpx
import os
from typing import Optional
from fastapi import status
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# CORS settings
origins = [
    "https://rentonomic.com",
    "https://www.rentonomic.com",
    "https://rentonomic.netlify.app"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Connect to the PostgreSQL database using environment variable
DATABASE_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# Serve static files if needed later
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.post("/list")
async def list_item(
    name: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price_per_day: int = Form(...),
    image_url: str = Form(...),
    email: str = Form(...)
):
    listing_id = str(uuid.uuid4())
    cursor.execute(
        "INSERT INTO listings (id, name, location, description, price_per_day, image_url) VALUES (%s, %s, %s, %s, %s, %s)",
        (listing_id, name, location, description, price_per_day, image_url)
    )
    conn.commit()

    return {"message": "Item listed successfully!"}


@app.get("/listings")
async def get_listings():
    cursor.execute("SELECT * FROM listings ORDER BY name")
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


@app.post("/request-to-rent")
async def request_to_rent(request: Request):
    data = await request.json()
    to_email = data.get("to_email")
    item_name = data.get("item_name")
    rental_dates = data.get("rental_dates")
    message = data.get("message")

    if not to_email or not item_name:
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": "Missing fields"})

    await send_email(to_email, item_name, rental_dates, message)
    return {"message": "Rental request sent successfully!"}


async def send_email(to_email, item_name, rental_dates, message):
    SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")

    email_subject = f"Rental Request for '{item_name}'"
    email_content = f"""
You've received a new rental request on Rentonomic!

Item: {item_name}
Rental Dates: {rental_dates}
Message from renter:
{message}

Please reply to the renter if you'd like to confirm availability.

Thanks for using Rentonomic!
"""

    await httpx.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": "requests@rentonomic.com"},
            "subject": email_subject,
            "content": [{"type": "text/plain", "value": email_content}]
        }
    )



























































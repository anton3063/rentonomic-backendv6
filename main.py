from fastapi import FastAPI, HTTPException, Request, Depends, Form, UploadFile, File, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import psycopg2
import bcrypt
import jwt
import uuid
import os
import requests
import csv
from io import StringIO
from datetime import datetime
import cloudinary
import cloudinary.uploader

# Load environment variables
DATABASE_URL = os.environ.get("DATABASE_URL")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
JWT_SECRET = os.environ.get("JWT_SECRET", "secret123")
CLOUD_NAME = os.environ.get("CLOUD_NAME")
CLOUD_API_KEY = os.environ.get("CLOUD_API_KEY")
CLOUD_API_SECRET = os.environ.get("CLOUD_API_SECRET")

cloudinary.config(
    cloud_name=CLOUD_NAME,
    api_key=CLOUD_API_KEY,
    api_secret=CLOUD_API_SECRET
)

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth & DB
security = HTTPBearer()

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
        return payload.get("email")
    except jwt.PyJWTError:
        raise HTTPException(status_code=403, detail="Invalid token")

def verify_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    email = verify_token(credentials)
    if email != "admin@rentonomic.com":
        raise HTTPException(status_code=403, detail="Admin access only")
    return email

class AuthRequest(BaseModel):
    email: str
    password: str

class RentalRequest(BaseModel):
    listing_id: str
    renter_email: str
    dates: list

@app.post("/signup")
def signup(auth: AuthRequest):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = %s", (auth.email,))
    if cur.fetchone():
        raise HTTPException(status_code=400, detail="Email already registered")
    hashed_pw = bcrypt.hashpw(auth.password.encode(), bcrypt.gensalt())
    cur.execute("INSERT INTO users (email, password, signup_date) VALUES (%s, %s, %s)",
                (auth.email, hashed_pw.decode(), datetime.utcnow()))
    conn.commit()
    cur.close()
    conn.close()
    token = jwt.encode({"email": auth.email}, JWT_SECRET, algorithm="HS256")
    return {"token": token}

@app.post("/login")
def login(auth: AuthRequest):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT password FROM users WHERE email = %s", (auth.email,))
    result = cur.fetchone()
    if not result or not bcrypt.checkpw(auth.password.encode(), result[0].encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = jwt.encode({"email": auth.email}, JWT_SECRET, algorithm="HS256")
    return {"token": token}

@app.post("/list")
def list_item(
    name: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price: int = Form(...),
    image: UploadFile = File(...),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    try:
        # Get user email from JWT token
        token = credentials.credentials
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        email = payload.get("email")
        if not email:
            raise HTTPException(status_code=401, detail="Unauthorized")

        # Upload image to Cloudinary
        upload_result = cloudinary.uploader.upload(image.file)
        image_url = upload_result.get("secure_url")

        # Save to database
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO listings (id, name, location, description, price_per_day, image_url, email)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (str(uuid.uuid4()), name, location, description, price, image_url, email))
        conn.commit()
        cur.close()
        conn.close()
        return {"message": "Listing created successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/listings")
def get_listings():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, location, description, price_per_day, image_url FROM listings ORDER BY name ASC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "id": r[0],
            "name": r[1],
            "location": r[2],
            "description": r[3],
            "price": r[4],
            "image_url": r[5]
        }
        for r in rows
    ]

@app.post("/request-to-rent")
def request_to_rent(data: RentalRequest):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT email, name FROM listings WHERE id = %s", (data.listing_id,))
        result = cur.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Listing not found")
        owner_email, item_name = result

        cur.execute("""
            INSERT INTO rental_requests (id, listing_id, renter_email, lister_email, rental_dates, message, request_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            str(uuid.uuid4()),
            data.listing_id,
            data.renter_email,
            owner_email,
            ", ".join(data.dates),
            "Is your item available for rent on this/these days?",
            datetime.utcnow()
        ))
        conn.commit()
        cur.close()
        conn.close()
        return {"message": "Request sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/users")
def get_users(admin: str = Depends(verify_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT email, signup_date FROM users")
    users = [{"email": r[0], "signup_date": r[1], "is_verified": True} for r in cur.fetchall()]
    cur.close()
    conn.close()
    return users

@app.get("/all-listings")
def get_all(admin: str = Depends(verify_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, location, price_per_day, email FROM listings")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"id": r[0], "name": r[1], "location": r[2], "price": r[3], "email": r[4]} for r in rows]

@app.get("/rental-requests")
def get_rental_requests(admin: str = Depends(verify_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT rr.id, rr.listing_id, l.name, rr.renter_email, rr.lister_email, rr.rental_dates, rr.message, rr.request_time
        FROM rental_requests rr
        JOIN listings l ON rr.listing_id = l.id
        ORDER BY rr.request_time DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "id": row[0],
            "listing_id": row[1],
            "listing_name": row[2],
            "renter_email": row[3],
            "lister_email": row[4],
            "rental_dates": row[5],
            "message": row[6],
            "request_time": row[7].isoformat()
        }
        for row in rows
    ]

@app.post("/admin/add-dummy-rental")
def add_dummy_rental(admin: str = Depends(verify_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, email FROM listings LIMIT 1")
    listing = cur.fetchone()
    if not listing:
        raise HTTPException(status_code=404, detail="No listings found")
    listing_id, lister_email = listing

    dummy_email = "renter@example.com"
    cur.execute("SELECT email FROM users WHERE email = %s", (dummy_email,))
    if not cur.fetchone():
        cur.execute("INSERT INTO users (email, password, signup_date) VALUES (%s, %s, %s)",
                    (dummy_email, "test", datetime.utcnow()))

    cur.execute("""
        INSERT INTO rental_requests (id, listing_id, renter_email, lister_email, rental_dates, message, request_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        str(uuid.uuid4()),
        listing_id,
        dummy_email,
        lister_email,
        "2025-08-01, 2025-08-02",
        "Is your item available for rent on this/these days?",
        datetime.utcnow()
    ))
    conn.commit()
    cur.close()
    conn.close()
    return {"message": "Dummy rental added"}

@app.delete("/admin/clear-rentals")
def clear_rentals(admin: str = Depends(verify_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM rental_requests")
    conn.commit()
    cur.close()
    conn.close()
    return {"message": "All rental requests deleted"}

@app.get("/admin/export-users")
def export_users(admin: str = Depends(verify_admin)):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT email, signup_date FROM users")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Email", "Signup Date"])
    for row in rows:
        writer.writerow([row[0], row[1].strftime('%Y-%m-%d %H:%M:%S')])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=users.csv"}
    )






























































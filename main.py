from fastapi import FastAPI, HTTPException, Depends, Form, UploadFile, File, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import psycopg2
import bcrypt
import jwt
import uuid
import os
import csv
import json
import requests  # kept for earlier versions (harmless)
from io import StringIO
from datetime import datetime, date
import cloudinary
import cloudinary.uploader
from typing import Optional, List

# ========= ENV =========
DATABASE_URL = os.environ.get("DATABASE_URL")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")  # optional
JWT_SECRET = os.environ.get("JWT_SECRET", "secret123")
CLOUD_NAME = os.environ.get("CLOUD_NAME")
CLOUD_API_KEY = os.environ.get("CLOUD_API_KEY")
CLOUD_API_SECRET = os.environ.get("CLOUD_API_SECRET")

# --- Stripe (safe import) ---
try:
    import stripe as _stripe
except Exception:
    _stripe = None
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
if _stripe and STRIPE_SECRET_KEY:
    _stripe.api_key = STRIPE_SECRET_KEY

# --- Cloudinary ---
cloudinary.config(
    cloud_name=CLOUD_NAME,
    api_key=CLOUD_API_KEY,
    api_secret=CLOUD_API_SECRET
)

# ========= APP =========
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Netlify + Render
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
security = HTTPBearer()

# ========= DB =========
def get_db_connection():
    # Render Postgres usually needs ?sslmode=require in the URL (already in your env)
    return psycopg2.connect(DATABASE_URL)

# ========= HEALTH =========
@app.get("/health")
def health():
    return {"ok": True}

# ========= AUTH HELPERS =========
def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing auth token")
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid auth token")
    email = payload.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Invalid auth payload")
    return email

def verify_admin(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    email = verify_token(credentials)
    if email != "admin@rentonomic.com":
        raise HTTPException(status_code=403, detail="Admin access only")
    return email

# ========= STRIPE CONNECT HELPER =========
def get_or_create_connect_account(user_email: str) -> str:
    if not _stripe:
        raise HTTPException(status_code=500, detail="Stripe library not installed")
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT stripe_account_id FROM users WHERE email = %s", (user_email,))
    row = cur.fetchone()
    if row and row[0]:
        acct_id = row[0]
        cur.close(); conn.close()
        return acct_id

    acct = _stripe.Account.create(
        type="express",
        country="GB",
        email=user_email,
        capabilities={"card_payments": {"requested": True}, "transfers": {"requested": True}},
        business_type="individual",
    )
    acct_id = acct["id"]

    cur.execute("UPDATE users SET stripe_account_id = %s WHERE email = %s", (acct_id, user_email))
    conn.commit()
    cur.close(); conn.close()
    return acct_id

# ========= MODELS =========
class AuthRequest(BaseModel):
    email: str
    password: str

class RentalRequest(BaseModel):
    listing_id: str
    renter_email: Optional[str] = None   # ignored; we use token
    dates: Optional[List[str]] = None    # legacy support

class CheckoutIn(BaseModel):
    listing_id: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None

class MessageSendIn(BaseModel):
    thread_id: int
    body: str

# ========= AUTH =========
@app.post("/signup")
def signup(auth: AuthRequest):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE email = %s", (auth.email,))
    if cur.fetchone():
        cur.close(); conn.close()
        raise HTTPException(status_code=400, detail="Email already registered")
    hashed_pw = bcrypt.hashpw(auth.password.encode(), bcrypt.gensalt()).decode()
    cur.execute(
        "INSERT INTO users (email, password, signup_date) VALUES (%s, %s, %s)",
        (auth.email, hashed_pw, datetime.utcnow())
    )
    conn.commit()
    cur.close(); conn.close()
    token = jwt.encode({"email": auth.email}, JWT_SECRET, algorithm="HS256")
    return {"token": token}

@app.post("/login")
def login(auth: AuthRequest):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT password FROM users WHERE email = %s", (auth.email,))
    row = cur.fetchone()
    if not row or not bcrypt.checkpw(auth.password.encode(), row[0].encode()):
        cur.close(); conn.close()
        raise HTTPException(status_code=401, detail="Invalid credentials")
    cur.close(); conn.close()
    token = jwt.encode({"email": auth.email}, JWT_SECRET, algorithm="HS256")
    return {"token": token}

# ========= LISTINGS =========
@app.post("/list")
def list_item(
    name: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price: int = Form(...),
    image: UploadFile = File(...),
    user_email: str = Depends(verify_token)
):
    upload_result = cloudinary.uploader.upload(image.file)
    image_url = upload_result.get("secure_url")

    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO listings (id, name, location, description, price_per_day, image_url, email)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (str(uuid.uuid4()), name, location, description, price, image_url, user_email))
    conn.commit()
    cur.close(); conn.close()
    return {"message": "Listing created successfully"}

def _outward_code(loc: Optional[str]) -> str:
    if not loc: return ""
    parts = loc.strip().split()
    return parts[0].upper()

@app.get("/listings")
def get_listings():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT id, name, location, description, price_per_day, image_url
        FROM listings
        ORDER BY name ASC
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()

    out = []
    for r in rows:
        base = r[4] or 0
        out.append({
            "id": r[0],
            "name": r[1],
            "location": _outward_code(r[2]),
            "description": r[3],
            "renter_price": round(float(base) * 1.10, 2),  # renter sees base×1.10
            "image_url": r[5]
        })
    return out

@app.get("/my-listings")
def my_listings(user_email: str = Depends(verify_token)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT id, name, location, description, price_per_day, image_url
        FROM listings
        WHERE email = %s
        ORDER BY name ASC
    """, (user_email,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return [
        {"id": r[0], "name": r[1], "location": r[2], "description": r[3], "price": r[4], "image_url": r[5]}
        for r in rows
    ]

@app.delete("/delete-listing/{listing_id}")
def delete_listing(listing_id: str, user_email: str = Depends(verify_token)):
    """
    Normal users: delete their own listing.
    Admin: can delete any listing by id.
    """
    conn = get_db_connection(); cur = conn.cursor()
    if user_email == "admin@rentonomic.com":
        cur.execute("DELETE FROM listings WHERE id = %s", (listing_id,))
    else:
        cur.execute("DELETE FROM listings WHERE id = %s AND email = %s", (listing_id, user_email))
    deleted = cur.rowcount
    conn.commit(); cur.close(); conn.close()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Listing not found or not owned by you")
    return {"message": "Listing deleted"}

@app.patch("/edit-listing")
def edit_listing(
    listing_id: str = Form(...),
    name: str = Form(...),
    location: str = Form(...),
    description: str = Form(...),
    price: int = Form(...),
    image: UploadFile = File(None),
    user_email: str = Depends(verify_token)
):
    conn = get_db_connection(); cur = conn.cursor()
    if image:
        upload_result = cloudinary.uploader.upload(image.file)
        image_url = upload_result.get("secure_url")
        cur.execute("""
            UPDATE listings
            SET name = %s, location = %s, description = %s, price_per_day = %s, image_url = %s
            WHERE id = %s AND email = %s
        """, (name, location, description, price, image_url, listing_id, user_email))
    else:
        cur.execute("""
            UPDATE listings
            SET name = %s, location = %s, description = %s, price_per_day = %s
            WHERE id = %s AND email = %s
        """, (name, location, description, price, listing_id, user_email))
    conn.commit()
    cur.close(); conn.close()
    return {"message": "Listing updated"}

# ========= LEGACY RENTAL REQUESTS =========
@app.get("/rental-history")
def rental_history_query(listing_id: str, user_email: str = Depends(verify_token)):
    return rental_history_path(listing_id, user_email)

@app.get("/rental-history/{listing_id}")
def rental_history_path(listing_id: str, user_email: str = Depends(verify_token)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT renter_email, rental_dates, message, request_time
        FROM rental_requests
        WHERE listing_id = %s AND lister_email = %s
        ORDER BY request_time DESC
    """, (listing_id, user_email))
    rows = cur.fetchall()
    cur.close(); conn.close()

    out = []
    for r in rows:
        dates = r[1] or ""
        start_date, end_date = "", ""
        if dates:
            parts = [p.strip() for p in dates.split(",")]
            if len(parts) >= 1: start_date = parts[0]
            if len(parts) >= 2: end_date = parts[1]
        out.append({
            "renter_email": r[0],
            "rental_dates": dates,
            "start_date": start_date,
            "end_date": end_date,
            "message": r[2],
            "request_time": r[3].isoformat() if r[3] else ""
        })
    return out

@app.post("/request-to-rent")
def request_to_rent(data: RentalRequest, renter_email_from_token: str = Depends(verify_token)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT email, name FROM listings WHERE id = %s", (data.listing_id,))
    listing = cur.fetchone()
    if not listing:
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="Listing not found")
    owner_email, _item_name = listing

    dates_str = ""
    if data.dates and isinstance(data.dates, list) and len(data.dates) > 0:
        dates_str = ", ".join(data.dates)

    cur.execute("""
        INSERT INTO rental_requests (id, listing_id, renter_email, lister_email, rental_dates, message, request_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        str(uuid.uuid4()), data.listing_id, renter_email_from_token, owner_email,
        dates_str, "Is your item available for rent on this/these days?", datetime.utcnow()
    ))
    conn.commit()
    cur.close(); conn.close()
    return {"message": "Request sent"}

@app.post("/request-rental")
def request_rental_form(
    listing_id: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    renter_email_from_token: str = Depends(verify_token)
):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT email, name FROM listings WHERE id = %s", (listing_id,))
    listing = cur.fetchone()
    if not listing:
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="Listing not found")
    owner_email, _item_name = listing

    dates_str = f"{start_date}, {end_date}"

    cur.execute("""
        INSERT INTO rental_requests (id, listing_id, renter_email, lister_email, rental_dates, message, request_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        str(uuid.uuid4()), listing_id, renter_email_from_token, owner_email,
        dates_str, "Is your item available for rent on this/these days?", datetime.utcnow()
    ))
    conn.commit()
    cur.close(); conn.close()
    return {"message": "Request sent"}

@app.get("/my-rental-requests")
def get_my_rental_requests(user_email: str = Depends(verify_token)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT rr.id, rr.listing_id, l.name, rr.renter_email, rr.lister_email, rr.rental_dates, rr.message, rr.request_time
        FROM rental_requests rr
        JOIN listings l ON rr.listing_id = l.id
        WHERE rr.lister_email = %s
        ORDER BY rr.request_time DESC
    """, (user_email,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return [
        {
            "id": row[0], "listing_id": row[1], "listing_name": row[2],
            "renter_email": row[3], "lister_email": row[4],
            "rental_dates": row[5], "message": row[6],
            "request_time": row[7].isoformat() if row[7] else ""
        } for row in rows
    ]

# ========= ADMIN =========
@app.get("/users")
def get_users(admin: str = Depends(verify_admin)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT email, signup_date FROM users")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return [{"email": r[0], "signup_date": r[1]} for r in rows]

@app.get("/all-listings")
def get_all(admin: str = Depends(verify_admin)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, name, location, price_per_day, email FROM listings")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return [{"id": r[0], "name": r[1], "location": r[2], "price": r[3], "email": r[4]} for r in rows]

@app.get("/rental-requests")
def get_all_rental_requests(admin: str = Depends(verify_admin)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT rr.id, rr.listing_id, l.name, rr.renter_email, rr.lister_email, rr.rental_dates, rr.message, rr.request_time
        FROM rental_requests rr
        JOIN listings l ON rr.listing_id = l.id
        ORDER BY rr.request_time DESC
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()
    return [
        {
            "id": row[0], "listing_id": row[1], "listing_name": row[2],
            "renter_email": row[3], "lister_email": row[4],
            "rental_dates": row[5], "message": row[6],
            "request_time": row[7].isoformat() if row[7] else ""
        } for row in rows
    ]

@app.post("/admin/add-dummy-rental")
def add_dummy_rental(admin: str = Depends(verify_admin)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, email FROM listings LIMIT 1")
    listing = cur.fetchone()
    if not listing:
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="No listings found")
    listing_id, lister_email = listing

    dummy_email = "renter@example.com"
    cur.execute("SELECT email FROM users WHERE email = %s", (dummy_email,))
    if not cur.fetchone():
        hashed_pw = bcrypt.hashpw(b"test", bcrypt.gensalt()).decode()
        cur.execute("INSERT INTO users (email, password, signup_date) VALUES (%s, %s, %s)",
                    (dummy_email, hashed_pw, datetime.utcnow()))

    cur.execute("""
        INSERT INTO rental_requests (id, listing_id, renter_email, lister_email, rental_dates, message, request_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        str(uuid.uuid4()), listing_id, dummy_email, lister_email,
        "2025-08-01, 2025-08-02", "Is your item available for rent on this/these days?", datetime.utcnow()
    ))
    conn.commit()
    cur.close(); conn.close()
    return {"message": "Dummy rental added"}

@app.delete("/admin/clear-rentals")
def clear_rentals(admin: str = Depends(verify_admin)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM rental_requests")
    conn.commit()
    cur.close(); conn.close()
    return {"message": "All rental requests deleted"}

@app.get("/admin/export-users")
def export_users(admin: str = Depends(verify_admin)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT email, signup_date FROM users")
    rows = cur.fetchall()
    cur.close(); conn.close()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Email", "Signup Date"])
    for row in rows:
        dt = row[1].strftime('%Y-%m-%d %H:%M:%S') if row[1] else ""
        writer.writerow([row[0], dt])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=users.csv"}
    )

# --- Admin power tools (optional, handy) ---
@app.delete("/admin/delete-listing/{listing_id}")
def admin_delete_listing(listing_id: str, admin: str = Depends(verify_admin)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        DELETE FROM messages WHERE thread_id IN (
            SELECT id FROM message_threads WHERE listing_id = %s
        )
    """, (listing_id,))
    cur.execute("DELETE FROM message_threads WHERE listing_id = %s", (listing_id,))
    cur.execute("DELETE FROM rentals WHERE listing_id = %s", (listing_id,))
    cur.execute("DELETE FROM rental_requests WHERE listing_id = %s", (listing_id,))
    cur.execute("DELETE FROM listings WHERE id = %s", (listing_id,))
    deleted = cur.rowcount
    conn.commit(); cur.close(); conn.close()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Listing not found")
    return {"message": "Listing and related records deleted"}

@app.delete("/admin/delete-user/{email}")
def admin_delete_user(email: str, admin: str = Depends(verify_admin)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        DELETE FROM messages
        WHERE thread_id IN (
            SELECT id FROM message_threads
            WHERE renter_email = %s OR lister_email = %s
        )
    """, (email, email))
    cur.execute("DELETE FROM message_threads WHERE renter_email = %s OR lister_email = %s", (email, email))
    cur.execute("DELETE FROM rentals WHERE renter_email = %s OR lister_email = %s", (email, email))
    cur.execute("DELETE FROM rental_requests WHERE renter_email = %s OR lister_email = %s", (email, email))
    cur.execute("DELETE FROM listings WHERE email = %s", (email,))
    listings_deleted = cur.rowcount
    cur.execute("DELETE FROM users WHERE email = %s", (email,))
    users_deleted = cur.rowcount
    conn.commit(); cur.close(); conn.close()
    if users_deleted == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "User deleted", "listings_deleted": listings_deleted}

@app.delete("/admin/delete-orphan-listings")
def admin_delete_orphans(admin: str = Depends(verify_admin)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        DELETE FROM messages WHERE thread_id IN (
            SELECT id FROM message_threads WHERE listing_id IN (
                SELECT id FROM listings WHERE email IS NULL OR email = ''
            )
        )
    """)
    cur.execute("""
        DELETE FROM message_threads WHERE listing_id IN (
            SELECT id FROM listings WHERE email IS NULL OR email = ''
        )
    """)
    cur.execute("DELETE FROM rentals WHERE listing_id IN (SELECT id FROM listings WHERE email IS NULL OR email = '')")
    cur.execute("DELETE FROM rental_requests WHERE listing_id IN (SELECT id FROM listings WHERE email IS NULL OR email = '')")
    cur.execute("DELETE FROM listings WHERE email IS NULL OR email = ''")
    deleted = cur.rowcount
    conn.commit(); cur.close(); conn.close()
    return {"message": "Orphan listings deleted", "count": deleted}

# ========= STRIPE: CONNECT ONBOARDING =========
@app.post("/stripe/create-onboarding-link")
def stripe_create_onboarding_link(current_user: str = Depends(verify_token)):
    if not _stripe:
        raise HTTPException(status_code=500, detail="Stripe library not installed")
    if not STRIPE_SECRET_KEY or not STRIPE_PUBLISHABLE_KEY or not FRONTEND_BASE_URL:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    acct_id = get_or_create_connect_account(current_user)
    link = _stripe.AccountLink.create(
        account=acct_id,
        refresh_url=f"{FRONTEND_BASE_URL}/dashboard.html?stripe=refresh",
        return_url=f"{FRONTEND_BASE_URL}/dashboard.html?stripe=return",
        type="account_onboarding",
    )
    return {"url": link["url"], "account_id": acct_id}

# ========= STRIPE: CHECKOUT + WEBHOOK =========
@app.post("/stripe/checkout-session")
def create_checkout_session(payload: CheckoutIn, current_user: str = Depends(verify_token)):
    if not _stripe:
        raise HTTPException(status_code=500, detail="Stripe library not installed")

    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, name, price_per_day, email FROM listings WHERE id = %s", (payload.listing_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="Listing not found")
    listing_id, name, price_per_day, lister_email = row

    if lister_email == current_user:
        cur.close(); conn.close()
        raise HTTPException(status_code=400, detail="You cannot rent your own listing")

    cur.execute("SELECT stripe_account_id FROM users WHERE email = %s", (lister_email,))
    row2 = cur.fetchone()
    if not row2 or not row2[0]:
        cur.close(); conn.close()
        raise HTTPException(status_code=400, detail="Owner has not completed Stripe onboarding")
    destination_acct = row2[0]

    base_pence = int(price_per_day) * 100
    renter_pence = int(round(base_pence * 1.10))
    fee_pence = renter_pence - base_pence

    session = _stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "gbp",
                "unit_amount": renter_pence,
                "product_data": {"name": f"Rent {name}"}
            },
            "quantity": 1
        }],
        success_url=f"{FRONTEND_BASE_URL}/dashboard.html?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{FRONTEND_BASE_URL}/dashboard.html?checkout=cancelled",
        customer_email=current_user,
        payment_intent_data={
            "application_fee_amount": fee_pence,
            "transfer_data": {"destination": destination_acct}
        }
    )

    cur.execute("""
        INSERT INTO rentals (listing_id, renter_email, lister_email,
            start_date, end_date, base_pence, renter_pence, fee_pence,
            stripe_session_id, status)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')
    """, (
        listing_id, current_user, lister_email,
        payload.start_date, payload.end_date,
        base_pence, renter_pence, fee_pence,
        session["id"]
    ))
    conn.commit()
    cur.close(); conn.close()
    return {"url": session["url"]}

@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    if not _stripe:
        raise HTTPException(status_code=500, detail="Stripe library not installed")

    payload_bytes = await request.body()
    sig_header = request.headers.get("Stripe-Signature")

    # Verify when secret exists; otherwise parse loosely (dev)
    if STRIPE_WEBHOOK_SECRET:
        try:
            event = _stripe.Webhook.construct_event(
                payload_bytes.decode("utf-8"), sig_header, STRIPE_WEBHOOK_SECRET
            )
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid webhook signature")
        etype = event.get("type")
        data = event.get("data", {}).get("object", {})
    else:
        try:
            raw = json.loads(payload_bytes.decode("utf-8"))
        except Exception:
            raw = {}
        etype = raw.get("type")
        data = raw.get("data", {}).get("object", {}) if isinstance(raw, dict) else {}

    conn = get_db_connection(); cur = conn.cursor()

    if etype == "checkout.session.completed":
        sid = data.get("id"); pi = data.get("payment_intent")
        cur.execute("""
            UPDATE rentals
            SET status='paid', stripe_payment_intent_id=%s
            WHERE stripe_session_id=%s
            RETURNING id, listing_id, renter_email, lister_email
        """, (pi, sid))
        row = cur.fetchone()
        if row:
            rid, lid, renter, lister = row
            cur.execute("""
                INSERT INTO message_threads (rental_id, listing_id, renter_email, lister_email, is_unlocked)
                VALUES (%s,%s,%s,%s,TRUE)
                ON CONFLICT DO NOTHING
            """, (rid, str(lid), renter, lister))
    elif etype in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        sid = data.get("id")
        new_status = "expired" if etype.endswith("expired") else "canceled"
        cur.execute("UPDATE rentals SET status=%s WHERE stripe_session_id=%s", (new_status, sid))

    conn.commit(); cur.close(); conn.close()
    return {"received": True}

# ========= MESSAGING =========
@app.get("/messages/thread/{listing_id}")
def get_thread(listing_id: str, current_user: str = Depends(verify_token)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT t.id, t.is_unlocked, t.renter_email, t.lister_email
        FROM message_threads t
        WHERE t.listing_id = %s AND t.is_unlocked=TRUE
          AND (t.renter_email=%s OR t.lister_email=%s)
        ORDER BY t.id DESC LIMIT 1
    """, (listing_id, current_user, current_user))
    thread = cur.fetchone()

    if not thread:
        # If a rental exists but not paid yet, return locked
        cur.execute("""
            SELECT status FROM rentals
            WHERE listing_id=%s AND (renter_email=%s OR lister_email=%s)
            ORDER BY id DESC LIMIT 1
        """, (listing_id, current_user, current_user))
        r = cur.fetchone()
        cur.close(); conn.close()
        if r and r[0] != "paid":
            raise HTTPException(status_code=403, detail="Messaging locked until payment")
        raise HTTPException(status_code=404, detail="No message thread")

    thread_id, _unlocked, renter, lister = thread
    cur.execute("""
        SELECT id, sender_email, body, created_at
        FROM messages
        WHERE thread_id=%s
        ORDER BY created_at ASC
    """, (thread_id,))
    msgs = [{"id": m[0], "sender_email": m[1], "body": m[2], "created_at": m[3].isoformat()} for m in cur.fetchall()]
    cur.close(); conn.close()
    return {"thread_id": thread_id, "renter_email": renter, "lister_email": lister, "messages": msgs}

@app.post("/messages/send")
def send_message(payload: MessageSendIn, current_user: str = Depends(verify_token)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT renter_email, lister_email, is_unlocked FROM message_threads WHERE id=%s", (payload.thread_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="Thread not found")
    renter, lister, unlocked = row
    if not unlocked:
        cur.close(); conn.close()
        raise HTTPException(status_code=403, detail="Messaging locked")
    if current_user not in (renter, lister):
        cur.close(); conn.close()
        raise HTTPException(status_code=403, detail="Not a participant")

    cur.execute("""
        INSERT INTO messages (thread_id, sender_email, body)
        VALUES (%s, %s, %s)
        RETURNING id, created_at
    """, (payload.thread_id, current_user, payload.body))
    mid, created = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    return {"id": mid, "sender_email": current_user, "body": payload.body, "created_at": created.isoformat()}





























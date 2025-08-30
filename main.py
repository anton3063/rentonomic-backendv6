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
import requests
from io import StringIO
from datetime import datetime, date
import cloudinary
import cloudinary.uploader
from typing import Optional, List
import re

# ========= ENV =========
DATABASE_URL = os.environ.get("DATABASE_URL")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
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

# --- SendGrid (optional) ---
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
except Exception:
    SendGridAPIClient = None
    Mail = None

# ========= APP =========
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
security = HTTPBearer()

# ========= DB =========
def get_db_connection():
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
    renter_email: Optional[str] = None
    dates: Optional[List[str]] = None

class CheckoutIn(BaseModel):
    listing_id: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None

class MessageSendIn(BaseModel):
    thread_id: str
    body: str

# ========= MASKING / SANITIZATION =========
EMAIL_RE = re.compile(r'([a-zA-Z0-9._%+-])([a-zA-Z0-9._%+-]*)(@[^@\s]+)')
URL_RE = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)
PHONE_RE = re.compile(r'(\+?\s?44\s?|\(?0\)?\s?)?(\d[\s\-\(\)]?){9,12}')
UK_POSTCODE_RE = re.compile(r'\b([A-Z]{1,2}\d{1,2}[A-Z]?)\s*\d[A-Z]{2}\b', re.IGNORECASE)
ADDRESS_WORDS = r'(street|st|road|rd|avenue|ave|lane|ln|close|cl|drive|dr|way|court|ct|place|pl|terrace|ter|crescent|cres|house|flat|apt|apartment|block|unit)'
ADDRESS_RE = re.compile(rf'\b\d+\s+\w+(?:\s+{ADDRESS_WORDS})\b', re.IGNORECASE)

def mask_email_display(email: str) -> str:
    try:
        local, domain = email.split('@', 1)
    except ValueError:
        return '********'
    if not local:
        return f'****@{domain}'
    return f'{local[0]}{"*" * max(1, len(local)-1)}@{domain}'

def sanitize_message_for_unpaid(text: str) -> str:
    t = text
    t = re.sub(r'[a-zA-Z0-9._%+-]+@[^@\s]+', '[email hidden until payment]', t)
    t = re.sub(
        r'\b([A-Za-z0-9._%-]+)\s*(dot|\.)\s*([A-Za-z0-9._%-]+)\s*(at|@)\s*([A-Za-z0-9._%-]+)\s*(dot|\.)\s*([A-Za-z]{2,})\b',
        '[email hidden until payment]', t, flags=re.IGNORECASE
    )
    t = PHONE_RE.sub('[phone hidden until payment]', t)
    t = URL_RE.sub('[link hidden until payment]', t)
    def _post_to_outcode(m):
        out = m.group(1).upper()
        return f'{out} [full postcode hidden until payment]'
    t = UK_POSTCODE_RE.sub(_post_to_outcode, t)
    t = ADDRESS_RE.sub('[address hidden until payment]', t)
    return t

def maybe_sanitize(text: str, is_unlocked: bool) -> str:
    return text if is_unlocked else sanitize_message_for_unpaid(text)

# ========= EMAIL NOTIFY =========
def _notify_new_message(to_email: str, from_email: str, thread_id: str, preview: str, is_unlocked: bool):
    if not (SENDGRID_API_KEY and SendGridAPIClient and Mail):
        return
    masked_from = mask_email_display(from_email)
    status_line = "Messaging is unlocked for this rental." if is_unlocked else \
                  "Phone, email, and addresses are hidden until payment."
    body = f"""
You have a new message from {masked_from}.

Preview:
{preview}

{status_line}

Sign in to view and reply:
https://rentonomic.com/dashboard.html#thread={thread_id}
"""
    message = Mail(
        from_email="alert@rentonomic.com",
        to_emails=to_email,
        subject="New message on Rentonomic",
        plain_text_content=body
    )
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        sg.send(message)
    except Exception:
        pass

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
            "renter_price": round(float(base) * 1.10, 2),
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

# ========= RENTAL HISTORY =========
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

# ========= RENTAL REQUEST =========
@app.post("/request-to-rent")
def request_to_rent(data: RentalRequest, renter_email_from_token: str = Depends(verify_token)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT email, name FROM listings WHERE id = %s", (data.listing_id,))
    listing = cur.fetchone()
    if not listing:
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="Listing not found")
    owner_email, item_name = listing

    dates_str = ""
    if data.dates and isinstance(data.dates, list) and len(data.dates) > 0:
        dates_str = ", ".join(data.dates)

    cur.execute("""
        INSERT INTO rental_requests (id, listing_id, renter_email, lister_email, rental_dates, message, request_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        str(uuid.uuid4()), data.listing_id, renter_email_from_token, owner_email,
        dates_str, "Is your item available for rent on this/these days? I want to rent.", datetime.utcnow()
    ))
    conn.commit()

    # --- New email to lister ---
    if SENDGRID_API_KEY and SendGridAPIClient and Mail:
        masked_renter = mask_email_display(renter_email_from_token)
        body = f"""
{masked_renter} has requested to rent your item "{item_name}".

Message:
Is this item available for rent on these days? I want to rent.

View this request in your dashboard:
https://rentonomic.com/dashboard.html
"""
        try:
            sg = SendGridAPIClient(SENDGRID_API_KEY)
            message = Mail(
                from_email="alert@rentonomic.com",
                to_emails=owner_email,
                subject=f"{masked_renter} has made a rental request",
                plain_text_content=body
            )
            sg.send(message)
        except Exception:
            pass

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

# ========= STRIPE =========
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
        raw = json.loads(payload_bytes.decode("utf-8"))
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
                INSERT INTO message_threads (id, rental_id, listing_id, renter_email, lister_email, is_unlocked, created_at)
                VALUES (%s,%s,%s,%s,%s,TRUE,NOW())
                ON CONFLICT DO NOTHING
            """, (str(uuid.uuid4()), rid, str(lid), renter, lister))
            cur.execute("""
                SELECT id FROM message_threads
                WHERE rental_id=%s AND listing_id=%s AND renter_email=%s AND lister_email=%s
                ORDER BY created_at DESC LIMIT 1
            """, (rid, str(lid), renter, lister))
            tr = cur.fetchone()
            if tr:
                thread_id = tr[0]
                cur.execute("""
                    INSERT INTO messages (thread_id, sender_email, body, is_system)
                    VALUES (%s, %s, %s, TRUE)
                """, (thread_id, 'system@rentonomic.com',
                      "Payment confirmed — messaging with your counterparty is now unlocked."))
    elif etype in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        sid = data.get("id")
        new_status = "expired" if etype.endswith("expired") else "canceled"
        cur.execute("UPDATE rentals SET status=%s WHERE stripe_session_id=%s", (new_status, sid))

    conn.commit(); cur.close(); conn.close()
    return {"received": True}

# ========= MESSAGING =========
@app.get("/messages/threads")
def list_threads(current_user: str = Depends(verify_token)):
    me = current_user
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT t.id, t.listing_id, t.renter_email, t.lister_email, t.is_unlocked,
               COALESCE((
                  SELECT body FROM messages m WHERE m.thread_id = t.id
                  ORDER BY m.created_at DESC LIMIT 1
               ), '') AS last_body
        FROM message_threads t
        WHERE t.renter_email=%s OR t.lister_email=%s
        ORDER BY t.created_at DESC
    """, (me, me))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    items = []
    for r in rows:
        item = dict(zip(cols, r))
        item["counterparty"] = mask_email_display(
            item["lister_email"] if me == item["renter_email"] else item["renter_email"]
        )
        item["last_body"] = maybe_sanitize(item["last_body"] or "", item["is_unlocked"])
        del item["renter_email"]; del item["lister_email"]
        items.append(item)
    cur.close(); conn.close()
    return items

@app.get("/messages/thread/{listing_id}")
def get_thread(listing_id: str, current_user: str = Depends(verify_token)):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT t.id, t.is_unlocked, t.renter_email, t.lister_email
        FROM message_threads t
        WHERE t.listing_id = %s AND (t.renter_email=%s OR t.lister_email=%s)
        ORDER BY t.created_at DESC LIMIT 1
    """, (listing_id, current_user, current_user))
    thread = cur.fetchone()

    if not thread:
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

    thread_id, is_unlocked, renter, lister = thread
    cur.execute("""
        SELECT id, sender_email, body, created_at, COALESCE(is_system, FALSE) AS is_system
        FROM messages
        WHERE thread_id=%s
        ORDER BY created_at ASC
    """, (thread_id,))
    msgs_raw = cur.fetchall()
    msgs = []
    for m in msgs_raw:
        mid, sender_email, body, created_at, is_system = m
        msgs.append({
            "id": mid,
            "sender_email": sender_email,
            "sender_display": mask_email_display(sender_email),
            "body": maybe_sanitize(body or "", bool(is_unlocked)),
            "is_system": bool(is_system),
            "created_at": created_at.isoformat() if created_at else ""
        })
    cur.close(); conn.close()
    return {
        "thread_id": thread_id,
        "is_unlocked": bool(is_unlocked),
        "counterparty": mask_email_display(lister if current_user == renter else renter),
        "messages": msgs
    }

@app.post("/messages/send")
def send_message(payload: MessageSendIn, current_user: str = Depends(verify_token)):
    thread_id = str(payload.thread_id)
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT renter_email, lister_email, is_unlocked FROM message_threads WHERE id=%s", (thread_id,))
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

    body = (payload.body or "").strip()
    if not body:
        cur.close(); conn.close()
        raise HTTPException(status_code=400





































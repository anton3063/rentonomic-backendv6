# main.py — Rentonomic API (full rewrite, consolidated)
import os
import uuid
import base64
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, Any, Dict, List

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Request, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
import jwt

# Cloudinary (optional)
import cloudinary
import cloudinary.uploader as cu

# SendGrid
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, MailSettings, ClickTracking

# Stripe
import stripe

# -----------------------------
# Environment
# -----------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

JWT_SECRET = os.getenv("JWT_SECRET", "change-me")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_EXP_MIN = int(os.getenv("JWT_EXP_MIN", "43200"))  # 30 days

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
SENDGRID_FROM = os.getenv("SENDGRID_FROM", "alert@rentonomic.com")

CLOUDINARY_URL = os.getenv("CLOUDINARY_URL")
if CLOUDINARY_URL:
    cloudinary.config(cloudinary_url=CLOUDINARY_URL)

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://rentonomic.netlify.app").rstrip("/")
CURRENCY = os.getenv("CURRENCY", "gbp")
PLATFORM_FEE_PERCENT = float(os.getenv("PLATFORM_FEE_PERCENT", "10"))

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@rentonomic.com").lower()

# -----------------------------
# App & CORS
# -----------------------------
app = FastAPI(title="Rentonomic API", version="13.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
security = HTTPBearer()

# -----------------------------
# DB helpers & migrations
# -----------------------------
def db_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

def migrate():
    with db_conn() as conn, conn.cursor() as cur:
        # users
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            stripe_account_id TEXT,
            is_admin BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(lower(email));")

        # listings
        cur.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id UUID PRIMARY KEY,
            name TEXT,
            location TEXT,
            description TEXT,
            price_per_day INTEGER,
            image_url TEXT,
            owner_id UUID,
            owner_email TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)
        cur.execute("ALTER TABLE listings ADD COLUMN IF NOT EXISTS owner_id UUID;")
        cur.execute("ALTER TABLE listings ADD COLUMN IF NOT EXISTS owner_email TEXT;")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_listings_owner_id ON listings(owner_id);")

        # rentals
        cur.execute("""
        CREATE TABLE IF NOT EXISTS rentals (
            id UUID PRIMARY KEY,
            listing_id UUID NOT NULL,
            renter_id UUID NOT NULL,
            renter_email TEXT NOT NULL,
            days INTEGER NOT NULL DEFAULT 1,
            amount_total INTEGER NOT NULL DEFAULT 0, -- pence
            currency TEXT NOT NULL DEFAULT 'gbp',
            checkout_session_id TEXT,
            payment_intent_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            start_date DATE,
            end_date DATE,
            approved_by_lister BOOLEAN NOT NULL DEFAULT FALSE,
            declined_by_lister BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)
        # Ensure columns exist if table predated
        for col, ddl in [
            ("renter_id", "ALTER TABLE rentals ADD COLUMN IF NOT EXISTS renter_id UUID NOT NULL DEFAULT gen_random_uuid()"),
            ("renter_email", "ALTER TABLE rentals ADD COLUMN IF NOT EXISTS renter_email TEXT"),
            ("days", "ALTER TABLE rentals ADD COLUMN IF NOT EXISTS days INTEGER DEFAULT 1"),
            ("amount_total", "ALTER TABLE rentals ADD COLUMN IF NOT EXISTS amount_total INTEGER DEFAULT 0"),
            ("currency", "ALTER TABLE rentals ADD COLUMN IF NOT EXISTS currency TEXT DEFAULT 'gbp'"),
            ("checkout_session_id", "ALTER TABLE rentals ADD COLUMN IF NOT EXISTS checkout_session_id TEXT"),
            ("payment_intent_id", "ALTER TABLE rentals ADD COLUMN IF NOT EXISTS payment_intent_id TEXT"),
            ("status", "ALTER TABLE rentals ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending'"),
            ("start_date", "ALTER TABLE rentals ADD COLUMN IF NOT EXISTS start_date DATE"),
            ("end_date", "ALTER TABLE rentals ADD COLUMN IF NOT EXISTS end_date DATE"),
            ("approved_by_lister", "ALTER TABLE rentals ADD COLUMN IF NOT EXISTS approved_by_lister BOOLEAN DEFAULT FALSE"),
            ("declined_by_lister", "ALTER TABLE rentals ADD COLUMN IF NOT EXISTS declined_by_lister BOOLEAN DEFAULT FALSE"),
        ]:
            try:
                cur.execute(ddl)
            except Exception:
                pass
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rentals_listing ON rentals(listing_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rentals_status ON rentals(status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rentals_session ON rentals(checkout_session_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rentals_created ON rentals(created_at);")

        # message_threads
        cur.execute("""
        CREATE TABLE IF NOT EXISTS message_threads (
            id UUID PRIMARY KEY,
            rental_id UUID NOT NULL,
            renter_id UUID NOT NULL,
            lister_id UUID NOT NULL,
            is_unlocked BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)
        cur.execute("ALTER TABLE message_threads ADD COLUMN IF NOT EXISTS is_unlocked BOOLEAN NOT NULL DEFAULT FALSE;")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_threads_rental ON message_threads(rental_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_threads_parties ON message_threads(renter_id, lister_id);")

        # messages
        cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id UUID PRIMARY KEY,
            thread_id UUID NOT NULL,
            sender_id UUID NOT NULL,
            body TEXT NOT NULL,
            is_system BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)
        cur.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT FALSE;")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);")

        conn.commit()

migrate()

# -----------------------------
# Utilities
# -----------------------------
def hash_password(pw: str) -> str:
    salt = os.getenv("PW_SALT", "rentonomic-salt").encode()
    return base64.b64encode(hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 120000)).decode()

def verify_password(pw: str, hashed: str) -> bool:
    return hash_password(pw) == hashed

def create_token(sub: str, extra: dict | None = None) -> str:
    payload = {"sub": sub, "exp": datetime.utcnow() + timedelta(minutes=JWT_EXP_MIN), "iat": datetime.utcnow()}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)):
    token = creds.credentials
    data = decode_token(token)
    email = data.get("sub")
    if not email:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, email, stripe_account_id, COALESCE(is_admin,false) AS is_admin FROM users WHERE lower(email)=lower(%s)", (email,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="User not found")
        return row

def require_admin(user: Dict[str, Any] = Depends(get_current_user)):
    if (user.get("is_admin") is True) or (user.get("email","").lower() == ADMIN_EMAIL):
        return user
    raise HTTPException(status_code=403, detail="Admin only")

def renter_price(base_price: int) -> int:
    return round(base_price * (1 + PLATFORM_FEE_PERCENT / 100.0))

def gbp_to_pence(amount: float | int) -> int:
    return int(round(float(amount) * 100))

def mask_email(e: str) -> str:
    try:
        name, dom = e.split("@", 1)
        return f"{name[0]}******@{dom}"
    except Exception:
        return e

def listing_public_shape(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": row.get("name"),
        "location": row.get("location"),
        "description": row.get("description") or "",
        "price_per_day": row.get("price_per_day"),
        "renter_price_per_day": renter_price(int(row.get("price_per_day") or 0)),
        "image_url": row.get("image_url") or "",
        "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
    }

# -----------------------------
# Pydantic Models
# -----------------------------
class SignupIn(BaseModel):
    email: EmailStr
    password: str

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class EditListingIn(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    price_per_day: Optional[int] = None
    image_url: Optional[str] = None

class RentRequestIn(BaseModel):
    listing_id: uuid.UUID
    dates: List[str]
    message: Optional[str] = None

class OnboardingIn(BaseModel):
    refresh_url: Optional[str] = None
    return_url: Optional[str] = None

class CheckoutIn(BaseModel):
    listing_id: uuid.UUID
    dates: List[str]
    days: int
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None

class CheckoutOut(BaseModel):
    url: str
    session_id: str

class SendMessageIn(BaseModel):
    body: str

# -----------------------------
# Exception handlers
# -----------------------------
from fastapi.exceptions import RequestValidationError

@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"error": "Invalid input", "details": exc.errors()})

@app.exception_handler(HTTPException)
async def http_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

@app.exception_handler(Exception)
async def generic_handler(request: Request, exc: Exception):
    logging.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"error": "Server error"})

# -----------------------------
# Health
# -----------------------------
@app.get("/health")
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat()}

# -----------------------------
# Me (+ Stripe status)
# -----------------------------
@app.get("/me")
def me(current_user: Dict[str, Any] = Depends(get_current_user)):
    acct_id = current_user.get("stripe_account_id")
    status = None
    if STRIPE_SECRET_KEY and acct_id:
        try:
            acct = stripe.Account.retrieve(acct_id)
            status = {
                "details_submitted": bool(acct.get("details_submitted")),
                "charges_enabled": bool(acct.get("charges_enabled")),
                "payouts_enabled": bool(acct.get("payouts_enabled")),
            }
        except Exception:
            status = None
    return {"email": current_user["email"], "stripe_account_id": acct_id, "stripe_status": status, "is_admin": bool(current_user.get("is_admin"))}

# -----------------------------
# Auth
# -----------------------------
@app.post("/signup")
def signup(body: SignupIn):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE lower(email)=lower(%s)", (body.email,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="Email already registered")
        user_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
            (user_id, body.email, hash_password(body.password)),
        )
        conn.commit()
    return {"token": create_token(body.email)}

@app.post("/login")
def login(body: LoginIn):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, email, password_hash FROM users WHERE lower(email)=lower(%s)", (body.email,))
        row = cur.fetchone()
        if not row or not verify_password(body.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"token": create_token(body.email)}

# -----------------------------
# Listings
# -----------------------------
@app.get("/listings")
def all_listings_public():
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, location, description, price_per_day, image_url, created_at
            FROM listings
            ORDER BY created_at DESC
        """)
        rows = cur.fetchall()
    return [listing_public_shape(r) for r in rows]

@app.get("/all-listings")
def all_listings_with_owner():
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, location, description, price_per_day, image_url, owner_email, created_at
            FROM listings
            ORDER BY created_at DESC
        """)
        return cur.fetchall()

@app.get("/my-listings")
def my_listings(current_user: Dict[str, Any] = Depends(get_current_user)):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, location, description, price_per_day, image_url, owner_email, created_at
            FROM listings
            WHERE owner_id=%s
            ORDER BY created_at DESC
        """, (current_user["id"],))
        return cur.fetchall()

@app.post("/list")
def create_listing(
    name: str = Form(...),
    location: str = Form(...),
    description: str = Form(""),
    price_per_day: int = Form(...),
    image: UploadFile | None = File(None),
    image_url: str | None = Form(None),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    final_url = None
    if image is not None:
        uploaded = cu.upload(image.file, folder="rentonomic/listings")
        final_url = uploaded.get("secure_url")
    elif image_url:
        final_url = image_url
    else:
        raise HTTPException(status_code=400, detail="Image or image_url required")

    listing_id = str(uuid.uuid4())
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO listings (id, name, location, description, price_per_day, image_url, owner_id, owner_email)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id, name, location, description, price_per_day, image_url, owner_email, created_at
        """, (listing_id, name, location, description, price_per_day, final_url, current_user["id"], current_user["email"]))
        row = cur.fetchone()
        conn.commit()
    return {"ok": True, "listing": row}

@app.put("/listings/{listing_id}")
def update_listing(
    listing_id: uuid.UUID = Path(...),
    body: EditListingIn = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    fields, vals = [], []
    for key in ["name", "location", "description", "price_per_day", "image_url"]:
        val = getattr(body, key) if body else None
        if val is not None:
            fields.append(f"{key}=%s"); vals.append(val)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    vals.extend([str(listing_id), current_user["id"]])
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            UPDATE listings
            SET {', '.join(fields)}
            WHERE id=%s AND owner_id=%s
            RETURNING id, name, location, description, price_per_day, image_url, owner_email, created_at
        """, tuple(vals))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Listing not found or not owned by user")
        conn.commit()
    return {"ok": True, "listing": row}

@app.delete("/listings/{listing_id}")
def delete_listing(listing_id: uuid.UUID, current_user: Dict[str, Any] = Depends(get_current_user)):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM listings WHERE id=%s AND owner_id=%s RETURNING id", (str(listing_id), current_user["id"]))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Listing not found or not owned by user")
        conn.commit()
    return {"ok": True}

# -----------------------------
# Admin
# -----------------------------
@app.get("/admin/listings")
def admin_listings(_: Dict[str, Any] = Depends(require_admin)):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, location, description, price_per_day, image_url, owner_email, created_at
            FROM listings
            ORDER BY created_at DESC
        """)
        return cur.fetchall()

@app.delete("/admin/listings/{listing_id}")
def admin_delete_listing(listing_id: uuid.UUID, _: Dict[str, Any] = Depends(require_admin)):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM listings WHERE id=%s RETURNING id", (str(listing_id),))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Listing not found")
        conn.commit()
    return {"ok": True}

@app.get("/admin/all-rental-requests")
def admin_all_rental_requests(_: Dict[str, Any] = Depends(require_admin)):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT
              r.id,
              r.listing_id,
              r.renter_email,
              r.days,
              r.status,
              r.created_at,
              r.start_date,
              r.end_date,
              r.approved_by_lister,
              r.declined_by_lister,
              l.name AS listing_name,
              l.owner_email AS lister_email
            FROM rentals r
            LEFT JOIN listings l ON l.id = r.listing_id
            ORDER BY r.created_at DESC
        """)
        rows = cur.fetchall()
    shaped = []
    for r in rows:
        shaped.append({
            "id": str(r["id"]),
            "listing_id": str(r["listing_id"]) if r.get("listing_id") else None,
            "renter_email": r.get("renter_email"),
            "lister_email": r.get("lister_email"),
            "listing_name": r.get("listing_name"),
            "status": r.get("status"),
            "approved_by_lister": bool(r.get("approved_by_lister")),
            "declined_by_lister": bool(r.get("declined_by_lister")),
            "created_at": r.get("created_at").isoformat() if r.get("created_at") else None,
            "start_date": r.get("start_date").isoformat() if r.get("start_date") else None,
            "end_date": r.get("end_date").isoformat() if r.get("end_date") else None,
            "days": r.get("days"),
        })
    return shaped

# -----------------------------
# Request to Rent
# -----------------------------
@app.post("/request-to-rent")
def request_to_rent(body: RentRequestIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    # Load listing incl owner
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, name, location, price_per_day, owner_email, owner_id
            FROM listings WHERE id=%s
        """, (str(body.listing_id),))
        listing = cur.fetchone()
        if not listing:
            raise HTTPException(status_code=404, detail="Listing not found")

    lister_email = listing.get("owner_email")
    if not lister_email:
        raise HTTPException(status_code=400, detail="Listing has no owner email on record")

    dates_list = body.dates or []
    start_d = min(dates_list) if dates_list else None
    end_d   = max(dates_list) if dates_list else None
    days_val = max(1, len(dates_list or []))
    message_text = body.message or "Is your item available for rent on these dates?"

    # Create or reuse a pending rental
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id FROM rentals
            WHERE listing_id=%s AND renter_id=%s
              AND status='pending' AND checkout_session_id IS NULL
            ORDER BY created_at DESC
            LIMIT 1
        """, (str(listing["id"]), current_user["id"]))
        row = cur.fetchone()
        if row:
            rental_id = row["id"]
            cur.execute("""
                UPDATE rentals
                   SET start_date=%s::date,
                       end_date=%s::date,
                       days=%s
                 WHERE id=%s
            """, (start_d, end_d, days_val, rental_id))
        else:
            rental_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO rentals
                    (id, listing_id, renter_id, renter_email, days, amount_total, currency,
                     checkout_session_id, status, start_date, end_date)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s,
                     %s, 'pending', %s, %s)
            """, (
                rental_id, str(listing["id"]), current_user["id"], current_user["email"],
                days_val, 0, CURRENCY,
                None, start_d, end_d
            ))
        conn.commit()

    # Ensure a message thread + system message
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM message_threads WHERE rental_id=%s", (rental_id,))
        t = cur.fetchone()
        if t:
            thread_id = t["id"]
        else:
            thread_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO message_threads (id, rental_id, renter_id, lister_id)
                VALUES (%s, %s, %s, %s)
            """, (thread_id, rental_id, current_user["id"], listing["owner_id"]))
            msg = "Renter requested dates: " + (", ".join(dates_list) if dates_list else "(none specified)")
            cur.execute("""
                INSERT INTO messages (id, thread_id, sender_id, body, is_system)
                VALUES (%s, %s, %s, %s, TRUE)
            """, (str(uuid.uuid4()), thread_id, listing["owner_id"], msg))
        conn.commit()

    # Email: masked renter + approve link with rental_id
    renter_masked = mask_email(current_user["email"])
    date_str = ", ".join(dates_list) if dates_list else "(no dates provided)"
    approve_url = f"{FRONTEND_URL}/dashboard.html?approve={rental_id}"

    subject = f"New rental request — {listing['name']}"
    plain = (
        "Hi,\n\n"
        f"You’ve received a rental request for '{listing['name']}' ({listing['location']}).\n\n"
        f"Renter: {renter_masked}\n"
        f"Dates: {date_str}\n"
        f"Message: {message_text}\n\n"
        f"Approve/decline here: {approve_url}\n\n"
        "— Rentonomic"
    )
    html_body = f"""
    <p>Hi,</p>
    <p>You’ve received a rental request for <strong>{listing['name']}</strong> ({listing['location']}).</p>
    <p><strong>Renter:</strong> {renter_masked}<br/>
       <strong>Dates:</strong> {date_str}<br/>
       <strong>Message:</strong> {message_text}</p>
    <p>
      <a href="{approve_url}" style="background:#16a34a;color:#fff;padding:10px 14px;border-radius:6px;text-decoration:none;">
        Open in Rentonomic to Approve/Decline
      </a>
    </p>
    <p style="color:#666;font-size:12px;margin-top:16px;">
      If the button doesn’t open, copy and paste this link:<br/>
      {approve_url}
    </p>
    """

    email_sent = False
    if SENDGRID_API_KEY:
        try:
            sg = SendGridAPIClient(SENDGRID_API_KEY)
            mail = Mail(
                from_email=SENDGRID_FROM,
                to_emails=lister_email,
                subject=subject,
                plain_text_content=plain,
                html_content=html_body
            )
            ms = MailSettings()
            ms.click_tracking = ClickTracking(False, False)
            mail.mail_settings = ms
            resp = sg.send(mail)
            email_sent = (resp.status_code == 202)
        except Exception as e:
            logging.exception("SendGrid send failed: %s", e)
            # Do not fail the request; the dashboard still has the thread & rental
    else:
        logging.warning("SENDGRID_API_KEY not set; skipping email send")

    return {"ok": True, "sent": email_sent, "rental_id": rental_id, "thread_id": thread_id}

# -----------------------------
# Threads & Messages
# -----------------------------
def can_reply(thread_row: Dict[str, Any]) -> bool:
    return bool(thread_row.get("is_unlocked"))

@app.get("/message-threads")
def message_threads(current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Returns threads visible to the user with minimal info for dashboard.
    """
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT t.id AS thread_id, t.is_unlocked, t.created_at,
                   r.id AS rental_id, r.status, r.start_date, r.end_date,
                   r.renter_id, r.renter_email, l.owner_id, l.owner_email,
                   l.id AS listing_id, l.name AS listing_name, l.location
            FROM message_threads t
            JOIN rentals r ON r.id = t.rental_id
            JOIN listings l ON l.id = r.listing_id
            WHERE t.renter_id=%s OR t.lister_id=%s
            ORDER BY t.created_at DESC
        """, (current_user["id"], current_user["id"]))
        rows = cur.fetchall()

    out = []
    for row in rows:
        if str(current_user["id"]) == str(row["renter_id"]):
            counterpart = mask_email(row.get("owner_email") or "")
            role = "renter"
        else:
            counterpart = mask_email(row.get("renter_email") or "")
            role = "lister"
        out.append({
            "thread_id": str(row["thread_id"]),
            "rental_id": str(row["rental_id"]),
            "listing": {
                "id": str(row["listing_id"]),
                "name": row["listing_name"],
                "location": row["location"],
            },
            "status": row["status"],
            "start_date": row["start_date"].isoformat() if row["start_date"] else None,
            "end_date": row["end_date"].isoformat() if row["end_date"] else None,
            "is_unlocked": bool(row["is_unlocked"]),
            "can_reply": bool(row["is_unlocked"]),
            "counterparty": counterpart,
            "role": role,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        })
    return out

@app.get("/threads/{thread_id}")
def get_thread(thread_id: uuid.UUID, current_user: Dict[str, Any] = Depends(get_current_user)):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT t.id, t.rental_id, t.renter_id, t.lister_id, t.is_unlocked,
                   r.status
            FROM message_threads t
            JOIN rentals r ON r.id = t.rental_id
            WHERE t.id=%s
        """, (str(thread_id),))
        t = cur.fetchone()
        if not t:
            raise HTTPException(status_code=404, detail="Thread not found")
        if str(current_user["id"]) not in {str(t["renter_id"]), str(t["lister_id"])}:
            raise HTTPException(status_code=403, detail="Not allowed")

        cur.execute("""
            SELECT id, sender_id, body, is_system, created_at
            FROM messages
            WHERE thread_id=%s
            ORDER BY created_at ASC
        """, (str(thread_id),))
        msgs = cur.fetchall()

    return {
        "thread_id": str(t["id"]),
        "is_unlocked": bool(t["is_unlocked"]),
        "status": t["status"],
        "messages": [{
            "id": str(m["id"]),
            "sender_id": str(m["sender_id"]),
            "body": m["body"],
            "is_system": bool(m["is_system"]),
            "created_at": m["created_at"].isoformat()
        } for m in msgs]
    }

@app.get("/messages")
def messages_alias(thread_id: uuid.UUID = Query(...), current_user: Dict[str, Any] = Depends(get_current_user)):
    # Simple alias to get_thread for convenience
    return get_thread(thread_id, current_user)

@app.post("/threads/{thread_id}/message")
def send_message(thread_id: uuid.UUID, body: SendMessageIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    if not body.body or not body.body.strip():
        raise HTTPException(status_code=400, detail="Message body required")

    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT t.id, t.rental_id, t.renter_id, t.lister_id, t.is_unlocked
            FROM message_threads t
            WHERE t.id=%s
        """, (str(thread_id),))
        t = cur.fetchone()
        if not t:
            raise HTTPException(status_code=404, detail="Thread not found")
        if str(current_user["id"]) not in {str(t["renter_id"]), str(t["lister_id"])}:
            raise HTTPException(status_code=403, detail="Not allowed")
        if not bool(t["is_unlocked"]):
            raise HTTPException(status_code=403, detail="Chat locked until payment")

        cur.execute("""
            INSERT INTO messages (id, thread_id, sender_id, body)
            VALUES (%s, %s, %s, %s)
            RETURNING id, created_at
        """, (str(uuid.uuid4()), str(thread_id), current_user["id"], body.body.strip()))
        m = cur.fetchone()
        conn.commit()

    return {
        "ok": True,
        "message": {
            "id": str(m["id"]),
            "sender_id": str(current_user["id"]),
            "body": body.body.strip(),
            "created_at": m["created_at"].isoformat()
        }
    }

# -----------------------------
# Lister Approve / Decline
# -----------------------------
def _assert_lister_for_rental(rental_id: str, user_id: str):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT r.id, l.owner_id
            FROM rentals r
            JOIN listings l ON l.id = r.listing_id
            WHERE r.id=%s
        """, (rental_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Rental not found")
        if str(row["owner_id"]) != str(user_id):
            raise HTTPException(status_code=403, detail="Only the lister can perform this action")

@app.post("/rentals/{rental_id}/approve")
def approve_rental(rental_id: uuid.UUID, current_user: Dict[str, Any] = Depends(get_current_user)):
    _assert_lister_for_rental(str(rental_id), str(current_user["id"]))
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE rentals
               SET approved_by_lister = TRUE,
                   declined_by_lister = FALSE
             WHERE id=%s
            RETURNING id
        """, (str(rental_id),))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Rental not found")
        conn.commit()
    return {"ok": True, "rental_id": str(rental_id), "approved": True}

@app.post("/rentals/{rental_id}/decline")
def decline_rental(rental_id: uuid.UUID, current_user: Dict[str, Any] = Depends(get_current_user)):
    _assert_lister_for_rental(str(rental_id), str(current_user["id"]))
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE rentals
               SET approved_by_lister = FALSE,
                   declined_by_lister = TRUE
             WHERE id=%s
            RETURNING id
        """, (str(rental_id),))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Rental not found")
        conn.commit()
    return {"ok": True, "rental_id": str(rental_id), "declined": True}

# -----------------------------
# Stripe Connect: onboarding
# -----------------------------
@app.post("/stripe/create-onboarding-link")
def stripe_onboarding_link(body: OnboardingIn | None = None, current_user: Dict[str, Any] = Depends(get_current_user)):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe not configured")

    try:
        with db_conn() as conn, conn.cursor() as cur:
            account_id = current_user.get("stripe_account_id")
            if not account_id:
                acct = stripe.Account.create(
                    type="express",
                    country="GB",
                    email=current_user["email"],
                    business_type="individual",
                    capabilities={"transfers": {"requested": True}},
                    business_profile={"product_description": "Sharing items locally through Rentonomic"},
                    default_currency=CURRENCY,
                )
                account_id = acct["id"]
                cur.execute("UPDATE users SET stripe_account_id=%s WHERE id=%s", (account_id, current_user["id"]))
                conn.commit()

        link = stripe.AccountLink.create(
            account=account_id,
            refresh_url=(body.refresh_url if body and body.refresh_url else f"{FRONTEND_URL}/list.html"),
            return_url=(body.return_url  if body and body.return_url  else f"{FRONTEND_URL}/list.html"),
            type="account_onboarding"
        )
        return {"ok": True, "url": link["url"], "account_id": account_id}

    except Exception as e:
        logging.exception("Stripe onboarding link failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create onboarding link")

# -----------------------------
# Stripe Checkout (90/10 split)
# -----------------------------
@app.post("/stripe/create-checkout-session", response_model=CheckoutOut)
def create_checkout_session(body: CheckoutIn, current_user: Dict[str, Any] = Depends(get_current_user)):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe not configured")
    if body.days < 1:
        raise HTTPException(status_code=400, detail="Days must be at least 1")

    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT l.id, l.name, l.location, l.price_per_day, l.owner_id, u.stripe_account_id
            FROM listings l
            LEFT JOIN users u ON u.id = l.owner_id
            WHERE l.id=%s
        """, (str(body.listing_id),))
        listing = cur.fetchone()
        if not listing:
            raise HTTPException(status_code=404, detail="Listing not found")
        lister_acct = listing.get("stripe_account_id")
        if not lister_acct:
            raise HTTPException(status_code=400, detail="Lister has not completed Stripe onboarding")

        base_per_day = int(listing["price_per_day"])
        renter_per_day = renter_price(base_per_day)
        unit_amount = gbp_to_pence(renter_per_day)
        quantity = int(body.days)
        amount_total = unit_amount * quantity
        app_fee = int(round(amount_total * (PLATFORM_FEE_PERCENT / 100.0)))

        success_url = body.success_url or f"{FRONTEND_URL}/dashboard.html?checkout=success&session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url = body.cancel_url or f"{FRONTEND_URL}/dashboard.html?checkout=cancel"

        try:
            session = stripe.checkout.Session.create(
                mode="payment",
                success_url=success_url,
                cancel_url=cancel_url,
                customer_email=current_user["email"],
                line_items=[{
                    "price_data": {
                        "currency": CURRENCY,
                        "product_data": {
                            "name": f"{listing['name']} — {listing['location']}",
                            "metadata": {
                                "listing_id": str(listing["id"]),
                                "owner_id": str(listing["owner_id"]),
                            }
                        },
                        "unit_amount": unit_amount,
                    },
                    "quantity": quantity
                }],
                payment_intent_data={
                    "application_fee_amount": app_fee,
                    "transfer_data": {"destination": lister_acct},
                    "metadata": {
                        "listing_id": str(listing["id"]),
                        "renter_email": current_user["email"],
                        "days": str(quantity),
                    }
                },
                metadata={
                    "listing_id": str(listing["id"]),
                    "renter_email": current_user["email"],
                    "days": str(quantity),
                    "dates": ",".join(body.dates or []),
                }
            )
        except Exception as e:
            logging.exception("Stripe session create failed: %s", e)
            raise HTTPException(status_code=500, detail="Stripe session creation failed")

        # Reuse pending rental if exists
        with db_conn() as conn2, conn2.cursor() as cur2:
            cur2.execute("""
                SELECT id FROM rentals
                WHERE listing_id=%s AND renter_id=%s
                  AND status='pending' AND checkout_session_id IS NULL
                ORDER BY created_at DESC
                LIMIT 1
            """, (str(listing["id"]), current_user["id"]))
            row = cur2.fetchone()

            if row:
                rental_id = row["id"]
                cur2.execute("""
                    UPDATE rentals
                       SET days=%s,
                           amount_total=%s,
                           currency=%s,
                           checkout_session_id=%s
                     WHERE id=%s
                """, (quantity, amount_total, CURRENCY, session["id"], rental_id))
            else:
                rental_id = str(uuid.uuid4())
                cur2.execute("""
                    INSERT INTO rentals (id, listing_id, renter_id, renter_email, days, amount_total, currency, checkout_session_id, status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending')
                """, (rental_id, str(listing["id"]), current_user["id"], current_user["email"], quantity, amount_total, CURRENCY, session["id"]))
            conn2.commit()

        return {"url": session["url"], "session_id": session["id"]}

# -----------------------------
# Stripe Webhook
# -----------------------------
@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        return JSONResponse(status_code=503, content={"error": "Webhook not configured"})

    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(payload=payload, sig_header=sig_header, secret=STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        logging.warning("Invalid Stripe signature: %s", e)
        return JSONResponse(status_code=400, content={"error": "Invalid signature"})

    try:
        type_ = event.get("type")
        data = event.get("data", {}).get("object", {})

        if type_ == "checkout.session.completed":
            session_id = data.get("id")
            payment_intent_id = data.get("payment_intent")

            meta = data.get("metadata", {}) or {}
            dates_csv = meta.get("dates", "")
            parts = [d.strip() for d in dates_csv.split(",") if d.strip()]
            start_d = min(parts) if parts else None
            end_d = max(parts) if parts else None

            with db_conn() as conn, conn.cursor() as cur:
                cur.execute("""
                    UPDATE rentals
                    SET status='paid',
                        payment_intent_id=%s,
                        start_date = COALESCE(start_date, %s::date),
                        end_date   = COALESCE(end_date,   %s::date)
                    WHERE checkout_session_id=%s
                    RETURNING id, listing_id, renter_id
                """, (payment_intent_id, start_d, end_d, session_id))
                r = cur.fetchone()
                if not r:
                    # graceful no-op
                    conn.commit()
                    return PlainTextResponse("ok", status_code=200)

                rental_id = r["id"]
                listing_id = r["listing_id"]
                renter_id = r["renter_id"]

                # Unlock thread
                cur.execute("SELECT id FROM message_threads WHERE rental_id=%s", (rental_id,))
                t = cur.fetchone()
                if t:
                    cur.execute("UPDATE message_threads SET is_unlocked=TRUE WHERE id=%s", (t["id"],))
                else:
                    # create thread if somehow missing
                    cur.execute("SELECT owner_id FROM listings WHERE id=%s", (listing_id,))
                    row = cur.fetchone()
                    lister_id = row["owner_id"] if row else None
                    thread_id = str(uuid.uuid4())
                    cur.execute("""
                        INSERT INTO message_threads (id, rental_id, renter_id, lister_id, is_unlocked)
                        VALUES (%s, %s, %s, %s, TRUE)
                    """, (thread_id, rental_id, renter_id, lister_id))
                    cur.execute("""
                        INSERT INTO messages (id, thread_id, sender_id, body, is_system)
                        VALUES (%s, %s, %s, %s, TRUE)
                    """, (str(uuid.uuid4()), thread_id, lister_id, "✅ Payment confirmed. You can chat here to arrange pickup and return."))

                conn.commit()

        elif type_ in ("checkout.session.expired", "payment_intent.payment_failed"):
            session_id = data.get("id") if "checkout" in type_ else None
            payment_intent_id = data.get("id") if type_ == "payment_intent.payment_failed" else None
            with db_conn() as conn, conn.cursor() as cur:
                if session_id:
                    cur.execute("UPDATE rentals SET status='failed' WHERE checkout_session_id=%s", (session_id,))
                elif payment_intent_id:
                    cur.execute("UPDATE rentals SET status='failed' WHERE payment_intent_id=%s", (payment_intent_id,))
                conn.commit()

    except Exception as e:
        logging.exception("Webhook handling error: %s", e)
        return JSONResponse(status_code=500, content={"error": "Webhook handler error"})

    return PlainTextResponse("ok", status_code=200)


















































































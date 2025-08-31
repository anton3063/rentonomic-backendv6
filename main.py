# main.py — Rentonomic (FastAPI) — expanded build
# ------------------------------------------------------------
# Features:
# - Auth (signup/login, JWT)
# - Listings: GET /listings, GET /listing/{id}, POST /list (form-data), PATCH /update-listing/{id} (owner), GET /my-listings
# - Rental requests: POST /request-to-rent (JSON), GET /my-rental-requests?role=renter|lister|all
#                    POST /rental-requests/{id}/accept, POST /rental-requests/{id}/decline
# - Messaging: GET /message-threads, GET /messages/{thread_id}, POST /messages/send
#              Threads are LOCKED until payment (webhook unlocks + system message)
# - Admin: GET /admin/all-rental-requests, DELETE /admin/listings/{id} (+ fallback /delete-listing/{id})
# - Stripe: POST /stripe/create-checkout-session, POST /stripe/create-onboarding-link, POST /stripe/webhook
# - SendGrid: best-effort email (never breaks API)
# - CORS: Netlify + rentonomic.com
# ------------------------------------------------------------

import os
from typing import Optional, Union, Dict, Any, List
from datetime import datetime, timedelta, date

from fastapi import (
    FastAPI, Depends, HTTPException, Body, Form, UploadFile, File, Request
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
import bcrypt
import jwt  # PyJWT
import psycopg2
import psycopg2.extras

# SendGrid (optional)
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
except Exception:  # pragma: no cover
    SendGridAPIClient = None
    Mail = None

# Stripe (optional)
try:
    import stripe as stripe_sdk
except Exception:  # pragma: no cover
    stripe_sdk = None

# ---------------------- Config ----------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "")
JWT_SECRET = os.environ.get("JWT_SECRET", "change-me")
JWT_ALG = "HS256"

ADMIN_EMAILS = {"admin@rentonomic.com"}  # golden-rule admin
ALERT_FROM_EMAIL = os.environ.get("ALERT_FROM_EMAIL", "alert@rentonomic.com")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_MODE = os.environ.get("STRIPE_PRICE_MODE", "per_day")  # future use

FRONTEND_BASE = os.environ.get("FRONTEND_BASE", "https://rentonomic.com")

app = FastAPI(title="Rentonomic API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://rentonomic.com",
        "https://www.rentonomic.com",
        "https://rentonomic.netlify.app",
        "http://localhost",
        "http://localhost:5173",
        "*",  # keep broad while iterating
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------- DB helpers ----------------------

def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def fetch_all_dict(cur) -> List[Dict[str, Any]]:
    try:
        rows = cur.fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        out.append({desc.name: val for desc, val in zip(cur.description, r)})
    return out

def fetch_one_dict(cur) -> Optional[Dict[str, Any]]:
    r = cur.fetchone()
    if r is None:
        return None
    return {desc.name: val for desc, val in zip(cur.description, r)}

# ---------------------- Auth helpers ----------------------

def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

def create_access_token(email: str, days: int = 45) -> str:
    payload = {"sub": email, "exp": datetime.utcnow() + timedelta(days=days)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def decode_token(token: str) -> str:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        return payload.get("sub") or ""
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

def bearer_email_from_header(auth_header: Optional[str]) -> str:
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth_header.split(" ", 1)[1].strip()
    return decode_token(token)

def is_admin(email: str) -> bool:
    return (email or "").lower() in ADMIN_EMAILS

def outward_prefix(loc: Optional[str]) -> str:
    if not loc:
        return ""
    # outward postcode only
    first = (loc.split("—")[0]).split("-")[0].split(",")[0].strip().split()
    return (first[0] if first else "").upper()

# FastAPI dependency
def verify_token(authorization: Optional[str] = None) -> str:
    return bearer_email_from_header(authorization)

# ---------------------- Email (best-effort) ----------------------

def send_alert_email(to_email: str, subject: str, html: str) -> None:
    try:
        if not (to_email and SENDGRID_API_KEY and SendGridAPIClient and Mail):
            return
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        msg = Mail(
            from_email=ALERT_FROM_EMAIL,
            to_emails=to_email,
            subject=subject,
            html_content=html,
        )
        sg.send(msg)
    except Exception:
        pass  # never fail the API on email issues

# ---------------------- Models ----------------------

class SignupIn(BaseModel):
    email: EmailStr
    password: str

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class RequestToRentIn(BaseModel):
    listing_id: Union[int, str]
    start_date: date
    end_date: date

class DeclineIn(BaseModel):
    reason: Optional[str] = None

class AcceptIn(BaseModel):
    note: Optional[str] = None  # placeholder for future

class CheckoutStartIn(BaseModel):
    request_id: Union[int, str]

# ---------------------- Auth routes ----------------------

@app.post("/signup")
def signup(payload: SignupIn):
    email = payload.email.lower()
    password_hash = hash_password(payload.password)

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="Email already registered")
        cur.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (%s, %s, NOW())",
            (email, password_hash),
        )
        conn.commit()
        token = create_access_token(email)
        return {"token": token, "email": email}
    finally:
        cur.close(); conn.close()

@app.post("/login")
def login(payload: LoginIn):
    email = payload.email.lower()
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT password_hash FROM users WHERE email=%s", (email,))
        row = cur.fetchone()
        if not row or not verify_password(payload.password, row[0]):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token = create_access_token(email)
        return {"token": token, "email": email}
    finally:
        cur.close(); conn.close()

# ---------------------- Listings ----------------------

@app.get("/listings")
def get_listings():
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, name, description, location, image_url,
                   price_per_day, owner_email, created_at
            FROM listings
            WHERE deleted_at IS NULL
            ORDER BY created_at DESC
        """)
        return fetch_all_dict(cur)
    finally:
        cur.close(); conn.close()

@app.get("/listing/{listing_id}")
def get_listing(listing_id: Union[int, str]):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, name, description, location, image_url,
                   price_per_day, owner_email, created_at
            FROM listings
            WHERE id=%s AND deleted_at IS NULL
        """, (str(listing_id),))
        row = fetch_one_dict(cur)
        if not row:
            raise HTTPException(status_code=404, detail="Listing not found")
        return row
    finally:
        cur.close(); conn.close()

@app.post("/list")
def create_listing(
    name: str = Form(...),
    description: Optional[str] = Form(""),
    location: Optional[str] = Form(""),
    price_per_day: Union[str, float] = Form(...),
    image_url: Optional[str] = Form(""),
    authorization: Optional[str] = None
):
    owner_email = verify_token(authorization)
    loc = outward_prefix(location)
    try:
        price_num = float(str(price_per_day).replace(",", ""))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid price")

    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO listings (name, description, location, price_per_day, image_url, owner_email, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            RETURNING id
        """, (name, description, loc, price_num, image_url, owner_email))
        new_id = cur.fetchone()[0]
        conn.commit()
        return {"ok": True, "id": new_id}
    finally:
        cur.close(); conn.close()

@app.patch("/update-listing/{listing_id}")
def update_listing(
    listing_id: Union[int, str],
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    price_per_day: Optional[str] = Form(None),
    image_url: Optional[str] = Form(None),
    authorization: Optional[str] = None
):
    owner = verify_token(authorization)
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT owner_email FROM listings WHERE id=%s AND deleted_at IS NULL", (str(listing_id),))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Listing not found")
        if row[0] != owner and not is_admin(owner):
            raise HTTPException(status_code=403, detail="Forbidden")

        fields = []
        values = []
        if name is not None: fields.append("name=%s"); values.append(name)
        if description is not None: fields.append("description=%s"); values.append(description)
        if location is not None: fields.append("location=%s"); values.append(outward_prefix(location))
        if price_per_day is not None:
            try: price_num = float(str(price_per_day).replace(",", ""))
            except Exception: raise HTTPException(status_code=400, detail="Invalid price")
            fields.append("price_per_day=%s"); values.append(price_num)
        if image_url is not None: fields.append("image_url=%s"); values.append(image_url)
        if not fields:
            return {"ok": True, "id": str(listing_id), "message": "No changes"}

        values.append(str(listing_id))
        cur.execute(f"UPDATE listings SET {', '.join(fields)} WHERE id=%s", values)
        conn.commit()
        return {"ok": True, "id": str(listing_id)}
    finally:
        cur.close(); conn.close()

@app.get("/my-listings")
def my_listings(authorization: Optional[str] = None):
    owner = verify_token(authorization)
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, name, description, location, image_url,
                   price_per_day, owner_email, created_at
            FROM listings
            WHERE owner_email=%s AND deleted_at IS NULL
            ORDER BY created_at DESC
        """, (owner,))
        return fetch_all_dict(cur)
    finally:
        cur.close(); conn.close()

# ---------------------- Rental Requests ----------------------

def ensure_thread(cur, listing_id: str, renter_email: str, lister_email: str) -> None:
    cur.execute("""
        INSERT INTO message_threads (listing_id, renter_email, lister_email, is_unlocked, created_at)
        VALUES (%s, %s, %s, FALSE, NOW())
        ON CONFLICT (listing_id, renter_email, lister_email) DO NOTHING
    """, (listing_id, renter_email, lister_email))

@app.post("/request-to-rent")
def request_to_rent(payload: RequestToRentIn, authorization: Optional[str] = None):
    renter_email = verify_token(authorization)
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="End date must be on/after start date")

    listing_id = str(payload.listing_id)

    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT id, name, owner_email, price_per_day FROM listings WHERE id=%s AND deleted_at IS NULL", (listing_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Listing not found")
        listing_name = row[1]; lister_email = row[2]; price_per_day = float(row[3] or 0)

        cur.execute("""
            INSERT INTO rental_requests (listing_id, renter_email, lister_email, start_date, end_date, status, request_time)
            VALUES (%s, %s, %s, %s, %s, 'requested', NOW())
            RETURNING id
        """, (listing_id, renter_email, lister_email, payload.start_date, payload.end_date))
        req_id = cur.fetchone()[0]

        ensure_thread(cur, listing_id, renter_email, lister_email)
        conn.commit()

        # Email lister
        star = renter_email[0] if renter_email else ""
        obfuscated = (star + "******@" + renter_email.split("@")[1]) if "@" in renter_email else renter_email
        html = f"""
            <p>You have a new rental request for <strong>{listing_name}</strong>.</p>
            <p>Renter: <strong>{obfuscated}</strong></p>
            <p>Dates: {payload.start_date} → {payload.end_date}</p>
            <p>Please review the request in your dashboard.</p>
        """
        send_alert_email(lister_email, "New rental request", html)

        return {"ok": True, "request_id": req_id, "listing_id": listing_id, "price_per_day": price_per_day}
    finally:
        cur.close(); conn.close()

@app.get("/my-rental-requests")
def my_rental_requests(role: Optional[str] = None, authorization: Optional[str] = None):
    """
    role: renter | lister | all  (default: all for convenience, filtered by current user)
    """
    user = verify_token(authorization)
    role = (role or "all").lower()
    conn = get_db_connection(); cur = conn.cursor()
    try:
        if role == "renter":
            cur.execute("""
                SELECT rr.*, COALESCE(l.name,'') AS listing_name
                FROM rental_requests rr
                LEFT JOIN listings l ON l.id=rr.listing_id
                WHERE rr.renter_email=%s
                ORDER BY rr.request_time DESC
            """, (user,))
        elif role == "lister":
            cur.execute("""
                SELECT rr.*, COALESCE(l.name,'') AS listing_name
                FROM rental_requests rr
                LEFT JOIN listings l ON l.id=rr.listing_id
                WHERE rr.lister_email=%s
                ORDER BY rr.request_time DESC
            """, (user,))
        else:
            cur.execute("""
                SELECT rr.*, COALESCE(l.name,'') AS listing_name
                FROM rental_requests rr
                LEFT JOIN listings l ON l.id=rr.listing_id
                WHERE rr.renter_email=%s OR rr.lister_email=%s
                ORDER BY rr.request_time DESC
            """, (user, user))
        return fetch_all_dict(cur)
    finally:
        cur.close(); conn.close()

@app.post("/rental-requests/{request_id}/accept")
def accept_request(request_id: Union[int, str], payload: AcceptIn = Body(None), authorization: Optional[str] = None):
    actor = verify_token(authorization)
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT rr.*, l.name AS listing_name, l.price_per_day
            FROM rental_requests rr
            LEFT JOIN listings l ON l.id=rr.listing_id
            WHERE rr.id=%s
        """, (str(request_id),))
        r = fetch_one_dict(cur)
        if not r:
            raise HTTPException(status_code=404, detail="Request not found")
        if r["lister_email"] != actor and not is_admin(actor):
            raise HTTPException(status_code=403, detail="Only the lister can accept")

        # Mark accepted (payment still pending)
        cur.execute("UPDATE rental_requests SET status='accepted', updated_at=NOW() WHERE id=%s", (str(request_id),))
        conn.commit()

        # Notify renter
        html = f"""
            <p>Your request for <strong>{r.get('listing_name','')}</strong> was accepted.</p>
            <p>Dates: {r.get('start_date')} → {r.get('end_date')}</p>
            <p>Proceed to payment from your dashboard.</p>
        """
        send_alert_email(r.get("renter_email",""), "Request accepted", html)
        return {"ok": True, "status": "accepted"}
    finally:
        cur.close(); conn.close()

@app.post("/rental-requests/{request_id}/decline")
def decline_request(request_id: Union[int, str], payload: DeclineIn = Body(...), authorization: Optional[str] = None):
    actor = verify_token(authorization)
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT rr.*, COALESCE(l.name,'') AS listing_name
            FROM rental_requests rr
            LEFT JOIN listings l ON l.id=rr.listing_id
            WHERE rr.id=%s
        """, (str(request_id),))
        r = fetch_one_dict(cur)
        if not r:
            raise HTTPException(status_code=404, detail="Request not found")
        if r["lister_email"] != actor and not is_admin(actor):
            raise HTTPException(status_code=403, detail="Only the lister can decline")

        cur.execute("UPDATE rental_requests SET status='declined', updated_at=NOW() WHERE id=%s", (str(request_id),))
        conn.commit()

        reason = (payload.reason or "").strip()
        html = f"""
            <p>The rental request for <strong>{r['listing_name']}</strong> has been declined.</p>
            {('<p><em>Reason:</em> ' + reason + '</p>') if reason else ''}
        """
        send_alert_email(r.get("renter_email",""), "Rental request declined", html)
        send_alert_email(r.get("lister_email",""), "You declined a rental request", html)

        return {"ok": True, "status": "declined"}
    finally:
        cur.close(); conn.close()

# ---------------------- Messaging ----------------------

class MessageSendIn(BaseModel):
    thread_id: Union[int, str]
    body: str

@app.get("/message-threads")
def list_threads(authorization: Optional[str] = None):
    user = verify_token(authorization)
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT mt.id, mt.listing_id, mt.renter_email, mt.lister_email, mt.is_unlocked, mt.created_at,
                   COALESCE(l.name,'') AS listing_name
            FROM message_threads mt
            LEFT JOIN listings l ON l.id=mt.listing_id
            WHERE mt.renter_email=%s OR mt.lister_email=%s
            ORDER BY mt.created_at DESC
        """, (user, user))
        return fetch_all_dict(cur)
    finally:
        cur.close(); conn.close()

@app.get("/messages/{thread_id}")
def list_messages(thread_id: Union[int, str], authorization: Optional[str] = None):
    user = verify_token(authorization)
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT renter_email, lister_email, is_unlocked FROM message_threads WHERE id=%s", (str(thread_id),))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Thread not found")
        renter, lister, unlocked = row[0], row[1], bool(row[2])
        if user not in (renter, lister) and not is_admin(user):
            raise HTTPException(status_code=403, detail="Forbidden")

        cur.execute("""
            SELECT id, thread_id, sender_email, body, created_at, COALESCE(system,false) AS system
            FROM messages
            WHERE thread_id=%s
            ORDER BY created_at ASC
        """, (str(thread_id),))
        return fetch_all_dict(cur)
    finally:
        cur.close(); conn.close()

@app.post("/messages/send")
def send_message(payload: MessageSendIn, authorization: Optional[str] = None):
    user = verify_token(authorization)
    thread_id = str(payload.thread_id).strip()
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Empty message")

    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT renter_email, lister_email, is_unlocked FROM message_threads WHERE id=%s", (thread_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Thread not found")

        renter, lister, unlocked = row[0], row[1], bool(row[2])
        if not unlocked:
            raise HTTPException(status_code=403, detail="Messaging locked")
        if user not in (renter, lister):
            raise HTTPException(status_code=403, detail="Not a participant")

        cur.execute("""
            INSERT INTO messages (thread_id, sender_email, body, created_at, system)
            VALUES (%s, %s, %s, NOW(), FALSE)
        """, (thread_id, user, body))
        conn.commit()
        return {"ok": True}
    finally:
        cur.close(); conn.close()

# ---------------------- Admin ----------------------

@app.get("/admin/all-rental-requests")
def admin_all_rental_requests(authorization: Optional[str] = None):
    admin = verify_token(authorization)
    if not is_admin(admin):
        raise HTTPException(status_code=403, detail="Admin only")
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT rr.id, rr.listing_id, COALESCE(l.name,'') AS listing_name,
                   rr.renter_email, rr.lister_email, rr.start_date, rr.end_date,
                   COALESCE(rr.status,'') AS status, COALESCE(rr.request_time,NOW()) AS request_time
            FROM rental_requests rr
            LEFT JOIN listings l ON l.id = rr.listing_id
            ORDER BY rr.request_time DESC
        """)
        return fetch_all_dict(cur)
    finally:
        cur.close(); conn.close()

@app.delete("/admin/listings/{listing_id}")
def admin_delete_listing(listing_id: Union[int, str], authorization: Optional[str] = None):
    admin = verify_token(authorization)
    if not is_admin(admin):
        raise HTTPException(status_code=403, detail="Admin only")
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT id, name, owner_email FROM listings WHERE id=%s", (str(listing_id),))
        row = fetch_one_dict(cur)
        if not row:
            raise HTTPException(status_code=404, detail="Listing not found")

        owner_email = row.get("owner_email",""); name = row.get("name","")

        # Cascade delete
        cur.execute("DELETE FROM messages WHERE thread_id IN (SELECT id FROM message_threads WHERE listing_id=%s)", (str(listing_id),))
        cur.execute("DELETE FROM message_threads WHERE listing_id=%s", (str(listing_id),))
        cur.execute("DELETE FROM rental_requests WHERE listing_id=%s", (str(listing_id),))
        cur.execute("DELETE FROM rentals WHERE listing_id=%s", (str(listing_id),))
        cur.execute("DELETE FROM listings WHERE id=%s", (str(listing_id),))
        conn.commit()

        if owner_email:
            send_alert_email(owner_email, "Listing removed by moderator",
                             f"<p>Your listing <strong>{name}</strong> was removed for violating our guidelines.</p>")
        return {"ok": True, "deleted_listing_id": str(listing_id)}
    finally:
        cur.close(); conn.close()

@app.delete("/delete-listing/{listing_id}")
def delete_listing_fallback(listing_id: Union[int, str], authorization: Optional[str] = None):
    # Keep admin-only for safety
    admin = verify_token(authorization)
    if not is_admin(admin):
        raise HTTPException(status_code=403, detail="Admin only")
    return admin_delete_listing(listing_id, authorization)

# ---------------------- Stripe ----------------------

@app.post("/stripe/create-onboarding-link")
def stripe_onboard(authorization: Optional[str] = None):
    email = verify_token(authorization)
    if not stripe_sdk or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=409, detail="Stripe not configured")
    stripe_sdk.api_key = STRIPE_SECRET_KEY

    # Minimal example: create account if needed + onboarding link
    # In production, you'd store the connected account id in DB for the lister.
    acct = stripe_sdk.Account.create(type="express", email=email)
    link = stripe_sdk.AccountLink.create(
        account=acct.id,
        refresh_url=f"{FRONTEND_BASE}/dashboard.html?onboarding=refresh",
        return_url=f"{FRONTEND_BASE}/dashboard.html?onboarding=return",
        type="account_onboarding",
    )
    return {"url": link.url, "account_id": acct.id}

@app.post("/stripe/create-checkout-session")
def stripe_checkout_start(payload: CheckoutStartIn, authorization: Optional[str] = None):
    user = verify_token(authorization)
    if not stripe_sdk or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=409, detail="Stripe not configured")
    stripe_sdk.api_key = STRIPE_SECRET_KEY

    # Get request + listing to compute amount (base * days, renter pays base×1.10)
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT rr.id, rr.listing_id, rr.renter_email, rr.lister_email, rr.start_date, rr.end_date,
                   COALESCE(l.name,'') AS listing_name, COALESCE(l.price_per_day,0) AS price_per_day
            FROM rental_requests rr
            LEFT JOIN listings l ON l.id=rr.listing_id
            WHERE rr.id=%s
        """, (str(payload.request_id),))
        r = fetch_one_dict(cur)
        if not r:
            raise HTTPException(status_code=404, detail="Request not found")

        # Only renter can start checkout
        if r["renter_email"] != user and not is_admin(user):
            raise HTTPException(status_code=403, detail="Only the renter can pay")

        # Days (inclusive)
        days = (r["end_date"] - r["start_date"]).days + 1 if r["start_date"] and r["end_date"] else 1
        days = max(1, days)
        base_per_day = float(r.get("price_per_day", 0) or 0)
        base_total = int(round(base_per_day * days * 100))  # pence
        final_total = int(round(base_total * 1.10))  # renter pays +10%
        app_fee = final_total - base_total  # 10%

        # NOTE: for real transfers you need the lister connected account id saved in DB.
        # This example just creates a normal Checkout (money to platform) to keep the build deployable.
        session = stripe_sdk.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "gbp",
                    "product_data": {"name": f"{r['listing_name']} x {days} day(s)"},
                    "unit_amount": final_total,
                },
                "quantity": 1,
            }],
            success_url=f"{FRONTEND_BASE}/dashboard.html?paid={r['id']}",
            cancel_url=f"{FRONTEND_BASE}/dashboard.html?cancel={r['id']}",
            metadata={
                "rental_request_id": str(r["id"]),
                "listing_id": str(r["listing_id"]),
                "renter_email": r["renter_email"],
                "lister_email": r["lister_email"],
                "days": str(days),
                "base_total": str(base_total),
                "platform_fee": str(app_fee),
            },
        )
        # Mark status “payment_initiated” to help UX
        cur.execute("UPDATE rental_requests SET status='payment_initiated', updated_at=NOW() WHERE id=%s", (str(r["id"]),))
        conn.commit()
        return {"url": session.url}
    finally:
        cur.close(); conn.close()

@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    if not stripe_sdk or not STRIPE_WEBHOOK_SECRET or not STRIPE_SECRET_KEY:
        # Accept silently to keep deploy simple
        return {"ok": True, "skipped": "stripe not configured"}

    stripe_sdk.api_key = STRIPE_SECRET_KEY
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe_sdk.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook")

    # Handle successful payments
    if event["type"] in ("checkout.session.completed", "payment_intent.succeeded"):
        meta = event["data"]["object"].get("metadata", {}) if "data" in event and "object" in event["data"] else {}
        req_id = meta.get("rental_request_id")
        listing_id = meta.get("listing_id")
        renter_email = meta.get("renter_email")
        lister_email = meta.get("lister_email")

        if req_id and listing_id and renter_email and lister_email:
            conn = get_db_connection(); cur = conn.cursor()
            try:
                # Mark paid
                cur.execute("UPDATE rental_requests SET status='paid', updated_at=NOW() WHERE id=%s", (str(req_id),))
                # Unlock thread
                cur.execute("""
                    UPDATE message_threads
                    SET is_unlocked=TRUE
                    WHERE listing_id=%s AND renter_email=%s AND lister_email=%s
                """, (str(listing_id), renter_email, lister_email))
                # System message
                cur.execute("""
                    INSERT INTO messages (thread_id, sender_email, body, created_at, system)
                    SELECT id, %s, %s, NOW(), TRUE
                    FROM message_threads
                    WHERE listing_id=%s AND renter_email=%s AND lister_email=%s
                """, ("system@rentonomic", "Payment confirmed. Messaging unlocked.", str(listing_id), renter_email, lister_email))
                conn.commit()
            finally:
                cur.close(); conn.close()

    return {"ok": True}

# ---------------------- Health ----------------------

@app.get("/")
def root():
    return {"ok": True, "service": "rentonomic-api"}









































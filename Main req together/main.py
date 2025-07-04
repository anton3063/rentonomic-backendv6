from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

sample_listings = [
    {
        "id": 1,
        "title": "Cordless Drill",
        "description": "Powerful cordless drill, great for home projects.",
        "location": "YO8",
        "price_per_day": 10,
        "image_url": "https://via.placeholder.com/300x200?text=Drill"
    }
]

@app.get("/")
async def root():
    return {"message": "Rentonomic backend is running"}

@app.get("/listings")
async def get_listings():
    return sample_listings
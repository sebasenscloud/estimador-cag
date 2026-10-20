from fastapi import FastAPI
from app.routers import estimations

app = FastAPI(title="Estimador CAG")

app.include_router(estimations.router)

@app.get("/")
def health():
    return {"status": "ok"}

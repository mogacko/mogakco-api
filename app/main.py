from fastapi import FastAPI

from app.routers.community import router as community_router

app = FastAPI(title="mogakco-api")
app.include_router(community_router)

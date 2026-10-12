from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import engine, Base
from app.api import (
    unsubscribe, auth, workspaces, organizations, markets, businesses,
    research, hypotheses, opportunities, campaigns, claims, senders, outbound, inbound, learning, capacity, discovery,
    leads, drafts
)
import app.models.tenancy  # Ensure tenancy models are registered
import app.models.business  # Ensure business models are registered
import app.models.research  # Ensure research models are registered
import app.models.opportunity  # Ensure opportunity models are registered
import app.models.campaign  # Ensure campaign models are registered
import app.models.outreach  # Ensure outreach models are registered
import app.models.inbound  # Ensure inbound models are registered
import app.models.learning  # Ensure learning models are registered
import app.models.lead      # Ensure lead qualification, draft, and campaign lead models are registered

# Create tables in development/testing mode
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# CORS Middleware
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Include API Routers
app.include_router(auth.router, prefix=settings.API_V1_STR)
app.include_router(workspaces.router, prefix=settings.API_V1_STR)
app.include_router(organizations.router, prefix=settings.API_V1_STR)
app.include_router(markets.router, prefix=settings.API_V1_STR)
app.include_router(discovery.router, prefix=settings.API_V1_STR)
app.include_router(businesses.router, prefix=settings.API_V1_STR)
app.include_router(research.router, prefix=settings.API_V1_STR)
app.include_router(hypotheses.router, prefix=settings.API_V1_STR)
app.include_router(opportunities.router, prefix=settings.API_V1_STR)
app.include_router(campaigns.router, prefix=settings.API_V1_STR)
app.include_router(claims.router, prefix=settings.API_V1_STR)
app.include_router(senders.router, prefix=settings.API_V1_STR)
app.include_router(outbound.router, prefix=settings.API_V1_STR)
app.include_router(inbound.router, prefix=settings.API_V1_STR)
app.include_router(learning.router, prefix=settings.API_V1_STR)
app.include_router(capacity.router, prefix=settings.API_V1_STR)
app.include_router(leads.router, prefix=settings.API_V1_STR)
app.include_router(drafts.router, prefix=settings.API_V1_STR)
app.include_router(unsubscribe.router, prefix=settings.API_V1_STR)

import os
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

class KillSwitchRequest(BaseModel):
    active: bool

@app.get("/api/v1/health")
def health_check():
    return {
        "status": "healthy",
        "project": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "emergency_kill_switch": settings.GLOBAL_EMERGENCY_KILL_SWITCH
    }

@app.post("/api/v1/system/kill-switch")
def set_kill_switch(req: KillSwitchRequest):
    settings.GLOBAL_EMERGENCY_KILL_SWITCH = req.active
    return {
        "status": "success",
        "emergency_kill_switch": settings.GLOBAL_EMERGENCY_KILL_SWITCH
    }

# Mount Frontend Dashboard
frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../frontend"))
if os.path.isdir(frontend_path):
    app.mount("/dashboard", StaticFiles(directory=frontend_path, html=True), name="frontend")

    @app.get("/")
    def root_redirect():
        return RedirectResponse(url="/dashboard/")


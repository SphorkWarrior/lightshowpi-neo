"""
LightShowPi Neo - FastAPI Backend

Main application file for the optional FastAPI backend that provides
web UI, scheduling, analytics, and remote control capabilities.

This backend is OPTIONAL - LightShowPi can run without it using the
traditional CLI/cron/button control methods.
"""

import os
import sys
import logging
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

# Add py directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'py'))

from api.core.database import init_database
from api.core.config import APIConfig, get_api_config
from api.core.auth import AuthManager
from api.models.schemas import (
    UserLogin, Token, SystemHealth, LightshowState
)
from api.routers import lightshow, schedules, buttons
from api.services.lightshow_manager import LightshowManager
from api.services.scheduler import SchedulerService
from api.services.button_manager import ButtonManagerService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)

# Global instances
db = None
auth_manager = None
api_config = None
lightshow_manager = None
scheduler_service = None
button_manager = None

# API version
API_VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle manager for FastAPI app.

    Handles startup and shutdown tasks.
    """
    global db, auth_manager, api_config, lightshow_manager, scheduler_service, button_manager

    # Startup
    log.info("Starting LightShowPi Neo API...")

    # Load configuration
    api_config = get_api_config()

    if not api_config.enabled:
        log.warning("API is not enabled in configuration. Set api.enabled=true to use the API.")
        log.warning("LightShowPi will continue to work in traditional mode.")

    # Initialize database
    db = init_database(api_config.db_path if api_config.db_path else None)
    auth_manager = AuthManager(db)

    # Check if we need to create a default admin user
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]

        if user_count == 0:
            # Create default admin user
            default_password = os.getenv("ADMIN_PASSWORD", "admin123")
            auth_manager.create_user("admin", default_password)
            log.warning(f"Created default admin user with password '{default_password}'")
            log.warning("PLEASE CHANGE THIS PASSWORD IMMEDIATELY!")

    # Initialize lightshow manager
    lightshow_manager = LightshowManager(api_config)
    lightshow.set_manager(lightshow_manager)

    # Initialize button manager
    button_config = {
        'button_manager': {
            'enabled': True,  # Can be overridden by config
            'audio_timeout': 300,
            'cooldown': 5
        }
    }
    button_manager = ButtonManagerService(button_config, lightshow_manager)
    buttons.set_button_manager(button_manager)
    log.info("Button manager service initialized")

    # Initialize and start scheduler
    scheduler_service = SchedulerService(db, lightshow_manager)
    schedules.set_scheduler(scheduler_service)
    scheduler_service.start()
    log.info("Scheduler service started")

    log.info(f"LightShowPi Neo API {API_VERSION} started successfully")

    yield

    # Shutdown
    log.info("Shutting down LightShowPi Neo API...")

    # Stop scheduler
    if scheduler_service:
        scheduler_service.stop()
        log.info("Scheduler service stopped")

    # Stop lightshow if running
    if lightshow_manager:
        lightshow_manager.stop(graceful=True)


# Create FastAPI app
app = FastAPI(
    title="LightShowPi Neo API",
    description="Modern API for controlling LightShowPi synchronized Christmas lights",
    version=API_VERSION,
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(lightshow.router)
app.include_router(schedules.router)
app.include_router(buttons.router)


# Dependency to get database
def get_db():
    """Dependency for getting database instance."""
    global db
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not initialized"
        )
    return db


# Dependency to get auth manager
def get_auth_manager(database = Depends(get_db)):
    """Dependency for getting auth manager."""
    return AuthManager(database)


# ============================================================================
# Authentication Endpoints
# ============================================================================

@app.post("/api/auth/login", response_model=Token, tags=["Authentication"])
async def login(
    credentials: UserLogin,
    auth_mgr: AuthManager = Depends(get_auth_manager)
):
    """
    Authenticate and receive a JWT token.

    **Security:**
    - Passwords are hashed with bcrypt
    - JWT tokens expire after 24 hours
    - Optional IP whitelisting per user
    """
    user = auth_mgr.authenticate_user(
        credentials.username,
        credentials.password
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create access token
    access_token = auth_mgr.create_access_token(
        data={"sub": user['username'], "user_id": user['id']}
    )

    return {"access_token": access_token, "token_type": "bearer"}


# ============================================================================
# Health Check Endpoint
# ============================================================================

@app.get("/api/health", response_model=SystemHealth, tags=["System"])
async def health_check():
    """
    Get system health status.

    This endpoint does not require authentication and can be used
    for monitoring and health checks.
    """
    import time
    start_time = getattr(app.state, 'start_time', time.time())

    return {
        "api_version": API_VERSION,
        "uptime": time.time() - start_time,
        "lightshow_state": LightshowState.IDLE,  # TODO: Get actual state
        "database_ok": db is not None,
        "clients_online": 0  # TODO: Get actual count
    }


# ============================================================================
# Root Endpoint
# ============================================================================

@app.get("/", tags=["Info"])
async def root():
    """
    API information endpoint.
    """
    return {
        "name": "LightShowPi Neo API",
        "version": API_VERSION,
        "docs": "/docs",
        "health": "/api/health"
    }


if __name__ == "__main__":
    import uvicorn

    # Load config to get port
    config = get_api_config()

    if not config.enabled:
        print("ERROR: API is not enabled in configuration")
        print("Set api.enabled=true in config/overrides.cfg to use the API")
        sys.exit(1)

    print(f"Starting LightShowPi Neo API on {config.host}:{config.port}")
    print(f"Docs available at: http://{config.host}:{config.port}/docs")

    uvicorn.run(
        "api.main:app",
        host=config.host,
        port=config.port,
        reload=False,  # Remove in production
        log_level="info"
    )

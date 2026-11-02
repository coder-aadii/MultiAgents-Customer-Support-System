from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.auth import auth_router
from app.api.routes.conversations import conversations_router
from app.api.routes.handoffs import handoffs_router
from app.api.routes.knowledge import knowledge_router
from app.api.routes.store_auth import store_auth_router
from app.api.routes.widget import widget_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(auth_router)
api_v1_router.include_router(widget_router)
api_v1_router.include_router(conversations_router)
api_v1_router.include_router(handoffs_router)
api_v1_router.include_router(knowledge_router)
api_v1_router.include_router(store_auth_router)



@api_v1_router.get("/ping")
async def ping() -> dict[str, str]:
    return {"status": "pong"}

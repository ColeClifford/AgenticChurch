from src.routes.services import members as members_router
from src.routes.services import router as services_router
from src.routes.content import router as content_router
from src.routes.congregation import router as congregation_router
from src.routes.chapel_ws import router as chapel_ws_router
from src.routes.moderation import router as moderation_router
from src.routes.pages import router as pages_router

all_routers = [
    services_router,
    members_router,
    content_router,
    congregation_router,
    chapel_ws_router,
    moderation_router,
    pages_router,
]

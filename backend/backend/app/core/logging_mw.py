import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

class SimpleAccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        resp = await call_next(request)
        ms = int((time.time() - start) * 1000)

        client_id = request.headers.get("X-Client-Id", "")
        plan = getattr(getattr(request.state, "plan", None), "name", "")

        print(f'[access] {resp.status_code} {request.method} {request.url.path} ms={ms} client_id="{client_id}" plan="{plan}"')
        return resp

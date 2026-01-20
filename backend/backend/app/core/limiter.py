from slowapi import Limiter
from slowapi.util import get_remote_address

def key_func(request):
  # Prefer stable identifiers over IP (Cloudflare/Render can collapse IPs)
  api_key = request.headers.get("x-api-key")
  if api_key:
    return f"key:{api_key}"

  client_id = request.headers.get("x-client-id")
  if client_id:
    return f"cid:{client_id}"

  return f"ip:{get_remote_address(request)}"

limiter = Limiter(key_func=key_func)

# You can keep your tier logic if you have it
def tier_limit_provider():
  # default safe limit; adjust per plan later
  return "30/day"

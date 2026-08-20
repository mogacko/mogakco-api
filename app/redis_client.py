import os
from functools import lru_cache

from redis import Redis


@lru_cache
def get_redis_client() -> Redis:
    return Redis.from_url(
        os.environ["REDIS_URL"],
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )

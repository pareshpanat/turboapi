import asyncio
import inspect
from contextlib import asynccontextmanager

@asynccontextmanager
async def timeout(seconds: float):
    if seconds is None or seconds <= 0:
        yield
        return
    async with asyncio.timeout(seconds):
        yield

def is_async_callable(fn):
    return inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(getattr(fn, "__call__", None))

async def run_sync(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)

async def call_callable(fn, *args, **kwargs):
    if is_async_callable(fn):
        return await fn(*args, **kwargs)
    result = await run_sync(fn, *args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result

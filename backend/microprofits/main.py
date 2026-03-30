from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from microprofits.api.rest_client import RestClient
from microprofits.data.store import Store
from microprofits.engine.loop import ScalperLoop
from microprofits.routes import config, heatmap, positions, status, trades, trail


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    store = Store()
    await store.start()

    client = RestClient()
    try:
        await client.start()
    except Exception as e:
        logger.error(f"Failed to authenticate with Capital.com: {e}")
        logger.warning("Bot will start but cannot trade until auth succeeds")

    loop = ScalperLoop(client, store)
    await loop.start()

    app.state.store = store
    app.state.client = client
    app.state.loop = loop

    logger.info("Microprofits started")
    yield

    # Shutdown
    await loop.stop()
    await client.close()
    await store.close()
    logger.info("Microprofits shutdown complete")


app = FastAPI(title="Microprofits", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(status.router)
app.include_router(config.router)
app.include_router(positions.router)
app.include_router(trades.router)
app.include_router(heatmap.router)
app.include_router(trail.router)


@app.get("/health")
async def health():
    return {"status": "ok"}

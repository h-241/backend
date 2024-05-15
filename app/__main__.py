from app.server import fastapi

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(fastapi, host="0.0.0.0", port=8000)

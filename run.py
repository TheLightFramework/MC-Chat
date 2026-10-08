"""Local launcher. Bind only to this computer; use one worker for stream ownership."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("backend.app:app", host="127.0.0.1", port=8765, workers=1)

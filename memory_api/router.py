"""Memory REST routes."""

from fastapi import APIRouter, Request, HTTPException
from memory_api.schemas import MemorySetRequest

router = APIRouter(prefix="/memory", tags=["memory"])


def _get_store(request: Request):
    return request.app.state.memory


@router.post("/set")
def memory_set(body: MemorySetRequest, request: Request):
    """Set a key/value pair in a namespace."""
    store = _get_store(request)
    store.set(
        namespace=body.namespace,
        key=body.key,
        value=body.value,
        source=body.source,
        confidence=body.confidence,
    )
    return {"status": "ok", "namespace": body.namespace, "key": body.key}


@router.get("/get")
def memory_get(namespace: str, key: str, request: Request):
    """Get a single entry by namespace and key."""
    store = _get_store(request)
    entry = store.get(namespace, key)
    if entry is None:
        raise HTTPException(status_code=404, detail="Key not found")
    return entry


@router.get("/list")
def memory_list(namespace: str, request: Request):
    """List all entries in a namespace."""
    store = _get_store(request)
    entries = store.list(namespace)
    return {"namespace": namespace, "count": len(entries), "entries": entries}


@router.delete("/delete")
def memory_delete(namespace: str, key: str, request: Request):
    """Delete a single entry."""
    store = _get_store(request)
    deleted = store.delete(namespace, key)
    if not deleted:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"status": "deleted", "namespace": namespace, "key": key}

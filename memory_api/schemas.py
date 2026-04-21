"""Pydantic models for memory API."""

from pydantic import BaseModel, Field
from typing import Any


class MemorySetRequest(BaseModel):
    namespace: str
    key: str
    value: Any
    source: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class MemoryEntry(BaseModel):
    namespace: str
    key: str
    value: Any
    source: str = ""
    confidence: float = 1.0
    created_at: str = ""
    updated_at: str = ""

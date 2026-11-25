"""Pydantic request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TranscriptionBase(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    language_hint: str | None = Field(default=None, max_length=20)
    duration_seconds: int | None = Field(default=None, ge=0, le=86_400)

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class TranscriptionCreate(TranscriptionBase):
    text: str = Field(min_length=1, max_length=50_000)
    source: str = Field(default="gemini-live", max_length=40)

    @field_validator("text")
    @classmethod
    def _clean_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("text must not be empty")
        return cleaned


class TranscriptionUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, min_length=1, max_length=50_000)
    language_hint: str | None = Field(default=None, max_length=20)
    duration_seconds: int | None = Field(default=None, ge=0, le=86_400)

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("text")
    @classmethod
    def _clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("text must not be empty")
        return cleaned


class TranscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    text: str
    language_hint: str | None
    duration_seconds: int | None
    source: str
    created_at: datetime
    updated_at: datetime


class TranscriptionPage(BaseModel):
    items: list[TranscriptionRead]
    total: int
    page: int
    page_size: int
    pages: int


class HealthResponse(BaseModel):
    status: str = "ok"
    environment: str
    database: str
    active_sessions: int
    models: list[str]

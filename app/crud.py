"""Database access helpers for transcriptions."""

from __future__ import annotations

import math
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas


def default_title(when: datetime | None = None) -> str:
    when = when or datetime.now()
    return when.strftime("Recording %d %b %Y, %H:%M:%S")


async def create_transcription(
    db: AsyncSession, data: schemas.TranscriptionCreate
) -> models.Transcription:
    row = models.Transcription(
        title=data.title or default_title(),
        text=data.text,
        language_hint=data.language_hint,
        duration_seconds=data.duration_seconds,
        source=data.source or "gemini-live",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def get_transcription(
    db: AsyncSession, transcription_id: int
) -> models.Transcription | None:
    return await db.get(models.Transcription, transcription_id)


async def list_transcriptions(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
) -> tuple[list[models.Transcription], int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)

    stmt = select(models.Transcription)
    count_stmt = select(func.count()).select_from(models.Transcription)

    if search:
        pattern = f"%{search.strip().lower()}%"
        condition = or_(
            func.lower(models.Transcription.title).like(pattern),
            func.lower(models.Transcription.text).like(pattern),
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total = (await db.execute(count_stmt)).scalar_one()
    stmt = (
        stmt.order_by(models.Transcription.created_at.desc(), models.Transcription.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return rows, int(total)


async def update_transcription(
    db: AsyncSession, row: models.Transcription, data: schemas.TranscriptionUpdate
) -> models.Transcription:
    payload = data.model_dump(exclude_unset=True)
    for key, value in payload.items():
        setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_transcription(db: AsyncSession, row: models.Transcription) -> None:
    await db.delete(row)
    await db.commit()


def total_pages(total: int, page_size: int) -> int:
    if page_size <= 0:
        return 0
    return math.ceil(total / page_size)

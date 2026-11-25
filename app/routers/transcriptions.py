"""REST CRUD endpoints for saved transcriptions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, schemas
from app.database import get_db

router = APIRouter(prefix="/api/transcriptions", tags=["transcriptions"])


@router.post(
    "",
    response_model=schemas.TranscriptionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a transcription",
)
async def create_transcription(
    payload: schemas.TranscriptionCreate,
    db: AsyncSession = Depends(get_db),
) -> schemas.TranscriptionRead:
    row = await crud.create_transcription(db, payload)
    return schemas.TranscriptionRead.model_validate(row)


@router.get(
    "",
    response_model=schemas.TranscriptionPage,
    summary="List transcriptions with pagination and search",
)
async def list_transcriptions(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: str | None = Query(None, max_length=200),
    db: AsyncSession = Depends(get_db),
) -> schemas.TranscriptionPage:
    rows, total = await crud.list_transcriptions(
        db, page=page, page_size=page_size, search=search
    )
    return schemas.TranscriptionPage(
        items=[schemas.TranscriptionRead.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=crud.total_pages(total, page_size),
    )


@router.get(
    "/{transcription_id}",
    response_model=schemas.TranscriptionRead,
    summary="Read one transcription",
)
async def get_transcription(
    transcription_id: int,
    db: AsyncSession = Depends(get_db),
) -> schemas.TranscriptionRead:
    row = await crud.get_transcription(db, transcription_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Transcription not found")
    return schemas.TranscriptionRead.model_validate(row)


@router.put(
    "/{transcription_id}",
    response_model=schemas.TranscriptionRead,
    summary="Update the title and/or text of a transcription",
)
async def update_transcription(
    transcription_id: int,
    payload: schemas.TranscriptionUpdate,
    db: AsyncSession = Depends(get_db),
) -> schemas.TranscriptionRead:
    row = await crud.get_transcription(db, transcription_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Transcription not found")
    row = await crud.update_transcription(db, row, payload)
    return schemas.TranscriptionRead.model_validate(row)


@router.delete(
    "/{transcription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a transcription",
)
async def delete_transcription(
    transcription_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    row = await crud.get_transcription(db, transcription_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Transcription not found")
    await crud.delete_transcription(db, row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

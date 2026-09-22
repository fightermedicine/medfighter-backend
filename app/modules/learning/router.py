"""Learning module API router (§35–§37)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, Query, UploadFile

from app.common.deps import DbSession
from app.modules.identity.deps import OptionalUser, RequireUser
from app.modules.learning.schemas import (
    AnkiImportRequest,
    CardDueResponse,
    CardReviewRequest,
    CardReviewResponse,
    CardSearchResponse,
    DeckResponse,
    LearningStatsOut,
    QuestionBankOut,
    QuizResultResponse,
    QuizStartOut,
    QuizSubmitRequest,
)
from app.modules.learning.service import (
    get_due_cards_for_deck,
    get_learning_stats,
    get_quiz_for_taking,
    import_anki_deck,
    import_anki_file,
    list_decks,
    list_question_banks,
    review_card,
    search_cards,
    submit_quiz,
)

router = APIRouter(prefix="/learning", tags=["learning"])


@router.get("/stats", response_model=LearningStatsOut)
async def get_stats(
    db: DbSession,
    current_user: RequireUser,
) -> LearningStatsOut:
    """Retrieve live aggregated learning analytics for current student."""
    return await get_learning_stats(db, user_id=current_user.id)


# --- MCQ Quiz Endpoints ---


@router.get("/quizzes", response_model=list[QuestionBankOut])
async def get_quizzes(
    db: DbSession,
    medical_year: int | None = Query(None, ge=1, le=6),
    folder_id: uuid.UUID | None = Query(None),
    current_user: OptionalUser = None,
) -> list[QuestionBankOut]:
    """List active question banks and quiz banks available for study."""
    if current_user:
        is_admin = any(ur.role_id in ("ADMIN", "SUPER_ADMIN") for ur in current_user.roles)
        if not is_admin:
            medical_year = current_user.medical_year
    return await list_question_banks(db, medical_year=medical_year, folder_id=folder_id)


@router.get("/quizzes/{bank_id}", response_model=QuizStartOut)
async def start_quiz(
    bank_id: uuid.UUID,
    db: DbSession,
    current_user: RequireUser,
) -> QuizStartOut:
    """Fetch quiz questions without answers or explanations (Server Authority §35)."""
    return await get_quiz_for_taking(db, bank_id)


@router.post("/quizzes/{bank_id}/submit", response_model=QuizResultResponse)
async def submit_quiz_attempt(
    bank_id: uuid.UUID,
    payload: QuizSubmitRequest,
    db: DbSession,
    current_user: RequireUser,
) -> QuizResultResponse:
    """Grade quiz submission, persist attempt, and return medical rationale."""
    return await submit_quiz(
        db,
        bank_id=bank_id,
        user_id=current_user.id,
        payload=payload,
    )


# --- Flashcard & Spaced Repetition Endpoints ---


@router.get("/decks", response_model=list[DeckResponse])
async def get_decks(
    db: DbSession,
    current_user: RequireUser,
    medical_year: int | None = Query(None, ge=1, le=6),
    folder_id: uuid.UUID | None = Query(None),
) -> list[DeckResponse]:
    """List available decks and cards due for current user."""
    is_admin = any(ur.role_id in ("ADMIN", "SUPER_ADMIN") for ur in current_user.roles)
    if not is_admin:
        medical_year = current_user.medical_year
    return await list_decks(
        db,
        user_id=current_user.id,
        medical_year=medical_year,
        folder_id=folder_id,
    )


@router.get("/decks/{deck_id}/due", response_model=list[CardDueResponse])
async def get_due_cards(
    deck_id: uuid.UUID,
    db: DbSession,
    current_user: RequireUser,
) -> list[CardDueResponse]:
    """Fetch due cards for the given deck for spaced repetition review."""
    return await get_due_cards_for_deck(db, deck_id=deck_id, user_id=current_user.id)


@router.get("/cards/search", response_model=CardSearchResponse)
async def search_learning_cards(
    db: DbSession,
    current_user: RequireUser,
    q: str = Query("", description="Search term across cards, decks, and tags"),
    deck_id: uuid.UUID | None = Query(None),
    medical_year: int | None = Query(None, ge=1, le=6),
    limit: int = Query(100, ge=1, le=500),
) -> CardSearchResponse:
    """Search flashcards across user accessible decks (Anki-style card browser)."""
    is_admin = any(ur.role_id in ("ADMIN", "SUPER_ADMIN") for ur in current_user.roles)
    if not is_admin:
        medical_year = current_user.medical_year
    return await search_cards(
        db=db,
        user_id=current_user.id,
        query=q,
        deck_id=deck_id,
        medical_year=medical_year,
        limit=limit,
    )


@router.post("/cards/{card_id}/review", response_model=CardReviewResponse)
async def submit_card_review(
    card_id: uuid.UUID,
    payload: CardReviewRequest,
    db: DbSession,
    current_user: RequireUser,
) -> CardReviewResponse:
    """Submit rating (1=Again, 2=Hard, 3=Good, 4=Easy) and advance SM-2 interval."""
    return await review_card(
        db,
        card_id=card_id,
        user_id=current_user.id,
        rating=payload.rating,
    )


@router.post("/decks/import-anki", response_model=DeckResponse, status_code=201)
async def import_anki(
    payload: AnkiImportRequest,
    db: DbSession,
    current_user: RequireUser,
) -> DeckResponse:
    """Import Anki deck cards into Fighters spaced repetition system."""
    return await import_anki_deck(db, user_id=current_user.id, payload=payload)


@router.post("/decks/import-file", response_model=DeckResponse, status_code=201)
async def import_anki_upload(
    db: DbSession,
    current_user: RequireUser,
    file: UploadFile = File(...),
    medical_year: int = Form(1),
    folder_id: uuid.UUID | None = Form(None),
    deck_title: str | None = Form(None),
    category: str = Form("Medical"),
) -> DeckResponse:
    """Import Anki package (.apkg, .colpkg, .tsv, .csv, .json) via multipart upload."""
    content = await file.read()
    return await import_anki_file(
        db=db,
        user_id=current_user.id,
        file_bytes=content,
        filename=file.filename or "deck.apkg",
        medical_year=medical_year,
        folder_id=folder_id,
        deck_title=deck_title,
        category=category,
    )


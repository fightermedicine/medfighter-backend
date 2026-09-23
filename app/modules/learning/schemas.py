"""Learning module Pydantic schemas (§35–§37).

Strict server authority: correct answers and explanations are never transmitted
pre-submission in question-taking endpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# --- Question Bank & Quiz Taking Schemas ---


class QuestionBankCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=255)
    description: str = ""
    category: str = Field(default="General Medicine", max_length=64)
    time_limit_seconds: int | None = Field(default=None, ge=10, le=86400)
    pass_percentage: int = Field(default=60, ge=1, le=100)
    exam_mode: str = Field(default="PRACTICE", max_length=32)
    show_explanations: bool = True


class QuestionBankOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    category: str
    medical_year: int = 1
    folder_id: uuid.UUID | None = None
    time_limit_seconds: int | None
    pass_percentage: int
    is_active: bool
    question_count: int
    exam_mode: str = "PRACTICE"
    show_explanations: bool = True
    has_attempted: bool = False
    user_score: int | None = None
    user_percentage: float | None = None
    user_passed: bool | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class OptionPublicOut(BaseModel):
    id: uuid.UUID
    text: str
    order_index: int

    model_config = {"from_attributes": True}


class QuestionPublicOut(BaseModel):
    id: uuid.UUID
    stem: str
    points: int
    order_index: int
    options: list[OptionPublicOut]

    model_config = {"from_attributes": True}


class QuizStartOut(BaseModel):
    bank_id: uuid.UUID
    title: str
    description: str
    category: str
    time_limit_seconds: int | None
    pass_percentage: int
    exam_mode: str = "PRACTICE"
    show_explanations: bool = True
    questions: list[QuestionPublicOut]


# --- Quiz Submission & Grading Schemas ---


class AnswerSubmission(BaseModel):
    question_id: uuid.UUID
    selected_option_id: uuid.UUID | None = None


class QuizSubmitRequest(BaseModel):
    answers: list[AnswerSubmission]


class GradedOptionOut(BaseModel):
    id: uuid.UUID
    text: str
    is_correct: bool
    order_index: int


class GradedQuestionOut(BaseModel):
    question_id: uuid.UUID
    stem: str
    explanation: str
    selected_option_id: uuid.UUID | None
    correct_option_id: uuid.UUID | None
    is_correct: bool
    points_earned: int
    max_points: int
    options: list[GradedOptionOut]


class QuizResultResponse(BaseModel):
    attempt_id: uuid.UUID
    bank_id: uuid.UUID
    bank_title: str
    score: int
    max_score: int
    percentage: float
    passed: bool
    completed_at: datetime
    graded_questions: list[GradedQuestionOut]


class AttemptSummaryResponse(BaseModel):
    attempt_id: uuid.UUID
    bank_id: uuid.UUID
    bank_title: str
    started_at: datetime
    completed_at: datetime | None
    score: int
    max_score: int
    percentage: float
    passed: bool

    model_config = {"from_attributes": True}


# --- Flashcards & Spaced Repetition (SM-2 / FSRS) ---


class DeckCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=255)
    description: str = ""
    category: str = Field(default="Medical", max_length=64)
    medical_year: int = Field(default=1, ge=1, le=5)
    folder_id: uuid.UUID | None = None
    is_public: bool = True


class DeckResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    category: str
    medical_year: int = 1
    folder_id: uuid.UUID | None = None
    is_public: bool
    total_cards: int
    due_cards: int
    created_at: datetime

    model_config = {"from_attributes": True}


class CardCreate(BaseModel):
    front: str = Field(..., min_length=1)
    back: str = Field(..., min_length=1)
    hint: str | None = None
    tags: str | None = None


class CardResponse(BaseModel):
    id: uuid.UUID
    deck_id: uuid.UUID
    front: str
    back: str
    hint: str | None
    tags: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CardDueResponse(BaseModel):
    id: uuid.UUID
    deck_id: uuid.UUID
    front: str
    back: str
    hint: str | None = None
    tags: str | None = None
    interval_days: int
    repetitions: int
    ease_factor: float
    due_date: datetime | None


class CardSearchResult(BaseModel):
    id: uuid.UUID
    deck_id: uuid.UUID
    deck_title: str
    front: str
    back: str
    hint: str | None = None
    tags: str | None = None
    interval_days: int = 1
    repetitions: int = 0
    state: int = 0
    due_date: datetime | None = None


class CardSearchResponse(BaseModel):
    total: int
    cards: list[CardSearchResult]


class CardReviewRequest(BaseModel):
    rating: int = Field(..., ge=1, le=4, description="1=Again, 2=Hard, 3=Good, 4=Easy")


class CardReviewResponse(BaseModel):
    review_id: uuid.UUID
    card_id: uuid.UUID
    rating: int
    next_interval_days: int
    next_due_date: datetime
    ease_factor: float
    repetitions: int


class AnkiCardImportItem(BaseModel):
    front: str
    back: str
    hint: str | None = None
    tags: str | None = None


class AnkiImportRequest(BaseModel):
    deck_title: str = Field(..., min_length=2, max_length=255)
    category: str = Field(default="Medical", max_length=64)
    cards: list[AnkiCardImportItem]


class LearningStatsOut(BaseModel):
    quizzes_attempted: int
    quizzes_passed: int
    average_percentage: float
    cards_reviewed: int
    cards_due: int
    total_cards: int
    total_decks: int

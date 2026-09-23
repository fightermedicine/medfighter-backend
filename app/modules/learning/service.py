"""Learning module business logic (§35–§37).

Enforces:
1. Server-authoritative quiz grading with detailed rationale.
2. Spaced repetition interval scheduling via SM-2.
3. Anki deck ingestion.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ProblemError
from app.modules.curriculum.models import CurriculumFolder
from app.modules.curriculum.service import get_folder_and_descendant_ids
from app.modules.learning.models import (
    AttemptAnswer,
    Card,
    CardReview,
    Deck,
    Question,
    QuestionBank,
    QuizAttempt,
)
from app.modules.learning.schemas import (
    AnkiImportRequest,
    CardDueResponse,
    CardReviewResponse,
    CardSearchResult,
    CardSearchResponse,
    DeckResponse,
    GradedOptionOut,
    GradedQuestionOut,
    LearningStatsOut,
    OptionPublicOut,
    QuestionBankOut,
    QuestionPublicOut,
    QuizResultResponse,
    QuizStartOut,
    QuizSubmitRequest,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


# ---------------------------------------------------------------------------
# Quiz / MCQ Engine
# ---------------------------------------------------------------------------


async def list_question_banks(
    db: AsyncSession,
    medical_year: int | None = None,
    folder_id: uuid.UUID | None = None,
    root_only: bool = False,
    user_id: uuid.UUID | None = None,
) -> list[QuestionBankOut]:
    """List active question banks optionally filtered by medical year and folder."""
    query = (
        select(
            QuestionBank,
            func.count(Question.id).label("question_count"),
        )
        .outerjoin(Question, Question.bank_id == QuestionBank.id)
        .where(QuestionBank.is_active == True)  # noqa: E712
    )

    if medical_year is not None:
        query = query.where(QuestionBank.medical_year == medical_year)
    if folder_id is not None:
        target_ids = await get_folder_and_descendant_ids(db, folder_id)
        query = query.where(QuestionBank.folder_id.in_(target_ids))
    elif root_only:
        query = query.where(QuestionBank.folder_id.is_(None))

    query = query.group_by(QuestionBank.id).order_by(QuestionBank.created_at.desc())
    result = await db.execute(query)
    rows = result.all()

    # If user_id is provided, fetch latest completed attempt per bank for status badge
    user_attempts: dict[uuid.UUID, QuizAttempt] = {}
    if user_id is not None:
        att_stmt = (
            select(QuizAttempt)
            .where(QuizAttempt.user_id == user_id, QuizAttempt.completed_at.is_not(None))
            .order_by(QuizAttempt.completed_at.desc())
        )
        att_res = await db.execute(att_stmt)
        for att in att_res.scalars().all():
            if att.bank_id not in user_attempts:
                user_attempts[att.bank_id] = att

    banks = []
    for bank, count in rows:
        att = user_attempts.get(bank.id)
        banks.append(
            QuestionBankOut(
                id=bank.id,
                title=bank.title,
                description=bank.description,
                category=bank.category,
                medical_year=getattr(bank, "medical_year", 1) or 1,
                folder_id=getattr(bank, "folder_id", None),
                time_limit_seconds=bank.time_limit_seconds,
                pass_percentage=bank.pass_percentage,
                is_active=bank.is_active,
                question_count=count,
                exam_mode=getattr(bank, "exam_mode", "PRACTICE") or "PRACTICE",
                show_explanations=getattr(bank, "show_explanations", True),
                has_attempted=att is not None,
                user_score=att.score if att else None,
                user_percentage=att.percentage if att else None,
                user_passed=att.passed if att else None,
                created_at=bank.created_at,
            )
        )
    return banks


async def get_quiz_for_taking(
    db: AsyncSession, bank_id: uuid.UUID, user_id: uuid.UUID | None = None
) -> QuizStartOut:
    """Fetch question bank for taking.

    CRITICAL SECURITY INVARIANT (§35):
    Neither `is_correct` nor `explanation` are included in the returned payload.
    For OFFICIAL exams: verify user has not completed it previously.
    """
    query = (
        select(QuestionBank)
        .where(QuestionBank.id == bank_id, QuestionBank.is_active == True)  # noqa: E712
        .options(selectinload(QuestionBank.questions).selectinload(Question.options))
    )
    result = await db.execute(query)
    bank = result.scalars().first()
    if not bank:
        raise ProblemError(
            status_code=404, code="not_found", detail="Question bank not found or inactive"
        )

    # Server enforcement for OFFICIAL exam single-attempt invariant
    if getattr(bank, "exam_mode", "PRACTICE") == "OFFICIAL" and user_id is not None:
        prior_att = await db.scalar(
            select(QuizAttempt.id).where(
                QuizAttempt.bank_id == bank_id,
                QuizAttempt.user_id == user_id,
                QuizAttempt.completed_at.is_not(None),
            )
        )
        if prior_att:
            raise ProblemError(
                status_code=403,
                code="exam_already_completed",
                detail="لقد قمت بإجراء هذا الاختبار الرسمي بالفعل، ولا يُسمح بإعادة المحاولة.",
            )

    import random as _random

    questions_out = []
    for q in bank.questions:
        options_out = [
            OptionPublicOut(id=opt.id, text=opt.text, order_index=idx)
            for idx, opt in enumerate(q.options)
        ]
        # Randomize options so the correct answer is not systematically option A
        _random.shuffle(options_out)
        for new_idx, opt_out in enumerate(options_out):
            opt_out.order_index = new_idx

        questions_out.append(
            QuestionPublicOut(
                id=q.id,
                stem=q.stem,
                points=q.points,
                order_index=q.order_index,
                options=options_out,
            )
        )

    return QuizStartOut(
        bank_id=bank.id,
        title=bank.title,
        description=bank.description,
        category=bank.category,
        time_limit_seconds=bank.time_limit_seconds,
        pass_percentage=bank.pass_percentage,
        exam_mode=getattr(bank, "exam_mode", "PRACTICE") or "PRACTICE",
        show_explanations=getattr(bank, "show_explanations", True),
        questions=questions_out,
    )


async def submit_quiz(
    db: AsyncSession,
    bank_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: QuizSubmitRequest,
) -> QuizResultResponse:
    """Grade quiz submission on server, persist attempt, and return explanations."""
    query = (
        select(QuestionBank)
        .where(QuestionBank.id == bank_id)
        .options(selectinload(QuestionBank.questions).selectinload(Question.options))
    )
    result = await db.execute(query)
    bank = result.scalars().first()
    if not bank:
        raise ProblemError(status_code=404, code="not_found", detail="Question bank not found")

    # Strict server enforcement: block second submission on OFFICIAL exams
    if getattr(bank, "exam_mode", "PRACTICE") == "OFFICIAL":
        prior_att = await db.scalar(
            select(QuizAttempt.id).where(
                QuizAttempt.bank_id == bank_id,
                QuizAttempt.user_id == user_id,
                QuizAttempt.completed_at.is_not(None),
            )
        )
        if prior_att:
            raise ProblemError(
                status_code=403,
                code="exam_already_completed",
                detail="لقد قمت بتسليم هذا الاختبار الرسمي مسبقاً، ولا يُسمح بإعادة المحاولة.",
            )

    user_answers = {ans.question_id: ans.selected_option_id for ans in payload.answers}

    total_score = 0
    max_score = sum(q.points for q in bank.questions)
    graded_questions: list[GradedQuestionOut] = []
    attempt_answers: list[AttemptAnswer] = []

    attempt_id = uuid.uuid4()
    now = _utc_now()

    for q in bank.questions:
        selected_opt_id = user_answers.get(q.id)
        correct_opt = next((opt for opt in q.options if opt.is_correct), None)
        correct_opt_id = correct_opt.id if correct_opt else None

        is_correct = bool(selected_opt_id and correct_opt_id and selected_opt_id == correct_opt_id)
        points_earned = q.points if is_correct else 0
        total_score += points_earned

        show_expl = getattr(bank, "show_explanations", True)

        graded_options = [
            GradedOptionOut(
                id=opt.id,
                text=opt.text,
                is_correct=opt.is_correct if show_expl else False,
                order_index=opt.order_index,
            )
            for opt in q.options
        ]

        graded_questions.append(
            GradedQuestionOut(
                question_id=q.id,
                stem=q.stem,
                explanation=q.explanation if show_expl else "",
                selected_option_id=selected_opt_id,
                correct_option_id=correct_opt_id if show_expl else None,
                is_correct=is_correct,
                points_earned=points_earned,
                max_points=q.points,
                options=graded_options,
            )
        )

        attempt_answers.append(
            AttemptAnswer(
                id=uuid.uuid4(),
                attempt_id=attempt_id,
                question_id=q.id,
                selected_option_id=selected_opt_id,
                is_correct=is_correct,
            )
        )

    percentage = round((total_score / max_score * 100), 2) if max_score > 0 else 0.0
    passed = percentage >= bank.pass_percentage

    attempt = QuizAttempt(
        id=attempt_id,
        user_id=user_id,
        bank_id=bank_id,
        started_at=now,
        completed_at=now,
        score=total_score,
        max_score=max_score,
        percentage=percentage,
        passed=passed,
        answers=attempt_answers,
    )
    db.add(attempt)
    await db.commit()

    return QuizResultResponse(
        attempt_id=attempt_id,
        bank_id=bank.id,
        bank_title=bank.title,
        score=total_score,
        max_score=max_score,
        percentage=percentage,
        passed=passed,
        completed_at=now,
        graded_questions=graded_questions,
    )


# ---------------------------------------------------------------------------
# Spaced Repetition (SM-2) & Flashcards
# ---------------------------------------------------------------------------


def calculate_sm2(
    rating: int,
    old_interval: int,
    old_ease: float,
    old_repetitions: int,
) -> tuple[int, float, int, int]:
    """SuperMemo SM-2 interval scheduler.

    rating:
      1 = Again (complete blackout)
      2 = Hard (correct with significant hesitation)
      3 = Good (correct with appropriate effort)
      4 = Easy (instant recall)

    Returns: (interval_days, new_ease_factor, repetitions, state)
    """
    # Ease Factor adjustment
    # EF' = EF + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
    new_ease = old_ease + (0.1 - (5 - rating) * (0.08 + (5 - rating) * 0.02))
    new_ease = max(1.3, round(new_ease, 2))

    if rating == 1:  # Again
        interval = 1
        repetitions = 0
        state = 3  # Relearning
    elif rating == 2:  # Hard
        interval = max(1, int(old_interval * 1.2)) if old_repetitions > 0 else 1
        repetitions = old_repetitions + 1
        state = 2  # Review
    elif rating == 3:  # Good
        if old_repetitions == 0:
            interval = 1
        elif old_repetitions == 1:
            interval = 6
        else:
            interval = max(1, int(old_interval * old_ease))
        repetitions = old_repetitions + 1
        state = 2  # Review
    else:  # Easy
        if old_repetitions == 0:
            interval = 4
        elif old_repetitions == 1:
            interval = 8
        else:
            interval = max(1, int(old_interval * old_ease * 1.3))
        repetitions = old_repetitions + 1
        state = 2  # Review

    return interval, new_ease, repetitions, state


async def list_decks(
    db: AsyncSession,
    user_id: uuid.UUID,
    medical_year: int | None = None,
    folder_id: uuid.UUID | None = None,
    root_only: bool = False,
) -> list[DeckResponse]:
    """List accessible decks with due counts for current user optionally filtered by medical year & folder."""
    total_subq = (
        select(func.count(Card.id))
        .where(Card.deck_id == Deck.id)
        .correlate(Deck)
        .scalar_subquery()
    )
    deck_query = (
        select(Deck, total_subq.label("total_cards"))
        .where((Deck.is_public == True) | (Deck.user_id == user_id))  # noqa: E712
    )
    if medical_year is not None:
        deck_query = deck_query.where(Deck.medical_year == medical_year)
    if folder_id is not None:
        target_ids = await get_folder_and_descendant_ids(db, folder_id)
        deck_query = deck_query.where(Deck.folder_id.in_(target_ids))
    elif root_only:
        deck_query = deck_query.where(Deck.folder_id.is_(None))

    deck_query = deck_query.order_by(Deck.created_at.desc())
    results = (await db.execute(deck_query)).all()
    if not results:
        return []

    deck_ids = [d.id for d, _ in results]
    now = _utc_now()

    latest_rev_subq = (
        select(
            CardReview.card_id,
            CardReview.due_date,
            func.row_number()
            .over(
                partition_by=CardReview.card_id,
                order_by=CardReview.reviewed_at.desc(),
            )
            .label("rn"),
        )
        .where(CardReview.user_id == user_id)
        .subquery()
    )

    due_stmt = (
        select(Card.deck_id, func.count(Card.id))
        .outerjoin(
            latest_rev_subq,
            (latest_rev_subq.c.card_id == Card.id) & (latest_rev_subq.c.rn == 1),
        )
        .where(
            Card.deck_id.in_(deck_ids),
            (latest_rev_subq.c.card_id.is_(None)) | (latest_rev_subq.c.due_date <= now),
        )
        .group_by(Card.deck_id)
    )
    due_counts = dict((await db.execute(due_stmt)).all())

    return [
        DeckResponse(
            id=d.id,
            title=d.title,
            description=d.description,
            category=d.category,
            medical_year=getattr(d, "medical_year", 1) or 1,
            folder_id=getattr(d, "folder_id", None),
            is_public=d.is_public,
            total_cards=total_cards or 0,
            due_cards=due_counts.get(d.id, 0),
            created_at=d.created_at,
        )
        for d, total_cards in results
    ]


async def get_due_cards_for_deck(
    db: AsyncSession,
    deck_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int = 50,
) -> list[CardDueResponse]:
    """Retrieve cards that are due for review for the user in a single bounded SQL query."""
    deck_res = await db.execute(select(Deck.id).where(Deck.id == deck_id))
    if not deck_res.scalar_one_or_none():
        raise ProblemError(status_code=404, code="not_found", detail="Deck not found")

    now = _utc_now()
    latest_rev_subq = (
        select(
            CardReview.card_id,
            CardReview.due_date,
            CardReview.interval_days,
            CardReview.repetitions,
            CardReview.ease_factor,
            func.row_number()
            .over(
                partition_by=CardReview.card_id,
                order_by=CardReview.reviewed_at.desc(),
            )
            .label("rn"),
        )
        .where(CardReview.user_id == user_id)
        .subquery()
    )

    query = (
        select(
            Card,
            latest_rev_subq.c.due_date,
            latest_rev_subq.c.interval_days,
            latest_rev_subq.c.repetitions,
            latest_rev_subq.c.ease_factor,
        )
        .outerjoin(
            latest_rev_subq,
            (latest_rev_subq.c.card_id == Card.id) & (latest_rev_subq.c.rn == 1),
        )
        .where(
            Card.deck_id == deck_id,
            (latest_rev_subq.c.card_id.is_(None)) | (latest_rev_subq.c.due_date <= now),
        )
        .limit(limit)
    )
    rows = (await db.execute(query)).all()

    due_list: list[CardDueResponse] = []
    for card, due_date, interval_days, reps, ease in rows:
        due_list.append(
            CardDueResponse(
                id=card.id,
                deck_id=card.deck_id,
                front=card.front,
                back=card.back,
                hint=card.hint,
                tags=card.tags,
                interval_days=interval_days if due_date is not None else 0,
                repetitions=reps if due_date is not None else 0,
                ease_factor=ease if due_date is not None else 2.5,
                due_date=due_date,
            )
        )

    return due_list


async def review_card(
    db: AsyncSession,
    card_id: uuid.UUID,
    user_id: uuid.UUID,
    rating: int,
) -> CardReviewResponse:
    """Process card review using SM-2 algorithm."""
    card_res = await db.execute(select(Card).where(Card.id == card_id))
    card = card_res.scalar_one_or_none()
    if not card:
        raise ProblemError(status_code=404, code="not_found", detail="Card not found")

    # Get previous review
    rev_query = (
        select(CardReview)
        .where(CardReview.card_id == card_id, CardReview.user_id == user_id)
        .order_by(CardReview.reviewed_at.desc())
    )
    rev_res = await db.execute(rev_query)
    last_review = rev_res.scalars().first()

    old_interval = last_review.interval_days if last_review else 1
    old_ease = last_review.ease_factor if last_review else 2.5
    old_repetitions = last_review.repetitions if last_review else 0

    new_interval, new_ease, new_repetitions, state = calculate_sm2(
        rating=rating,
        old_interval=old_interval,
        old_ease=old_ease,
        old_repetitions=old_repetitions,
    )

    now = _utc_now()
    next_due = now + timedelta(days=new_interval)
    review_id = uuid.uuid4()

    review_record = CardReview(
        id=review_id,
        user_id=user_id,
        card_id=card_id,
        rating=rating,
        state=state,
        interval_days=new_interval,
        ease_factor=new_ease,
        repetitions=new_repetitions,
        due_date=next_due,
        reviewed_at=now,
    )
    db.add(review_record)
    await db.commit()

    return CardReviewResponse(
        review_id=review_id,
        card_id=card_id,
        rating=rating,
        next_interval_days=new_interval,
        next_due_date=next_due,
        ease_factor=new_ease,
        repetitions=new_repetitions,
    )


async def import_anki_deck(
    db: AsyncSession,
    user_id: uuid.UUID,
    payload: AnkiImportRequest,
    medical_year: int = 1,
    folder_id: uuid.UUID | None = None,
) -> DeckResponse:
    """Create deck and import cards from Anki structured payload."""
    deck_id = uuid.uuid4()
    deck = Deck(
        id=deck_id,
        user_id=user_id,
        title=payload.deck_title,
        description=f"Imported from Anki ({len(payload.cards)} cards)",
        category=payload.category,
        medical_year=medical_year,
        folder_id=folder_id,
        is_public=False,
    )
    db.add(deck)

    for item in payload.cards:
        card = Card(
            id=uuid.uuid4(),
            deck_id=deck_id,
            front=item.front,
            back=item.back,
            hint=item.hint,
            tags=item.tags,
        )
        db.add(card)

    await db.commit()
    await db.refresh(deck)

    return DeckResponse(
        id=deck.id,
        title=deck.title,
        description=deck.description,
        category=deck.category,
        medical_year=deck.medical_year,
        folder_id=deck.folder_id,
        is_public=deck.is_public,
        total_cards=len(payload.cards),
        due_cards=len(payload.cards),
        created_at=deck.created_at,
    )


async def import_anki_file(
    db: AsyncSession,
    user_id: uuid.UUID,
    file_bytes: bytes,
    filename: str,
    medical_year: int = 1,
    folder_id: uuid.UUID | None = None,
    deck_title: str | None = None,
    category: str = "Medical",
) -> DeckResponse:
    """Parse and import Anki package (.apkg, .colpkg, .tsv, .csv, .json) into database."""
    from app.modules.learning.anki_parser import parse_anki_package

    parsed_title, cards_data = parse_anki_package(file_bytes, filename)
    final_title = deck_title.strip() if deck_title and deck_title.strip() else parsed_title

    deck_id = uuid.uuid4()
    deck = Deck(
        id=deck_id,
        user_id=user_id,
        title=final_title,
        description=f"Imported from {filename} ({len(cards_data)} cards)",
        category=category,
        medical_year=medical_year,
        folder_id=folder_id,
        is_public=False,
    )
    db.add(deck)

    for item in cards_data:
        card = Card(
            id=uuid.uuid4(),
            deck_id=deck_id,
            front=item["front"],
            back=item["back"],
            hint=item.get("hint"),
            tags=item.get("tags"),
        )
        db.add(card)

    await db.commit()
    await db.refresh(deck)

    return DeckResponse(
        id=deck.id,
        title=deck.title,
        description=deck.description,
        category=deck.category,
        medical_year=deck.medical_year,
        folder_id=deck.folder_id,
        is_public=deck.is_public,
        total_cards=len(cards_data),
        due_cards=len(cards_data),
        created_at=deck.created_at,
    )


async def get_learning_stats(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> LearningStatsOut:
    """Compute live learning analytics for authenticated student."""
    quiz_stmt = select(
        func.count(QuizAttempt.id),
        func.sum(case((QuizAttempt.passed == True, 1), else_=0)),  # noqa: E712
        func.avg(QuizAttempt.percentage),
    ).where(QuizAttempt.user_id == user_id)
    q_row = (await db.execute(quiz_stmt)).fetchone()
    quizzes_attempted = q_row[0] or 0
    quizzes_passed = q_row[1] or 0
    avg_pct = round(float(q_row[2] or 0.0), 2)

    cards_reviewed = (
        await db.scalar(
            select(func.count(CardReview.id)).where(CardReview.user_id == user_id)
        )
    ) or 0

    deck_ids = (
        await db.scalars(
            select(Deck.id).where((Deck.is_public == True) | (Deck.user_id == user_id))  # noqa: E712
        )
    ).all()
    total_decks = len(deck_ids)

    if not deck_ids:
        total_cards = 0
        cards_due = 0
    else:
        total_cards = (
            await db.scalar(
                select(func.count(Card.id)).where(Card.deck_id.in_(deck_ids))
            )
        ) or 0

        now = _utc_now()
        latest_rev_subq = (
            select(
                CardReview.card_id,
                CardReview.due_date,
                func.row_number()
                .over(
                    partition_by=CardReview.card_id,
                    order_by=CardReview.reviewed_at.desc(),
                )
                .label("rn"),
            )
            .where(CardReview.user_id == user_id)
            .subquery()
        )

        due_stmt = (
            select(func.count(Card.id))
            .outerjoin(
                latest_rev_subq,
                (latest_rev_subq.c.card_id == Card.id) & (latest_rev_subq.c.rn == 1),
            )
            .where(
                Card.deck_id.in_(deck_ids),
                (latest_rev_subq.c.card_id.is_(None)) | (latest_rev_subq.c.due_date <= now),
            )
        )
        cards_due = (await db.scalar(due_stmt)) or 0

    return LearningStatsOut(
        quizzes_attempted=quizzes_attempted,
        quizzes_passed=quizzes_passed,
        average_percentage=avg_pct,
        cards_reviewed=cards_reviewed,
        cards_due=cards_due,
        total_cards=total_cards,
        total_decks=total_decks,
    )


async def search_cards(
    db: AsyncSession,
    user_id: uuid.UUID,
    query: str,
    deck_id: uuid.UUID | None = None,
    medical_year: int | None = None,
    limit: int = 100,
) -> CardSearchResponse:
    """Search flashcards across accessible decks with matching on front, back, tags, and deck title."""
    q_pattern = f"%{query.strip()}%"

    stmt = (
        select(Card, Deck)
        .join(Deck, Card.deck_id == Deck.id)
        .where(
            or_(
                Deck.user_id == user_id,
                Deck.user_id.is_(None),
                Deck.is_public.is_(True),
            )
        )
    )

    if query.strip():
        stmt = stmt.where(
            or_(
                Card.front.ilike(q_pattern),
                Card.back.ilike(q_pattern),
                Card.tags.ilike(q_pattern),
                Deck.title.ilike(q_pattern),
            )
        )

    if deck_id:
        stmt = stmt.where(Card.deck_id == deck_id)

    if medical_year:
        stmt = stmt.where(Deck.medical_year == medical_year)

    stmt = stmt.order_by(Deck.title, Card.created_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).all()

    card_ids = [c.id for c, _ in rows]
    reviews_map: dict[uuid.UUID, CardReview] = {}
    if card_ids:
        rev_stmt = (
            select(CardReview)
            .where(CardReview.card_id.in_(card_ids), CardReview.user_id == user_id)
            .order_by(CardReview.reviewed_at.desc())
        )
        reviews = (await db.scalars(rev_stmt)).all()
        for r in reviews:
            if r.card_id not in reviews_map:
                reviews_map[r.card_id] = r

    results: list[CardSearchResult] = []
    for card, deck in rows:
        rev = reviews_map.get(card.id)
        results.append(
            CardSearchResult(
                id=card.id,
                deck_id=deck.id,
                deck_title=deck.title,
                front=card.front,
                back=card.back,
                hint=card.hint,
                tags=card.tags,
                interval_days=rev.interval_days if rev else 1,
                repetitions=rev.repetitions if rev else 0,
                state=rev.state if rev else 0,
                due_date=rev.due_date if rev else None,
            )
        )

    return CardSearchResponse(total=len(results), cards=results)

"""Learning module (§35–§37).

OWNS: question banks, questions, options, attempts, answers, decks, cards,
reviews. Server grades (§35); correct answers are not exposed pre-submission.
"""

TABLES_OWNED = (
    "question_banks",
    "questions",
    "question_options",
    "quiz_attempts",
    "attempt_answers",
    "decks",
    "cards",
    "card_reviews",
)

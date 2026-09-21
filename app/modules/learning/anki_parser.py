"""Advanced Anki (.apkg/.colpkg) & Flashcard Package Parser.

Production-grade Anki collection processor supporting:
1. Anki 2.0 / 2.1 / 2.1b (standard SQLite and zstandard-compressed collection.anki21b)
2. Full Cloze deletion expansion ({{c1::answer::hint}}) producing multi-card sets
3. Media mapping & base64 embedding for instant offline images (< 500KB)
4. Multi-field model template resolution (Text, Extra, First Aid, Pathoma, High-Yield)
5. TSV, CSV, TXT delimited text and AnkiConnect JSON deck exports
"""

from __future__ import annotations

import base64
import csv
import io
import json
import mimetypes
import re
import sqlite3
import tempfile
import zipfile
from typing import Any


def _sanitize_html(text: str) -> str:
    """Sanitize HTML: preserve essential formatting and layout while stripping dangerous tags."""
    if not text:
        return ""
    # Strip dangerous executable elements
    text = re.sub(r"<(script|style|iframe|object|embed)[^>]*>.*?</\1>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # Convert HTML line breaks
    text = re.sub(r"&nbsp;", " ", text, flags=re.IGNORECASE)
    # Remove empty class/id attributes or sound tags [sound:...]
    text = re.sub(r"\[sound:[^\]]+\]", "", text)
    return text.strip()


def _strip_html(text: str) -> str:
    """Clean all HTML tags for plain-text searches or summary previews."""
    if not text:
        return ""
    text = re.sub(r"<(br|div|p)\s*/?>", "\n", text, flags=re.IGNORECASE)
    clean = re.sub(r"<[^>]+>", "", text)
    clean = re.sub(r"&nbsp;", " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"&amp;", "&", clean, flags=re.IGNORECASE)
    clean = re.sub(r"&lt;", "<", clean, flags=re.IGNORECASE)
    clean = re.sub(r"&gt;", ">", clean, flags=re.IGNORECASE)
    return clean.strip()


CLOZE_REGEX = re.compile(r"\{\{c(\d+)::(.*?)(?:::(.*?))?\}\}", re.DOTALL)


def _expand_cloze_fields(
    text: str, extra_text: str = "", tags: str | None = None
) -> list[dict[str, Any]]:
    """Expand a single Cloze note containing {{c1::...}}, {{c2::...}} into individual cards."""
    matches = CLOZE_REGEX.findall(text)
    if not matches:
        return []

    cloze_indices = sorted(list({int(m[0]) for m in matches}))
    cards: list[dict[str, Any]] = []

    for c_num in cloze_indices:
        hint_text: str | None = None

        def _replace_front(match: re.Match[str]) -> str:
            nonlocal hint_text
            num = int(match.group(1))
            ans = match.group(2)
            h = match.group(3) if match.group(3) is not None else ""
            if num == c_num:
                if h:
                    hint_text = h
                    return f"[{h}]"
                return "[...]"
            # Other clozes are shown revealed without curly brackets
            return ans

        def _replace_back(match: re.Match[str]) -> str:
            num = int(match.group(1))
            ans = match.group(2)
            if num == c_num:
                return f"<b>[{ans}]</b>"
            return ans

        front = CLOZE_REGEX.sub(_replace_front, text)
        back = CLOZE_REGEX.sub(_replace_back, text)

        if extra_text:
            back = f"{back}<br><hr style='margin: 12px 0; border: none; border-top: 1px solid #cbd5e1;'><br>{extra_text}"

        cards.append({
            "front": _sanitize_html(front),
            "back": _sanitize_html(back),
            "hint": hint_text,
            "tags": tags,
        })

    return cards


def parse_anki_package(file_bytes: bytes, filename: str) -> tuple[str, list[dict[str, Any]]]:
    """Parse an uploaded Anki file (.apkg, .colpkg, .tsv, .csv, .txt, .json) into deck title and cards list."""
    fname_lower = filename.lower()
    is_zip = file_bytes.startswith(b"PK\x03\x04") or fname_lower.endswith((".apkg", ".colpkg", ".zip"))

    if is_zip:
        return _parse_apkg(file_bytes, filename)
    elif fname_lower.endswith((".tsv", ".txt", ".csv")):
        return _parse_delimited_text(file_bytes, filename)
    elif fname_lower.endswith(".json"):
        return _parse_json(file_bytes, filename)
    else:
        # Auto-detect format
        try:
            return _parse_apkg(file_bytes, filename)
        except Exception:
            try:
                return _parse_json(file_bytes, filename)
            except Exception:
                return _parse_delimited_text(file_bytes, filename)


def _parse_apkg(file_bytes: bytes, filename: str) -> tuple[str, list[dict[str, Any]]]:
    """Extract SQLite collection database from .apkg/.colpkg with model, cloze & media support."""
    deck_title = re.sub(r"\.(apkg|colpkg|zip)$", "", filename, flags=re.IGNORECASE).strip() or "Imported Anki Deck"
    cards: list[dict[str, Any]] = []

    with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
        namelist = zf.namelist()

        # ── 1. Read Media Mapping ─────────────────────────────────────────────
        media_map: dict[str, str] = {}  # { "0": "heart_anatomy.png" }
        if "media" in namelist:
            try:
                raw_media = zf.read("media").decode("utf-8", errors="replace")
                media_map = json.loads(raw_media)
            except Exception:
                media_map = {}

        # Pre-cache small images as base64 data URIs (< 500KB)
        img_data_uris: dict[str, str] = {}
        for num_key, orig_fname in media_map.items():
            if num_key in namelist:
                try:
                    data = zf.read(num_key)
                    if len(data) <= 500 * 1024:  # <= 500 KB
                        mime, _ = mimetypes.guess_type(orig_fname)
                        mime = mime or "image/png"
                        b64 = base64.b64encode(data).decode("ascii")
                        img_data_uris[orig_fname] = f"data:{mime};base64,{b64}"
                except Exception:
                    pass

        # ── 2. Locate SQLite Database ─────────────────────────────────────────
        db_filename: str | None = None
        for candidate in ("collection.anki21b", "collection.anki21", "collection.anki2"):
            if candidate in namelist:
                db_filename = candidate
                break

        if not db_filename:
            for name in namelist:
                if "anki2" in name:
                    db_filename = name
                    break

        if not db_filename:
            raise ValueError(f"Archive does not contain an Anki collection database (found: {namelist})")

        db_bytes = zf.read(db_filename)

        # Handle zstandard compressed collection.anki21b
        if db_filename.endswith(".anki21b") or db_bytes[:4] == b"\x28\xb5\x2f\xfd":
            try:
                import zstandard

                dctx = zstandard.ZstdDecompressor()
                db_bytes = dctx.decompress(db_bytes)
            except Exception as exc:
                raise ValueError(f"Failed to decompress Anki 2.1b zstd collection: {exc}") from exc

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp.write(db_bytes)
            tmp_path = tmp.name

        try:
            conn = sqlite3.connect(tmp_path)
            cursor = conn.cursor()

            # ── 3. Parse Decks and Models from 'col' table ─────────────────────
            models_dict: dict[int, dict[str, Any]] = {}
            try:
                cursor.execute("SELECT decks, models FROM col")
                row = cursor.fetchone()
                if row:
                    if row[0]:
                        try:
                            decks_json = json.loads(row[0])
                            for _, d_data in decks_json.items():
                                if d_data.get("name") and d_data.get("name") != "Default":
                                    deck_title = d_data["name"]
                                    break
                        except Exception:
                            pass
                    if row[1]:
                        try:
                            raw_models = json.loads(row[1])
                            for m_id, m_data in raw_models.items():
                                models_dict[int(m_id)] = m_data
                        except Exception:
                            pass
            except Exception:
                pass

            # ── 4. Query Notes Table ──────────────────────────────────────────
            cursor.execute("SELECT mid, flds, tags FROM notes")
            notes_rows = cursor.fetchall()

            for mid, flds, tags in notes_rows:
                parts = flds.split("\x1f") if isinstance(flds, str) else []
                if not parts:
                    continue

                # Embed inlined media images into parts
                for i in range(len(parts)):
                    part_text = parts[i]
                    for orig_fname, data_uri in img_data_uris.items():
                        if orig_fname in part_text:
                            # Replace src="orig_fname" with src="data_uri"
                            pattern = rf'(<img[^>]+src=["\']?){re.escape(orig_fname)}(["\']?[^>]*>)'
                            part_text = re.sub(pattern, rf"\1{data_uri}\2", part_text, flags=re.IGNORECASE)
                    parts[i] = part_text

                clean_tags = (tags or "").strip().replace(" ", ", ") or None

                # Model template identification
                model_meta = models_dict.get(mid, {})
                model_type = model_meta.get("type", 0)  # 1 = Cloze, 0 = Standard
                flds_meta = model_meta.get("flds", [])
                field_names = [f.get("name", "") for f in flds_meta]

                first_field = parts[0] if len(parts) > 0 else ""
                second_field = parts[1] if len(parts) > 1 else ""

                # Check if this note is a Cloze deletion
                is_cloze = model_type == 1 or CLOZE_REGEX.search(first_field) or CLOZE_REGEX.search(second_field)

                if is_cloze:
                    # Collect extra fields beyond the cloze text
                    extra_parts = parts[1:] if len(parts) > 1 else []
                    extra_text = "<br><br>".join([_sanitize_html(p) for p in extra_parts if p.strip()])
                    cloze_cards = _expand_cloze_fields(first_field, extra_text=extra_text, tags=clean_tags)
                    if cloze_cards:
                        cards.extend(cloze_cards)
                        continue

                # Standard Non-Cloze Model
                if len(field_names) >= 2 and len(parts) >= 2:
                    front = _sanitize_html(parts[0])
                    # Join remaining fields (Extra, Lecture Notes, First Aid, etc.)
                    back_sections = []
                    for idx in range(1, len(parts)):
                        val = _sanitize_html(parts[idx])
                        if not val:
                            continue
                        lbl = field_names[idx] if idx < len(field_names) else ""
                        if lbl and lbl.lower() not in ("back", "answer"):
                            back_sections.append(f"<div style='margin-top: 8px;'><b>[{lbl}]</b><br>{val}</div>")
                        else:
                            back_sections.append(val)

                    back = "<br>".join(back_sections) if back_sections else front
                else:
                    front = _sanitize_html(parts[0]) if len(parts) > 0 else ""
                    back = _sanitize_html(parts[1]) if len(parts) > 1 else front

                hint = _sanitize_html(parts[2]) if len(parts) > 2 and len(parts) <= 3 else None

                if not front.strip():
                    continue

                cards.append({
                    "front": front,
                    "back": back or front,
                    "hint": hint,
                    "tags": clean_tags,
                })

            conn.close()
        finally:
            import os

            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    return deck_title, cards


def _parse_delimited_text(file_bytes: bytes, filename: str) -> tuple[str, list[dict[str, Any]]]:
    """Parse TSV / CSV / TXT files formatted as front<sep>back<sep>tags."""
    deck_title = re.sub(r"\.(tsv|csv|txt)$", "", filename, flags=re.IGNORECASE).strip() or "Imported Flashcards"
    cards: list[dict[str, Any]] = []

    try:
        content = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content = file_bytes.decode("latin-1", errors="replace")

    first_lines = "\n".join([line for line in content.splitlines()[:5] if line.strip() and not line.startswith("#")])
    delimiter = "\t" if "\t" in first_lines else ","

    reader = csv.reader(content.splitlines(), delimiter=delimiter)
    for row in reader:
        if not row or not any(row):
            continue
        if row[0].strip().startswith("#"):
            continue

        raw_front = row[0].strip()
        raw_back = row[1].strip() if len(row) > 1 else raw_front
        tags = row[2].strip() if len(row) > 2 else None
        hint = row[3].strip() if len(row) > 3 else None

        # Check for cloze syntax in raw_front
        if CLOZE_REGEX.search(raw_front):
            cloze_cards = _expand_cloze_fields(raw_front, extra_text=raw_back if raw_back != raw_front else "", tags=tags)
            if cloze_cards:
                cards.extend(cloze_cards)
                continue

        front = _sanitize_html(raw_front)
        back = _sanitize_html(raw_back)

        if front:
            cards.append({
                "front": front,
                "back": back,
                "hint": hint,
                "tags": tags,
            })

    return deck_title, cards


def _parse_json(file_bytes: bytes, filename: str) -> tuple[str, list[dict[str, Any]]]:
    """Parse JSON deck format."""
    deck_title = re.sub(r"\.json$", "", filename, flags=re.IGNORECASE).strip() or "Imported JSON Deck"
    cards: list[dict[str, Any]] = []

    try:
        data = json.loads(file_bytes.decode("utf-8"))
    except UnicodeDecodeError:
        data = json.loads(file_bytes.decode("latin-1", errors="replace"))

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                raw_front = str(item.get("front") or item.get("question") or item.get("q") or "")
                raw_back = str(item.get("back") or item.get("answer") or item.get("a") or "")
                hint = item.get("hint")
                tags = item.get("tags")
                if isinstance(tags, list):
                    tags = ", ".join(tags)

                if CLOZE_REGEX.search(raw_front):
                    cloze_cards = _expand_cloze_fields(raw_front, extra_text=raw_back if raw_back != raw_front else "", tags=str(tags) if tags else None)
                    if cloze_cards:
                        cards.extend(cloze_cards)
                        continue

                front = _sanitize_html(raw_front)
                back = _sanitize_html(raw_back)

                if front:
                    cards.append({
                        "front": front,
                        "back": back or front,
                        "hint": str(hint) if hint else None,
                        "tags": str(tags) if tags else None,
                    })
    elif isinstance(data, dict):
        if data.get("deckName") or data.get("title") or data.get("name"):
            deck_title = data.get("deckName") or data.get("title") or data.get("name")

        notes = data.get("notes") or data.get("cards") or []
        for item in notes:
            if isinstance(item, dict):
                fields = item.get("fields", item)
                raw_front = str(fields.get("Front") or fields.get("front") or fields.get("question") or fields.get("Text") or "")
                raw_back = str(fields.get("Back") or fields.get("back") or fields.get("answer") or fields.get("Extra") or "")
                hint = fields.get("Hint") or fields.get("hint")
                tags = item.get("tags")
                if isinstance(tags, list):
                    tags = ", ".join(tags)

                if CLOZE_REGEX.search(raw_front):
                    cloze_cards = _expand_cloze_fields(raw_front, extra_text=raw_back, tags=str(tags) if tags else None)
                    if cloze_cards:
                        cards.extend(cloze_cards)
                        continue

                front = _sanitize_html(raw_front)
                back = _sanitize_html(raw_back)

                if front:
                    cards.append({
                        "front": front,
                        "back": back or front,
                        "hint": str(hint) if hint else None,
                        "tags": str(tags) if tags else None,
                    })

    return deck_title, cards

import difflib
from supabase_db import supabase


def normalize_text(text):
    return (text or "").lower().strip()


def load_command_phrases():
    result = supabase.table("command_phrases").select("*").execute()
    return result.data or []


def load_word_fixes():
    result = supabase.table("command_word_fixes").select("*").execute()

    fixes = {}
    for row in result.data or []:
        fixes[row["wrong_word"]] = row["correct_word"]

    return fixes


def apply_learned_fixes(text):
    fixes = load_word_fixes()
    words = normalize_text(text).split()

    fixed_words = []
    for word in words:
        fixed_words.append(fixes.get(word, word))

    return " ".join(fixed_words)


def save_unknown_command(user_id, text, best_score):
    supabase.table("unknown_commands").insert({
        "user_id": user_id,
        "text": text,
        "best_score": best_score,
    }).execute()


def detect_command_by_memory(text, user_id=None):
    fixed_text = apply_learned_fixes(text)
    phrases = load_command_phrases()

    best_intent = None
    best_score = 0

    for row in phrases:
        intent = row["intent"]
        phrase = normalize_text(row["phrase"])

        score = difflib.SequenceMatcher(
            None,
            fixed_text,
            phrase
        ).ratio()

        if score > best_score:
            best_score = score
            best_intent = intent

    if best_score >= 0.72:
        return {
            "intent": best_intent,
            "score": best_score,
            "fixed_text": fixed_text,
        }

    if user_id:
        save_unknown_command(user_id, text, best_score)

    return {
        "intent": "unknown",
        "score": best_score,
        "fixed_text": fixed_text,
    }


def learn_word_fix(wrong_word, correct_word):
    wrong_word = normalize_text(wrong_word)
    correct_word = normalize_text(correct_word)

    if not wrong_word or not correct_word:
        return

    supabase.table("command_word_fixes").upsert({
        "wrong_word": wrong_word,
        "correct_word": correct_word,
    }, on_conflict="wrong_word").execute()


def auto_learn_from_previous(wrong_text, correct_text):
    wrong_words = normalize_text(wrong_text).split()
    correct_words = normalize_text(correct_text).split()

    for wrong_word in wrong_words:
        if wrong_word in correct_words:
            continue

        best_match = difflib.get_close_matches(
            wrong_word,
            correct_words,
            n=1,
            cutoff=0.65
        )

        if best_match:
            learn_word_fix(wrong_word, best_match[0])

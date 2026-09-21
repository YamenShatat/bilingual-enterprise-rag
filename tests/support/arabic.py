"""Ground truth and a simple metric for judging Arabic text extraction."""

import re

# The Arabic sentences the bilingual PDF and DOCX fixtures were built from.
ARABIC_TRUTH = [
    "سياسة الإجازة السنوية",
    "يحصل الموظف على ٢١ يومًا من الإجازة السنوية بعد إنهاء فترة التجربة.",
    "يجب تقديم الطلب عبر بوابة الموارد البشرية قبل ١٠ أيام عمل.",
    "أهلاً بكم في شركة أكمي، آمل أن تكون على ما يرام في مدرسة الهدى.",
    "للاتصال بالشبكة الداخلية استخدم VPN ثم افتح Cisco AnyConnect.",
]


def words(text: str) -> list[str]:
    """Split on whitespace after replacing sentence punctuation (ASCII and Arabic) with spaces."""
    return [word for word in re.sub(r"[.,،:;؛؟?!()]", " ", text).split() if word]


def arabic_word_recall(document_text: str) -> float:
    """Share of ground-truth words that appear intact in the text, ignoring their order."""
    expected = [word for line in ARABIC_TRUTH for word in words(line)]
    found = set(words(document_text))
    return sum(word in found for word in expected) / len(expected)

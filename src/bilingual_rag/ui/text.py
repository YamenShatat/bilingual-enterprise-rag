"""What the UI shows, as plain functions (no Streamlit), so they can be tested.

Model output is untrusted text: ``answer_html`` escapes it before it is placed inside HTML, so an
answer (or a planted document quoted in one) cannot inject markup or script into the page.
``dir="auto"`` lets the browser lay out Arabic right to left and English left to right.
"""

import html

# One plain explanation per refusal reason (D-019, D-023), in both languages of the corpus.
REFUSAL_MESSAGES = {
    "no_results": (
        "None of the documents you may read match this question.",
        "لا توجد وثائق مسموح لك بالاطلاع عليها تطابق هذا السؤال.",
    ),
    "low_score": (
        "The documents you may read do not seem to cover this question.",
        "لا يبدو أن الوثائق المسموح لك بالاطلاع عليها تغطي هذا السؤال.",
    ),
    "model_refused": (
        "The documents found do not contain the answer, so none is given.",
        "الوثائق التي عُثر عليها لا تتضمن الإجابة، لذلك لم تُقدَّم إجابة.",
    ),
    "uncited": (
        "An answer was produced but could not be traced to a source, so it is not shown.",
        "أُنتجت إجابة لكن تعذّر ربطها بمصدر، لذلك لا تُعرض.",
    ),
}


def refusal_message(code: str) -> str:
    """The English and Arabic explanation of a refusal code, one per line."""
    english, arabic = REFUSAL_MESSAGES.get(
        code, (f"No answer ({code}).", f"لا توجد إجابة ({code}).")
    )
    return f"{english}\n\n{arabic}"


def answer_html(text: str) -> str:
    """The answer, escaped and wrapped so the browser picks the text direction itself."""
    escaped = html.escape(text).replace("\n", "<br>")
    return f'<div dir="auto" style="font-size:1.1rem;line-height:1.7">{escaped}</div>'


def citation_rows(citations: list[dict]) -> list[dict]:
    """Rows for the sources table, in citation-number order."""
    return [
        {"#": c["number"], "Document": c["title"], "Page": c["page"], "ID": c["document_id"]}
        for c in sorted(citations, key=lambda c: c["number"])
    ]

# Data

This folder holds the documents used to develop and evaluate the system.

**Synthetic dataset created for demonstration purposes. No confidential company data is included.**

The corpus describes a fictional company, *Acme MENA Technology*, and deliberately mixes
English-only, Arabic-only and bilingual documents so that all four retrieval directions
(EN→EN, AR→AR, AR→EN, EN→AR) can be evaluated.

## Layout

```text
data/synthetic/<department>/<document>_<language>.md
```

The department folder will become the `department` metadata field, and the `_en` / `_ar`
suffix marks the document language.

## Current documents (4 so far; the target is roughly 30-50)

| Document | Language | Notes |
| --- | --- | --- |
| `hr/annual_leave_policy_en.md` | English | Bilingual pair with the Arabic version |
| `hr/annual_leave_policy_ar.md` | Arabic | Same policy; uses Arabic-Indic digits (٢١) |
| `hr/remote_work_policy_en.md` | English | English-only content |
| `security/password_policy_ar.md` | Arabic | Arabic-only content; uses Western digits (14) and an embedded email address |

The two Arabic documents deliberately use different digit systems, because both occur in
real Arabic business documents and retrieval has to cope with either.

Files are UTF-8 without a BOM and use LF line endings; an integration test enforces this.

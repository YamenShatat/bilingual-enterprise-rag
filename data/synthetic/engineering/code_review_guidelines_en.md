# Code Review Guidelines

**Acme MENA Technology — Engineering**
Document owner: Engineering Leads · Effective: 1 January 2026 · Version 2.1

## 1. Why We Review

Every change to production code is reviewed by another engineer before it is merged.

## 2. Approvals

A pull request needs at least 2 approvals, one of which must come from a code owner of the affected area. Authors cannot approve their own changes.

## 3. Turnaround

Reviewers respond within 1 business day. If you cannot review in time, tell the author so they can find another reviewer.

## 4. Size

Keep pull requests under 400 changed lines. Large changes should be split into smaller ones.

## 5. Checks

All automated checks must pass before merging, and test coverage of changed code must be at least 80%.

## 6. Security-Sensitive Changes

Changes to authentication, authorisation, encryption or payment code also require a review from the Security team.

## 7. Hotfixes

A production hotfix needs 1 approval, and a full review must be completed within 24 hours after it is deployed.

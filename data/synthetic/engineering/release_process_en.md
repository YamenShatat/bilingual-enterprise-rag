# Release Process

**Acme MENA Technology — Engineering**
Document owner: Release Manager · Effective: 1 January 2026 · Version 1.8

## 1. Release Cadence

Production releases go out every 2 weeks on Tuesday.

## 2. Code Freeze

The release branch is frozen on Monday at 12:00 Gulf Standard Time. Only fixes for release blockers are accepted after the freeze.

## 3. Release Checklist

1. All automated checks are green.
2. A rollback plan is written and reviewed.
3. Release notes are published to the support team.
4. The on-call engineer confirms availability.

## 4. Gradual Rollout

Each release is first deployed to 10% of users for 1 hour. If error rates stay below 1%, it is rolled out to everyone.

## 5. Rollback

Any engineer on call may roll back a release without further approval if the error rate exceeds 5%.

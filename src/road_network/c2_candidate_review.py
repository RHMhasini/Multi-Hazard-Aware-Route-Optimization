"""Versioned, human-authored review records for Component 2 candidates.

Review records are evidence annotations only. This module never edits a graph
and never converts a review classification into a repair approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CANDIDATE_REPORT = ROOT / "outputs/component2_evaluation/c2_topology_candidates.json"
DEFAULT_REVIEW_DATASET = ROOT / "outputs/component2_evaluation/c2_topology_candidate_reviews.json"
SCHEMA_IDENTIFIER = "c2-topology-candidate-reviews/v1"
CLASSIFICATIONS = {"likely_gap", "legitimate_endpoint", "uncertain"}
AUTOMATED_STATUSES = {"eligible", "needs_review", "rejected"}
EVIDENCE_FIELDS = {"evidence_type", "description", "reference"}


class ReviewDatasetError(ValueError):
    """Raised when review data cannot be safely reconciled to its source."""

    http_status = 422
    code = "invalid_review_data"


class ReviewArtifactMissingError(ReviewDatasetError):
    http_status = 404
    code = "review_artifact_missing"


class CandidateSourceMissingError(ReviewDatasetError):
    http_status = 404
    code = "candidate_source_missing"


class CandidateSourceMismatchError(ReviewDatasetError):
    http_status = 409
    code = "source_report_hash_mismatch"


class ReviewArtifactMalformedError(ReviewDatasetError):
    http_status = 422
    code = "malformed_review_artifact"


def _project_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def _load_report(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except FileNotFoundError as exc:
        raise CandidateSourceMissingError(
            f"Candidate source report is missing: {_project_relative(path)}. Restore the report before loading reviews."
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewArtifactMalformedError(
            f"Candidate source report is unreadable or malformed: {_project_relative(path)}."
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise ReviewArtifactMalformedError("Candidate source report must contain a candidates list.")
    return raw, payload


def _candidate_index(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for candidate in report["candidates"]:
        if not isinstance(candidate, dict):
            raise ReviewDatasetError("Every source candidate must be an object.")
        candidate_id = candidate.get("candidate_id")
        status = candidate.get("status")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ReviewDatasetError("Every source candidate needs a stable candidate_id.")
        if candidate_id in result:
            raise ReviewDatasetError(f"Duplicate source candidate ID: {candidate_id}")
        if not isinstance(status, str) or status not in AUTOMATED_STATUSES:
            raise ReviewDatasetError(f"Candidate {candidate_id} has an invalid automated status.")
        result[candidate_id] = candidate
    return result


def create_unreviewed_dataset(candidate_report_path: Path) -> dict[str, Any]:
    raw, report = _load_report(candidate_report_path)
    candidates = _candidate_index(report)
    return {
        "schema_identifier": SCHEMA_IDENTIFIER,
        "candidate_source": {
            "path": candidate_report_path.name,
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "review_policy": (
            "Human annotations are separate from automated status and are not repair approvals."
        ),
        "reviews": [
            {
                "candidate_id": candidate_id,
                "automated_status": candidate["status"],
                "review_state": "unreviewed",
                "classification": None,
                "reviewer_decision": None,
                "supporting_evidence": [],
                "limitations": [],
                "reviewed_at": None,
                "reviewer_id": None,
            }
            for candidate_id, candidate in sorted(candidates.items())
        ],
    }


def validate_review_dataset(
    dataset: Any, candidate_report_path: Path
) -> dict[str, Any]:
    raw, report = _load_report(candidate_report_path)
    source_candidates = _candidate_index(report)
    if not isinstance(dataset, dict) or dataset.get("schema_identifier") != SCHEMA_IDENTIFIER:
        raise ReviewDatasetError(f"Review dataset must use schema {SCHEMA_IDENTIFIER}.")
    provenance = dataset.get("candidate_source")
    if not isinstance(provenance, dict):
        raise ReviewDatasetError("Review dataset is missing candidate_source provenance.")
    expected_hash = hashlib.sha256(raw).hexdigest()
    if provenance.get("sha256") != expected_hash:
        raise CandidateSourceMismatchError(
            "Candidate source-report SHA-256 mismatch. Restore the matching candidate report or explicitly reconcile the review dataset."
        )
    reviews = dataset.get("reviews")
    if not isinstance(reviews, list):
        raise ReviewDatasetError("Review dataset must contain a reviews list.")

    indexed: dict[str, dict[str, Any]] = {}
    for index, review in enumerate(reviews):
        if not isinstance(review, dict):
            raise ReviewDatasetError(f"Review at index {index} must be an object.")
        candidate_id = review.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id not in source_candidates:
            raise ReviewDatasetError(f"Review at index {index} references an unknown candidate.")
        if candidate_id in indexed:
            raise ReviewDatasetError(f"Duplicate review candidate ID: {candidate_id}")
        source_status = source_candidates[candidate_id]["status"]
        if review.get("automated_status") != source_status:
            raise ReviewDatasetError(
                f"Automated status changed for candidate {candidate_id}; source status is immutable."
            )
        classification = review.get("classification")
        if classification is not None and (
            not isinstance(classification, str) or classification not in CLASSIFICATIONS
        ):
            raise ReviewDatasetError(f"Invalid review classification for candidate {candidate_id}.")
        expected_state = "unreviewed" if classification is None else "reviewed"
        if review.get("review_state") != expected_state:
            raise ReviewDatasetError(f"Review state does not match classification for {candidate_id}.")
        decision = review.get("reviewer_decision")
        evidence = review.get("supporting_evidence")
        limitations = review.get("limitations")
        if decision is not None and (not isinstance(decision, str) or not decision.strip()):
            raise ReviewDatasetError(f"Reviewer decision for {candidate_id} must be non-empty text or null.")
        if not isinstance(evidence, list) or not all(isinstance(item, dict) for item in evidence):
            raise ReviewDatasetError(f"Supporting evidence for {candidate_id} must be a list of objects.")
        for evidence_item in evidence:
            if not EVIDENCE_FIELDS <= set(evidence_item) or any(
                not isinstance(evidence_item.get(field), str) or not evidence_item[field].strip()
                for field in EVIDENCE_FIELDS
            ):
                raise ReviewDatasetError(f"Supporting evidence for {candidate_id} is incomplete.")
        if not isinstance(limitations, list) or not all(
            isinstance(item, str) and item.strip() for item in limitations
        ):
            raise ReviewDatasetError(f"Limitations for {candidate_id} must be a list of non-empty strings.")
        if classification is not None and (not decision or not evidence or not limitations):
            raise ReviewDatasetError(
                f"Reviewed candidate {candidate_id} requires a decision, evidence, and limitations."
            )
        reviewed_at = review.get("reviewed_at")
        if reviewed_at is not None:
            if not isinstance(reviewed_at, str):
                raise ReviewDatasetError(f"Review timestamp for {candidate_id} must be ISO 8601 text or null.")
            try:
                datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ReviewDatasetError(f"Review timestamp for {candidate_id} is not valid ISO 8601.") from exc
        reviewer_id = review.get("reviewer_id")
        if reviewer_id is not None and (not isinstance(reviewer_id, str) or not reviewer_id.strip()):
            raise ReviewDatasetError(f"Reviewer identifier for {candidate_id} must be non-empty text or null.")
        indexed[candidate_id] = review

    missing_ids = sorted(set(source_candidates) - set(indexed))
    unexpected_ids = sorted(set(indexed) - set(source_candidates))
    if missing_ids or unexpected_ids:
        raise ReviewDatasetError(
            f"Review candidate set differs from source; missing={missing_ids}, unexpected={unexpected_ids}."
        )
    return dataset


def load_review_dataset(review_path: Path, candidate_report_path: Path) -> dict[str, Any]:
    try:
        dataset = json.loads(review_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReviewArtifactMissingError(
            f"Review artifact is missing: {_project_relative(review_path)}. Restore it or initialize a new artifact with `python -m src.road_network.c2_candidate_review --init --candidate-report outputs/component2_evaluation/c2_topology_candidates.json --output outputs/component2_evaluation/c2_topology_candidate_reviews.json`."
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewArtifactMalformedError(
            f"Review artifact is unreadable or malformed: {_project_relative(review_path)}. Validate or restore the JSON artifact."
        ) from exc
    return validate_review_dataset(dataset, candidate_report_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-report", type=Path, default=DEFAULT_CANDIDATE_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_REVIEW_DATASET)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--init", action="store_true", help="Create a new all-unreviewed review dataset; refuse overwrite.")
    mode.add_argument("--validate", action="store_true", help="Validate an existing review dataset against its source report.")
    args = parser.parse_args()
    if args.init:
        if args.output.exists():
            parser.error(f"Refusing to overwrite existing review dataset: {args.output}")
        dataset = create_unreviewed_dataset(args.candidate_report)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(dataset, indent=2) + "\n", encoding="utf-8")
    else:
        load_review_dataset(args.output, args.candidate_report)
    print(f"Review dataset valid: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Applications RAG CLI.

Commands:
  build      Rebuild JSONL + LanceDB index from tracker CSV.
  feedback-batch  Replay outcome events from JSONL into RLHF model.
  query      Semantic search over indexed applications.
  retrieve   Smart retrieval endpoint for automation/agents.
  status     Dashboard: counts by status, pending drafts.
  watch      Auto-rebuild when tracker CSV changes (polling).
  sync-feedback  Infer explicit outcomes from tracker fields and update RLHF.
  autonomous Continuous loop: build + sync-feedback on tracker changes.
  feedback   Record an outcome for an application; updates Thompson model.
  thumb      Quick vote alias for feedback (up/down -> outcome mapping).
  recommend  Suggest best targeting arms via Thompson Sampling.
  log        Append a manual event note.
  scan       Scan text artifacts for high-risk PII patterns.
"""

import argparse
import csv
import hashlib
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

try:
    import lancedb  # type: ignore
except Exception:  # pragma: no cover
    lancedb = None  # type: ignore

from memalign import (
    append_jsonl,
    build_long_memory_entry,
    build_short_memory_entry,
    load_jsonl,
    long_memory_scores,
    normalize_row,
    recency_scores,
    slug,
)
from shieldcortex import assert_no_high_risk_pii, gate_text
from rlhf import OUTCOME_REWARDS, ThompsonModel, VALID_OUTCOMES
from distributed import create_runtime
from structured_adapter import get_structured_adapter


ROOT = Path(__file__).resolve().parents[1]  # Resume/
RAG_DIR = ROOT / "rag"
DATA_DIR = RAG_DIR / "data"
LOG_DIR = RAG_DIR / "logs"
LANCEDB_DIR = RAG_DIR / "lancedb"

TRACKER_CSV = ROOT / "applications" / "job_applications" / "application_tracker.csv"
APPLICATIONS_DIR = ROOT / "applications"
ARMS_JSON = DATA_DIR / "arms.json"
SHORT_MEMORY_JSONL = DATA_DIR / "memory_short.jsonl"
LONG_MEMORY_JSONL = DATA_DIR / "memory_long.jsonl"
FEEDBACK_BATCH_LEDGER = DATA_DIR / "feedback_batch_seen.json"
SESSION_STATE_JSON = DATA_DIR / "session_state.json"
TRACKER_FEEDBACK_LEDGER = DATA_DIR / "tracker_feedback_seen.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_text_file(path: Path, *, max_bytes: int = 300_000) -> str:
    try:
        data = path.read_bytes()
    except Exception:
        return ""
    if len(data) > max_bytes:
        data = data[:max_bytes]
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _load_session_state() -> Dict:
    if not SESSION_STATE_JSON.exists():
        return {}
    try:
        data = json.loads(SESSION_STATE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_session_state(payload: Dict) -> None:
    SESSION_STATE_JSON.parent.mkdir(parents=True, exist_ok=True)
    SESSION_STATE_JSON.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _remember_recent_results(*, source: str, query: str, app_ids: List[str]) -> None:
    state = _load_session_state()
    state["last_results"] = {
        "source": source,
        "query": query,
        "app_ids": [str(x) for x in app_ids if str(x)],
        "ts": _utc_now(),
    }
    _save_session_state(state)


def _latest_app_id_from_index() -> Optional[str]:
    apps_path = DATA_DIR / "applications.jsonl"
    if not apps_path.exists():
        return None

    best_app_id: Optional[str] = None
    best_key: Tuple[str, str, str] = ("", "", "")
    with apps_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            app_id = str(rec.get("app_id", "") or "")
            if not app_id:
                continue
            key = (
                str(rec.get("date_applied", "") or ""),
                str(rec.get("updated_at", "") or ""),
                app_id,
            )
            if key > best_key:
                best_key = key
                best_app_id = app_id
    return best_app_id


def _resolve_thumb_app_id(app_id: Optional[str]) -> str:
    if app_id:
        return app_id

    state = _load_session_state()
    last = state.get("last_results", {})
    if isinstance(last, dict):
        app_ids = last.get("app_ids", [])
        if isinstance(app_ids, list):
            for candidate in app_ids:
                cid = str(candidate or "")
                if cid:
                    return cid

    fallback = _latest_app_id_from_index()
    if fallback:
        return fallback

    raise SystemExit(
        "Cannot infer app_id for thumb feedback. Run query/retrieve first or pass --app-id."
    )


def _gate_or_raise(text: str, *, context: str) -> str:
    result = gate_text(text, context=context)
    return result.text


def _collect_company_artifacts(company: str) -> List[Path]:
    company_dir = APPLICATIONS_DIR / slug(company)
    if not company_dir.exists():
        return []
    return [p for p in company_dir.rglob("*") if p.is_file()]


def _indexable_text_paths(paths: Iterable[Path]) -> List[Path]:
    exts = {".md", ".txt", ".html", ".csv"}
    return [p for p in paths if p.suffix.lower() in exts]


def _resolve_cover_letter(cover_letter_key: str, company: str) -> Optional[str]:
    """Try to resolve a cover letter key to an actual file path."""
    if not cover_letter_key:
        return None
    # Search in company-specific dir first, then global cover_letters/
    candidates = [
        APPLICATIONS_DIR / slug(company) / "cover_letters",
        ROOT / "cover_letters",
    ]
    for search_dir in candidates:
        if not search_dir.exists():
            continue
        for p in search_dir.iterdir():
            if p.is_file() and cover_letter_key.lower() in p.stem.lower():
                return str(p.relative_to(ROOT))
    return None


def _build_rag_text(n: Dict, company: str, role: str, artifacts: List[Path]) -> str:
    """Construct the RAG document text from structured fields + text artifacts."""
    parts: List[str] = [
        f"Company: {company}",
        f"Role: {role}",
        f"Status: {n.get('Status', '')}",
        f"Application Method: {n.get('application_method', '')}",
        f"Career Page URL: {n.get('Career Page URL', '')}",
        f"Tags: {';'.join(n.get('Tags', []))}",
        f"Notes: {n.get('Notes', '') or ''}",
        f"Cover Letter Used: {n.get('Cover Letter Used', '') or ''}",
    ]

    for p in sorted(_indexable_text_paths(artifacts)):
        rel = str(p.relative_to(ROOT))
        txt = _read_text_file(p)
        if not txt.strip():
            continue
        txt = _gate_or_raise(txt, context=rel)
        parts.append(f"\n---\nFILE: {rel}\n{txt}")

    combined = "\n".join(parts)
    combined = _gate_or_raise(combined, context=f"{company} / {role}")
    return combined


def _build_application_record(row: Dict[str, str]) -> Dict:
    n = normalize_row(row)
    company = str(n.get("Company", "")).strip()
    role = str(n.get("Role", "")).strip()

    artifacts = _collect_company_artifacts(company)
    evidence = [
        str(p.relative_to(ROOT))
        for p in artifacts
        if "/submissions/" in str(p).replace("\\", "/")
    ]
    resumes = [
        str(p.relative_to(ROOT))
        for p in artifacts
        if "/tailored_resumes/" in str(p).replace("\\", "/")
    ]
    cover_letters_dir = [
        str(p.relative_to(ROOT))
        for p in artifacts
        if "/cover_letters/" in str(p).replace("\\", "/")
    ]

    cl_key = str(n.get("Cover Letter Used", "") or "")
    cover_letter_path = _resolve_cover_letter(cl_key, company) or (
        cover_letters_dir[0] if cover_letters_dir else None
    )

    rag_text = _build_rag_text(n, company, role, artifacts)
    context_bundle_text = " | ".join(
        [
            f"company={company}",
            f"role={role}",
            f"status={n.get('Status', '')}",
            f"method={n.get('application_method', '')}",
            f"tags={' '.join(str(t) for t in n.get('Tags', []))}",
            f"location={n.get('Location', '')}",
            f"salary={n.get('Salary Range', '')}",
            f"signals={str(n.get('What Worked', '') or '')[:160]}",
        ]
    ).strip()
    context_bundle_text = _gate_or_raise(
        context_bundle_text, context=f"context_bundle:{company}/{role}"
    )

    return {
        "app_id": n["app_id"],
        "company": company,
        "role": role,
        "status": n.get("Status", ""),
        "date_applied": (n.get("Date Applied", "") or "").strip(),
        "follow_up_date": (n.get("Follow Up Date", "") or "").strip(),
        "url": (n.get("Career Page URL", "") or "").strip(),
        "application_method": n.get("application_method", "direct"),
        "tags": n.get("Tags", []),
        "notes": str(n.get("Notes", "") or ""),
        "artifacts": {
            "resumes": resumes,
            "cover_letters": cover_letters_dir,
            "cover_letter_used": cover_letter_path,
            "evidence": evidence,
        },
        "context_bundle_text": context_bundle_text,
        "rag_text": rag_text,
        "source_tracker_row": {k: v for k, v in n.items() if k not in ("rag_text",)},
        "updated_at": _utc_now(),
    }


# ---------------------------------------------------------------------------
# Embedding: field-boosted + bigram hashing (offline, deterministic)
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> List[str]:
    tokens = text.lower().split()
    bigrams = [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
    return tokens + bigrams


def _hashing_embedding(text: str, *, dims: int = 1536) -> np.ndarray:
    vec = np.zeros((dims,), dtype=np.float32)
    for tok in _tokenize(text):
        h_bytes = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        h = int.from_bytes(h_bytes, "little") % dims
        vec[h] += 1.0
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec /= norm
    return vec


def _record_embedding(rec: Dict, *, dims: int = 1536) -> np.ndarray:
    """Field-boosted embedding: key fields repeated for higher weight."""
    parts: List[str] = []
    parts += [rec.get("company", "")] * 5
    parts += [rec.get("role", "")] * 4
    parts += rec.get("tags", []) * 3
    parts += [rec.get("application_method", "")] * 2
    parts += [rec.get("status", "")]
    parts += [rec.get("notes", "")]
    parts += [rec.get("context_bundle_text", "")] * 2
    parts += [rec.get("rag_text", "")]
    return _hashing_embedding(" ".join(parts), dims=dims)


def _applications_table_schema():
    """Explicit schema for empty LanceDB table initialization."""
    import pyarrow as pa  # type: ignore

    return pa.schema(
        [
            pa.field("app_id", pa.string()),
            pa.field("company", pa.string()),
            pa.field("role", pa.string()),
            pa.field("status", pa.string()),
            pa.field("date_applied", pa.string()),
            pa.field("url", pa.string()),
            pa.field("application_method", pa.string()),
            pa.field("tags", pa.list_(pa.string())),
            pa.field("notes", pa.string()),
            pa.field(
                "artifacts",
                pa.struct(
                    [
                        pa.field("resumes", pa.list_(pa.string())),
                        pa.field("cover_letters", pa.list_(pa.string())),
                        pa.field("cover_letter_used", pa.string()),
                        pa.field("evidence", pa.list_(pa.string())),
                    ]
                ),
            ),
            pa.field("context_bundle_text", pa.string()),
            pa.field("text", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), 1536)),
            pa.field("updated_at", pa.string()),
        ]
    )


def _ensure_lancedb_indexes(table, *, has_data: bool) -> None:
    """Create retrieval indexes; log and continue on index creation failures."""
    try:
        table.create_fts_index(
            ["text", "context_bundle_text", "company", "role", "notes"],
            stem=True,
            remove_stop_words=True,
            replace=True,
        )
    except Exception as e:
        _append_event(None, "index_warn", f"FTS index skipped: {e}")

    for col in ("status", "application_method", "date_applied"):
        try:
            table.create_scalar_index(col, replace=True)
        except Exception as e:
            _append_event(None, "index_warn", f"Scalar index skipped for {col}: {e}")

    if has_data:
        try:
            table.create_index(
                metric="cosine", vector_column_name="vector", replace=True
            )
        except Exception as e:
            _append_event(None, "index_warn", f"Vector index skipped: {e}")

        try:
            table.optimize()
        except Exception as e:
            _append_event(None, "index_warn", f"Optimize skipped: {e}")


def _rrf_fuse(
    vector_rows: List[Dict], lexical_rows: List[Dict], *, rrf_k: int = 60
) -> List[Dict]:
    """Reciprocal rank fusion over dense and lexical candidate lists."""
    fused: Dict[str, Tuple[float, Dict]] = {}

    def _add(rows: List[Dict], label: str) -> None:
        for rank, row in enumerate(rows, start=1):
            app_id = str(row.get("app_id", ""))
            if not app_id:
                continue
            score = 1.0 / (rrf_k + rank)
            prior_score, prior_row = fused.get(app_id, (0.0, row))
            merged_row = dict(prior_row)
            merged_row.update(row)
            merged_row[f"_rank_{label}"] = rank
            fused[app_id] = (prior_score + score, merged_row)

    _add(vector_rows, "vec")
    _add(lexical_rows, "fts")

    ranked: List[Dict] = []
    for app_id, (score, row) in fused.items():
        out = dict(row)
        out["app_id"] = app_id
        out["_hybrid_score"] = score
        ranked.append(out)

    ranked.sort(key=lambda r: float(r.get("_hybrid_score", 0.0)), reverse=True)
    return ranked


def _native_hybrid_query(table, q: str, *, candidate_k: int) -> List[Dict]:
    """Try LanceDB native hybrid+rerank. Returns [] when unavailable."""
    try:
        from lancedb.rerankers import RRFReranker  # type: ignore

        query = table.search(
            q.strip(),
            query_type="hybrid",
            fts_columns=["text", "context_bundle_text", "company", "role", "notes"],
        )
        query = query.rerank(RRFReranker())
        return query.limit(candidate_k).to_list()
    except Exception:
        return []


def _manual_hybrid_query(
    table, q: str, q_vec: np.ndarray, *, candidate_k: int
) -> List[Dict]:
    """Fallback hybrid retrieval for custom-vector tables: dense + FTS + RRF."""
    vector_rows = table.search(q_vec, query_type="vector").limit(candidate_k).to_list()

    lexical_rows: List[Dict] = []
    try:
        lexical_rows = (
            table.search(
                q.strip(),
                query_type="fts",
                fts_columns=["text", "context_bundle_text", "company", "role", "notes"],
            )
            .limit(candidate_k)
            .to_list()
        )
    except Exception:
        lexical_rows = []

    if lexical_rows:
        return _rrf_fuse(vector_rows, lexical_rows)
    return vector_rows


def _display_score(row: Dict) -> float:
    if "_hybrid_score" in row:
        return float(row["_hybrid_score"])
    if "_score" in row:
        return float(row["_score"])
    if "_distance" in row:
        return 1.0 / (1.0 + max(0.0, float(row["_distance"])))
    return 0.0


def _lexical_overlap_score(query: str, row: Dict) -> float:
    q_terms = {t for t in query.lower().split() if t}
    if not q_terms:
        return 0.0
    tags = row.get("tags", [])
    tags_text = " ".join(str(t) for t in tags) if isinstance(tags, list) else ""
    text = " ".join(
        [
            str(row.get("company", "") or ""),
            str(row.get("role", "") or ""),
            str(row.get("application_method", "") or ""),
            tags_text,
            str(row.get("context_bundle_text", "") or ""),
            str(row.get("notes", "") or ""),
        ]
    ).lower()
    hit = sum(1 for t in q_terms if t in text)
    return min(1.0, hit / max(1, len(q_terms)))


def _normalize_base_score(raw: float) -> float:
    if raw <= 0.0:
        return 0.0
    if raw <= 1.0:
        return raw
    return raw / (1.0 + raw)


def _rlhf_prior_for_row(row: Dict, model: ThompsonModel) -> float:
    priors: List[float] = []
    method = str(row.get("application_method", "") or "")
    method_arm = model.arms.get(f"method:{method}")
    if method_arm is not None:
        priors.append(method_arm.mean_reward)
    tags = row.get("tags", [])
    if isinstance(tags, list):
        for tag in tags:
            arm = model.arms.get(f"cat:{tag}")
            if arm is not None:
                priors.append(arm.mean_reward)
    if not priors:
        return 0.5
    return float(sum(priors) / len(priors))


def _fuse_hybrid_rlhf_memory_scores(
    rows: List[Dict],
    *,
    query: str,
    model: ThompsonModel,
    short_scores: Dict[str, float],
    long_scores: Dict[str, float],
) -> List[Dict]:
    out: List[Dict] = []
    for row in rows:
        app_id = str(row.get("app_id", "") or "")
        base = _normalize_base_score(_display_score(row))
        lexical = _lexical_overlap_score(query, row)
        rlhf = _rlhf_prior_for_row(row, model)
        mem_short = short_scores.get(app_id, 0.0)
        mem_long = long_scores.get(app_id, 0.0)
        final = (
            0.48 * base
            + 0.22 * lexical
            + 0.20 * rlhf
            + 0.06 * mem_short
            + 0.04 * mem_long
        )
        boosted = dict(row)
        boosted["_base_score"] = base
        boosted["_lexical_score"] = lexical
        boosted["_rlhf_score"] = rlhf
        boosted["_memory_short"] = mem_short
        boosted["_memory_long"] = mem_long
        boosted["_final_score"] = final
        out.append(boosted)

    out.sort(key=lambda r: float(r.get("_final_score", 0.0)), reverse=True)
    return out


def _rebuild_long_memory(records: List[Dict]) -> None:
    LONG_MEMORY_JSONL.parent.mkdir(parents=True, exist_ok=True)
    ts = _utc_now()
    with LONG_MEMORY_JSONL.open("w", encoding="utf-8") as out:
        for rec in records:
            entry = build_long_memory_entry(rec, ts=ts)
            entry["summary"] = _gate_or_raise(
                str(entry.get("summary", "")),
                context=f"memory_long:{entry.get('app_id', 'unknown')}",
            )
            out.write(json.dumps(entry, ensure_ascii=True) + "\n")


def _load_tracker_rows() -> List[Dict[str, str]]:
    with TRACKER_CSV.open("r", newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if any(v.strip() for v in r.values())]


def _stable_shard_for_app(app_id: str, *, world_size: int) -> int:
    digest = hashlib.blake2b(app_id.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "little") % max(1, world_size)


def _build_records_from_rows(
    rows: List[Dict[str, str]], *, shard_rank: int = 0, shard_world_size: int = 1
) -> Tuple[List[Dict], List[str]]:
    records: List[Dict] = []
    errors: List[str] = []
    seen_ids: set = set()

    for row in rows:
        try:
            if shard_world_size > 1:
                n = normalize_row(row)
                app_id = str(n.get("app_id", ""))
                if (
                    _stable_shard_for_app(app_id, world_size=shard_world_size)
                    != shard_rank
                ):
                    continue
            rec = _build_application_record(row)
        except Exception as e:
            errors.append(
                f"Failed to ingest {row.get('Company', '')} / {row.get('Role', '')}: {e}"
            )
            continue

        if rec["app_id"] in seen_ids:
            continue
        seen_ids.add(rec["app_id"])
        records.append(rec)

    return records, errors


def _dedupe_records(records: List[Dict]) -> List[Dict]:
    out: List[Dict] = []
    seen: set = set()
    for rec in records:
        app_id = rec.get("app_id")
        if not app_id or app_id in seen:
            continue
        seen.add(app_id)
        out.append(rec)
    return out


def _write_records_to_jsonl(records: List[Dict]) -> None:
    apps_path = DATA_DIR / "applications.jsonl"
    with apps_path.open("w", encoding="utf-8") as out:
        for rec in records:
            out.write(json.dumps(rec, ensure_ascii=True) + "\n")
    _rebuild_long_memory(records)
    SHORT_MEMORY_JSONL.parent.mkdir(parents=True, exist_ok=True)
    SHORT_MEMORY_JSONL.touch(exist_ok=True)


def _index_records_in_lancedb(records: List[Dict]) -> int:
    if lancedb is None:
        _append_event(None, "build_skipped", "lancedb import failed; wrote JSONL only")
        print(f"Built {len(records)} records (JSONL only; lancedb unavailable)")
        return 0

    db = lancedb.connect(str(LANCEDB_DIR))
    items = []
    for rec in records:
        items.append(
            {
                "app_id": rec["app_id"],
                "company": rec["company"],
                "role": rec["role"],
                "status": rec["status"],
                "date_applied": rec["date_applied"],
                "url": rec["url"],
                "application_method": rec["application_method"],
                "tags": rec["tags"],
                "notes": rec["notes"],
                "artifacts": rec["artifacts"],
                "context_bundle_text": rec.get("context_bundle_text", ""),
                "text": rec["rag_text"],
                "vector": _record_embedding(rec),
                "updated_at": rec["updated_at"],
            }
        )

    if items:
        table = db.create_table("applications", data=items, mode="overwrite")
    else:
        table = db.create_table(
            "applications",
            data=[],
            schema=_applications_table_schema(),
            mode="overwrite",
        )
    _ensure_lancedb_indexes(table, has_data=bool(items))
    _append_event(None, "build_ok", f"Indexed {len(items)} applications")
    print(f"✅ Built {len(items)} applications (JSONL + LanceDB)")
    return len(items)


def _load_app_lookup() -> Dict[str, Dict[str, object]]:
    apps_path = DATA_DIR / "applications.jsonl"
    if not apps_path.exists():
        return {}
    out: Dict[str, Dict[str, object]] = {}
    with apps_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            app_id = str(rec.get("app_id", ""))
            if app_id:
                out[app_id] = rec
    return out


def _parse_outcome_from_row(row: Dict) -> Optional[str]:
    outcome = str(row.get("outcome", "") or "").strip().lower()
    if outcome in VALID_OUTCOMES:
        return outcome
    if str(row.get("type", "") or "").strip() == "outcome":
        msg = str(row.get("msg", "") or "")
        for token in msg.split():
            if token.startswith("outcome="):
                candidate = token.split("=", 1)[1].strip().lower()
                if candidate in VALID_OUTCOMES:
                    return candidate
    return None


def _load_feedback_seen_keys() -> set:
    if not FEEDBACK_BATCH_LEDGER.exists():
        return set()
    try:
        payload = json.loads(FEEDBACK_BATCH_LEDGER.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(payload, list):
        return set()
    return {str(x) for x in payload if isinstance(x, str)}


def _save_feedback_seen_keys(keys: set) -> None:
    FEEDBACK_BATCH_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    FEEDBACK_BATCH_LEDGER.write_text(
        json.dumps(sorted(keys), ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _load_seen_key_file(path: Path) -> set:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(payload, list):
        return set()
    return {str(x) for x in payload if isinstance(x, str)}


def _save_seen_key_file(path: Path, keys: set) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sorted(keys), ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _infer_tracker_outcome(row: Dict[str, str]) -> Optional[str]:
    n = normalize_row(row)
    status = str(n.get("Status", "") or "")

    response = str(row.get("Response", "") or "").strip().lower()
    stage = str(row.get("Interview Stage", "") or "").strip().lower()
    response_type = str(row.get("Response Type", "") or "").strip().lower()
    combined = " | ".join([response, stage, response_type])

    if status == "Offer" or "offer" in combined:
        return "offer"
    if status == "Rejected" or "reject" in combined:
        return "rejected"
    if (
        status == "Blocked"
        or "blocked" in combined
        or "captcha" in combined
        or "recaptcha" in combined
    ):
        return "blocked"

    if status != "Applied":
        return None

    interview_markers = (
        "interview",
        "phone screen",
        "screening",
        "onsite",
        "final round",
    )
    if any(m in combined for m in interview_markers):
        return "interview"

    response_markers = (
        "recruiter",
        "reached out",
        "reply",
        "responded",
        "response",
    )
    if any(m in combined for m in response_markers):
        return "response"
    return None


def sync_tracker_feedback() -> Tuple[int, int]:
    """Autonomously sync explicit tracker outcomes into RLHF arms (idempotent)."""
    rows = _load_tracker_rows()
    app_lookup = _load_app_lookup()
    seen_keys = _load_seen_key_file(TRACKER_FEEDBACK_LEDGER)
    model = ThompsonModel(ARMS_JSON)

    processed = 0
    skipped = 0

    for row in rows:
        n = normalize_row(row)
        app_id = str(n.get("app_id", "") or "")
        if not app_id:
            skipped += 1
            continue

        outcome = _infer_tracker_outcome(row)
        if outcome is None:
            skipped += 1
            continue

        status = str(n.get("Status", "") or "")
        response = str(row.get("Response", "") or "").strip()
        stage = str(row.get("Interview Stage", "") or "").strip()
        response_type = str(row.get("Response Type", "") or "").strip()
        dedupe_key = "|".join(
            [app_id, outcome, status, response, stage, response_type]
        ).lower()
        if dedupe_key in seen_keys:
            skipped += 1
            continue

        app_rec = app_lookup.get(app_id, {})
        tags = app_rec.get("tags", n.get("Tags", []))
        if not isinstance(tags, list):
            tags = []
        method = str(
            app_rec.get("application_method", n.get("application_method", "direct"))
            or "direct"
        )

        model.record_outcome(tags, method, outcome, save=False)
        _append_event(
            app_id,
            "tracker_outcome_sync",
            (
                f"outcome={outcome} status={status} method={method} "
                f"tags={tags} response_type={response_type}"
            ),
            outcome=outcome,
        )
        seen_keys.add(dedupe_key)
        processed += 1

    model.save()
    _save_seen_key_file(TRACKER_FEEDBACK_LEDGER, seen_keys)
    _append_event(
        None,
        "tracker_feedback_sync",
        f"processed={processed} skipped={skipped}",
    )
    print(f"✅ Synced tracker feedback: processed={processed} skipped={skipped}")
    return processed, skipped


def _compute_feedback_deltas(
    rows: List[Dict],
    app_lookup: Dict[str, Dict[str, object]],
    *,
    seen_keys: Optional[set] = None,
) -> Tuple[Dict[str, Dict[str, float]], int, int, set]:
    deltas: Dict[str, Dict[str, float]] = {}
    seen: set = set()
    already_seen = seen_keys if seen_keys is not None else set()
    new_seen: set = set()
    processed = 0
    skipped = 0

    def _bump(name: str, reward: float) -> None:
        item = deltas.setdefault(
            name, {"alpha": 0.0, "beta": 0.0, "pulls": 0.0, "total_reward": 0.0}
        )
        item["alpha"] += reward
        item["beta"] += 1.0 - reward
        item["pulls"] += 1.0
        item["total_reward"] += reward

    for row in rows:
        app_id = str(row.get("app_id", "") or "")
        outcome = _parse_outcome_from_row(row)
        ts = str(row.get("ts", "") or "")
        if not app_id or outcome is None:
            skipped += 1
            continue
        key = f"{app_id}|{outcome}|{ts}"
        if key in seen or key in already_seen:
            skipped += 1
            continue
        seen.add(key)
        new_seen.add(key)

        app = app_lookup.get(app_id)
        if app is None:
            skipped += 1
            continue

        tags = app.get("tags", [])
        method = str(app.get("application_method", "direct") or "direct")
        reward = OUTCOME_REWARDS[outcome]
        if isinstance(tags, list):
            for tag in tags:
                _bump(f"cat:{tag}", reward)
        _bump(f"method:{method}", reward)
        processed += 1

    return deltas, processed, skipped, new_seen


def _merge_feedback_deltas(
    chunks: List[Dict[str, Dict[str, float]]],
) -> Dict[str, Dict[str, float]]:
    merged: Dict[str, Dict[str, float]] = {}
    for chunk in chunks:
        for arm_name, d in chunk.items():
            out = merged.setdefault(
                arm_name, {"alpha": 0.0, "beta": 0.0, "pulls": 0.0, "total_reward": 0.0}
            )
            out["alpha"] += float(d.get("alpha", 0.0))
            out["beta"] += float(d.get("beta", 0.0))
            out["pulls"] += float(d.get("pulls", 0.0))
            out["total_reward"] += float(d.get("total_reward", 0.0))
    return merged


def _apply_feedback_deltas(
    model: ThompsonModel, merged: Dict[str, Dict[str, float]]
) -> None:
    for arm_name, d in merged.items():
        arm = model._get_or_create(
            arm_name
        )  # intentionally reuse existing arm lifecycle
        arm.alpha += float(d.get("alpha", 0.0))
        arm.beta += float(d.get("beta", 0.0))
        arm.pulls += int(round(float(d.get("pulls", 0.0))))
        arm.total_reward += float(d.get("total_reward", 0.0))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def build(
    *,
    dist_mode: str = "auto",
    dist_backend: str = "auto",
    world_size: Optional[int] = None,
) -> None:
    """Rebuild JSONL + LanceDB index from tracker CSV."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LANCEDB_DIR.mkdir(parents=True, exist_ok=True)

    runtime = create_runtime(
        mode=dist_mode, backend=dist_backend, requested_world_size=world_size
    )
    try:
        rows = _load_tracker_rows()

        if runtime.enabled:
            local_records, local_errors = _build_records_from_rows(
                rows, shard_rank=runtime.rank, shard_world_size=runtime.world_size
            )
            gathered_records = runtime.gather_objects(local_records)
            gathered_errors = runtime.gather_objects(local_errors)
            if not runtime.is_leader:
                return
            records = [r for chunk in (gathered_records or []) for r in (chunk or [])]
            errors = [e for chunk in (gathered_errors or []) for e in (chunk or [])]
        else:
            records, errors = _build_records_from_rows(rows)

        records = _dedupe_records(records)
        for err in errors:
            _append_event(None, "ingest_error", err)

        _write_records_to_jsonl(records)

        model = ThompsonModel(ARMS_JSON)
        if not model.arms:
            model.bootstrap_from_records(records)

        _index_records_in_lancedb(records)
    finally:
        runtime.finalize()


def query(q: str, *, k: int = 8) -> None:
    """Semantic search over indexed applications."""
    if lancedb is None:
        raise SystemExit(
            "lancedb unavailable; run build to generate JSONL, or install lancedb."
        )

    db = lancedb.connect(str(LANCEDB_DIR))
    table = db.open_table("applications")
    q_vec = _hashing_embedding(q.strip())
    candidate_k = max(k * 8, 40)

    # First try native LanceDB hybrid+rerank. If the table lacks an embedding
    # function (custom vector ingestion), fall back to manual dense+lexical RRF.
    results = _native_hybrid_query(table, q, candidate_k=candidate_k)
    if not results:
        results = _manual_hybrid_query(table, q, q_vec, candidate_k=candidate_k)

    model = ThompsonModel(ARMS_JSON)
    short_rows = load_jsonl(SHORT_MEMORY_JSONL)
    long_rows = load_jsonl(LONG_MEMORY_JSONL)
    short_boost = recency_scores(short_rows, now_ts=_utc_now())
    long_boost = long_memory_scores(long_rows)
    results = _fuse_hybrid_rlhf_memory_scores(
        results,
        query=q,
        model=model,
        short_scores=short_boost,
        long_scores=long_boost,
    )
    results = results[:k]

    if not results:
        print("No results.")
        _remember_recent_results(source="query", query=q, app_ids=[])
        return
    _remember_recent_results(
        source="query",
        query=q,
        app_ids=[str(r.get("app_id", "") or "") for r in results],
    )
    for r in results:
        method = r.get("application_method", "?")
        tags = ";".join(r.get("tags", []))
        score = float(r.get("_final_score", 0.0))
        print(
            f"- {r.get('app_id')} | {r.get('company'):<22} | "
            f"{r.get('role'):<45} | {r.get('status'):<8} | "
            f"score={score:0.4f} | {method:<12} | {tags}"
        )


def retrieve(
    q: str,
    *,
    k: int = 5,
    status: Optional[str] = None,
    method: Optional[str] = None,
    json_output: bool = False,
    envelope: bool = False,
    provider: str = "local",
) -> None:
    """Single smart retrieval endpoint for agents/automation."""
    if envelope and not json_output:
        raise SystemExit("--envelope requires --json")
    try:
        adapter = get_structured_adapter(provider)
        request_payload = adapter.normalize_retrieve_request(
            query=q,
            k=k,
            status=status,
            method=method,
        )
    except ValueError as e:
        raise SystemExit(str(e))

    q = str(request_payload.get("query", ""))
    k = int(request_payload.get("k", k))
    status = request_payload.get("status")
    method = request_payload.get("method")

    if lancedb is None:
        raise SystemExit(
            "lancedb unavailable; run build to generate JSONL, or install lancedb."
        )

    db = lancedb.connect(str(LANCEDB_DIR))
    table = db.open_table("applications")
    q_vec = _hashing_embedding(q.strip())
    candidate_k = max(k * 12, 60)

    results = _native_hybrid_query(table, q, candidate_k=candidate_k)
    if not results:
        results = _manual_hybrid_query(table, q, q_vec, candidate_k=candidate_k)

    if status:
        want = status.strip().lower()
        results = [r for r in results if str(r.get("status", "")).lower() == want]
    if method:
        want = method.strip().lower()
        results = [
            r for r in results if str(r.get("application_method", "")).lower() == want
        ]

    model = ThompsonModel(ARMS_JSON)
    short_rows = load_jsonl(SHORT_MEMORY_JSONL)
    long_rows = load_jsonl(LONG_MEMORY_JSONL)
    short_boost = recency_scores(short_rows, now_ts=_utc_now())
    long_boost = long_memory_scores(long_rows)
    ranked = _fuse_hybrid_rlhf_memory_scores(
        results,
        query=q,
        model=model,
        short_scores=short_boost,
        long_scores=long_boost,
    )[:k]

    payload = []
    for row in ranked:
        artifacts = row.get("artifacts", {})
        evidence = artifacts.get("evidence", []) if isinstance(artifacts, dict) else []
        payload.append(
            {
                "app_id": str(row.get("app_id", "") or ""),
                "company": str(row.get("company", "") or ""),
                "role": str(row.get("role", "") or ""),
                "status": str(row.get("status", "") or ""),
                "method": str(row.get("application_method", "") or ""),
                "tags": [str(t) for t in row.get("tags", [])]
                if isinstance(row.get("tags"), list)
                else [],
                "score": round(float(row.get("_final_score", 0.0)), 4),
                "context": str(row.get("context_bundle_text", "") or "")[:320],
                "evidence": [str(e) for e in evidence]
                if isinstance(evidence, list)
                else [],
            }
        )

    payload = adapter.validate_retrieve_results(payload)
    _remember_recent_results(
        source="retrieve",
        query=q,
        app_ids=[str(item.get("app_id", "") or "") for item in payload],
    )

    if json_output:
        print(
            adapter.render_retrieve_json(
                request=request_payload,
                results=payload,
                envelope=envelope,
            )
        )
        return

    if not payload:
        print("No results.")
        return
    for item in payload:
        tags = ";".join(item["tags"]) if isinstance(item.get("tags"), list) else ""
        print(
            f"- {item['app_id']} | {item['company']:<22} | {item['role']:<45} | "
            f"{item['status']:<8} | score={item['score']:.4f} | {item['method']:<12} | {tags}"
        )
        print(f"  context: {item['context']}")


def status() -> None:
    """Print application status dashboard."""
    if not DATA_DIR.joinpath("applications.jsonl").exists():
        print("No index found. Run: python3 cli.py build")
        return

    records: List[Dict] = []
    with DATA_DIR.joinpath("applications.jsonl").open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    counts: Dict[str, int] = defaultdict(int)
    drafts: List[Dict] = []
    blocked: List[Dict] = []

    for rec in records:
        s = rec.get("status", "Unknown")
        counts[s] += 1
        if s == "Draft":
            drafts.append(rec)
        elif s == "Blocked":
            blocked.append(rec)

    print("\n── Application Status Dashboard ─────────────────────────────")
    for st in ["Applied", "Draft", "Blocked", "Closed", "Rejected", "Offer"]:
        n = counts.get(st, 0)
        bar = "█" * n
        print(f"  {st:<10} {n:>3}  {bar}")
    other = {
        k: v
        for k, v in counts.items()
        if k not in ["Applied", "Draft", "Blocked", "Closed", "Rejected", "Offer"]
    }
    for k, v in sorted(other.items()):
        print(f"  {k:<10} {v:>3}")

    if drafts:
        print(f"\n── Pending Drafts ({len(drafts)}) ──────────────────────────────────")
        for d in drafts:
            tags = ";".join(d.get("tags", []))[:40]
            method = d.get("application_method", "?")
            print(
                f"  [{method:<10}] {d['company']:<22}  {d['role'][:45]:<45}  [{tags}]"
            )

    if blocked:
        print(f"\n── Blocked ({len(blocked)}) ────────────────────────────────────────")
        for b in blocked:
            print(
                f"  [{b.get('application_method', '?'):<10}] {b['company']:<22}  {b['role'][:45]}"
            )

    print(f"\n  Total: {len(records)} applications tracked")
    print()


def watch(interval: int = 10) -> None:
    """Poll tracker CSV and rebuild index on change."""
    last_mtime: Optional[float] = None
    print(f"Watching {TRACKER_CSV} every {interval}s. Ctrl-C to stop.")
    while True:
        try:
            mtime = TRACKER_CSV.stat().st_mtime
            if last_mtime is not None and mtime != last_mtime:
                print(f"[{_utc_now()}] Change detected — rebuilding...")
                build()
            last_mtime = mtime
        except FileNotFoundError:
            print(f"[{_utc_now()}] Tracker CSV not found: {TRACKER_CSV}")
        time.sleep(interval)


def autonomous(interval: int = 30) -> None:
    """Autonomous loop: rebuild index + sync tracker outcomes on tracker changes."""
    last_mtime: Optional[float] = None
    print(f"Autonomous mode watching {TRACKER_CSV} every {interval}s. Ctrl-C to stop.")
    while True:
        try:
            mtime = TRACKER_CSV.stat().st_mtime
            changed = last_mtime is None or mtime != last_mtime
            if changed:
                print(f"[{_utc_now()}] Change detected — build + sync-feedback...")
                build()
                sync_tracker_feedback()
                last_mtime = mtime
        except FileNotFoundError:
            print(f"[{_utc_now()}] Tracker CSV not found: {TRACKER_CSV}")
        except Exception as e:
            err = f"autonomous loop error: {e}"
            _append_event(None, "autonomous_error", err)
            print(f"[{_utc_now()}] {err}")
        time.sleep(interval)


def feedback(app_id: str, outcome: str) -> None:
    """Record an outcome for an application and update the Thompson model.

    Looks up the application by app_id, extracts its tags + method, and
    updates the RLHF arms accordingly.
    """
    if outcome not in VALID_OUTCOMES:
        raise SystemExit(
            f"Unknown outcome {outcome!r}. Valid: {sorted(VALID_OUTCOMES)}"
        )

    apps_path = DATA_DIR / "applications.jsonl"
    if not apps_path.exists():
        raise SystemExit("Index not built. Run: python3 cli.py build")

    rec: Optional[Dict] = None
    with apps_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("app_id") == app_id:
                rec = r
                break

    if rec is None:
        raise SystemExit(f"app_id {app_id!r} not found in index.")

    tags = rec.get("tags", [])
    method = rec.get("application_method", "direct")

    model = ThompsonModel(ARMS_JSON)
    model.record_outcome(tags, method, outcome)

    _append_event(
        app_id,
        "outcome",
        f"outcome={outcome} tags={tags} method={method}",
        outcome=outcome,
    )
    print(f"✅ Recorded outcome={outcome!r} for {rec['company']} / {rec['role']}")


def _outcome_from_thumb(vote: str) -> str:
    s = (vote or "").strip().lower()
    mapping = {
        "up": "response",
        "thumbs_up": "response",
        "+1": "response",
        "👍": "response",
        "down": "no_response",
        "thumbs_down": "no_response",
        "-1": "no_response",
        "👎": "no_response",
    }
    outcome = mapping.get(s)
    if outcome is None:
        raise SystemExit(
            "Unknown thumb vote. Use one of: up, down, thumbs_up, thumbs_down, 👍, 👎, +1, -1."
        )
    return outcome


def thumb_feedback(app_id: Optional[str], vote: str) -> None:
    outcome = _outcome_from_thumb(vote)
    target_app_id = _resolve_thumb_app_id(app_id)
    feedback(target_app_id, outcome)


def feedback_batch(
    *,
    source: str = "memory_short",
    dist_mode: str = "auto",
    dist_backend: str = "auto",
    world_size: Optional[int] = None,
) -> None:
    """Replay outcome events from JSONL into RLHF arms in batch."""
    app_lookup = _load_app_lookup()
    if not app_lookup:
        raise SystemExit("Index not built. Run: python3 cli.py build")

    if source == "events":
        rows = load_jsonl(LOG_DIR / "events.jsonl")
    else:
        rows = load_jsonl(SHORT_MEMORY_JSONL)
    seen_keys = _load_feedback_seen_keys()

    runtime = create_runtime(
        mode=dist_mode, backend=dist_backend, requested_world_size=world_size
    )
    try:
        if runtime.enabled:
            local_rows = [
                row
                for idx, row in enumerate(rows)
                if idx % runtime.world_size == runtime.rank
            ]
        else:
            local_rows = rows

        local_deltas, local_processed, local_skipped, local_seen = (
            _compute_feedback_deltas(local_rows, app_lookup, seen_keys=seen_keys)
        )

        if runtime.enabled:
            gathered_deltas = runtime.gather_objects(local_deltas)
            gathered_counts = runtime.gather_objects(
                {"processed": local_processed, "skipped": local_skipped}
            )
            gathered_seen = runtime.gather_objects(sorted(local_seen))
            if not runtime.is_leader:
                return
            merged = _merge_feedback_deltas(gathered_deltas or [])
            total_processed = sum(
                int(c.get("processed", 0)) for c in (gathered_counts or [])
            )
            total_skipped = sum(
                int(c.get("skipped", 0)) for c in (gathered_counts or [])
            )
            new_seen = {
                str(x)
                for chunk in (gathered_seen or [])
                for x in (chunk or [])
                if isinstance(x, str)
            }
        else:
            merged = local_deltas
            total_processed = local_processed
            total_skipped = local_skipped
            new_seen = local_seen

        model = ThompsonModel(ARMS_JSON)
        _apply_feedback_deltas(model, merged)
        model.save()
        if new_seen:
            _save_feedback_seen_keys(seen_keys.union(new_seen))
        _append_event(
            None,
            "feedback_batch",
            (
                f"source={source} processed={total_processed} skipped={total_skipped} "
                f"arms_touched={len(merged)} dist={runtime.enabled} new_seen={len(new_seen)}"
            ),
        )
        print(
            "✅ Replayed feedback batch: "
            f"processed={total_processed} skipped={total_skipped} arms={len(merged)}"
        )
    finally:
        runtime.finalize()


def recommend(*, k: int = 8) -> None:
    """Show top-k recommended targeting arms via Thompson Sampling."""
    model = ThompsonModel(ARMS_JSON)
    if not model.arms:
        print("No RLHF data yet. Run build first (bootstraps from tracker).")
        return

    top = model.recommend(k=k)
    stats_by_name = {s["arm"]: s for s in model.stats()}

    print("\n── Thompson Sampling Recommendations ──────────────────────────")
    print(f"  {'Arm':<30} {'Mean':>6}  {'Pulls':>5}  {'α':>5}  {'β':>5}")
    print("  " + "─" * 58)
    for arm_name, sampled_val in top:
        s = stats_by_name.get(arm_name, {})
        print(
            f"  {arm_name:<30} {s.get('mean_reward', 0):>6.3f}  "
            f"{s.get('pulls', 0):>5}  {s.get('alpha', 0):>5.1f}  {s.get('beta', 0):>5.1f}"
        )
    print()


def _append_event(
    app_id: Optional[str], event_type: str, msg: str, *, outcome: Optional[str] = None
) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    safe_msg = _gate_or_raise(msg, context="events.jsonl")
    payload = {
        "ts": _utc_now(),
        "app_id": app_id,
        "type": event_type,
        "msg": safe_msg,
    }
    with (LOG_DIR / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")

    short_entry = build_short_memory_entry(
        app_id=app_id,
        event_type=event_type,
        msg=safe_msg,
        ts=payload["ts"],
        outcome=outcome,
    )
    short_entry["text"] = _gate_or_raise(
        str(short_entry.get("text", "")), context="memory_short.jsonl"
    )
    append_jsonl(SHORT_MEMORY_JSONL, short_entry)


def log_event(app_id: str, event_type: str, msg: str) -> None:
    _append_event(app_id, event_type, msg)
    print(f"✅ Logged {event_type!r} for {app_id}")


def scan() -> None:
    """Scan text artifacts for high-risk PII patterns (DOB/SSN)."""
    findings = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in {".md", ".txt", ".html", ".csv", ".jsonl"}:
            continue
        txt = _read_text_file(p)
        if not txt:
            continue
        try:
            assert_no_high_risk_pii(txt, context=str(p.relative_to(ROOT)))
        except Exception as e:
            findings.append((str(p.relative_to(ROOT)), str(e)))

    if not findings:
        print("✅ No high-risk PII patterns detected in text artifacts.")
        return

    print("⚠️  PII findings:")
    for path, err in findings:
        print(f"  - {path}: {err}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Applications RAG CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    bp = sub.add_parser("build", help="Rebuild JSONL + LanceDB index")
    bp.add_argument(
        "--dist-mode",
        choices=["off", "auto", "on"],
        default="auto",
        help="Distributed mode for build (default: auto)",
    )
    bp.add_argument(
        "--dist-backend",
        default="auto",
        help="Distributed backend: auto|gloo|nccl (default: auto)",
    )
    bp.add_argument(
        "--world-size",
        type=int,
        default=None,
        help="Expected world size when distributed is enabled",
    )

    qp = sub.add_parser("query", help="Semantic search")
    qp.add_argument("q", help="Query text")
    qp.add_argument("-k", type=int, default=8, help="Max results (default 8)")

    rp2 = sub.add_parser("retrieve", help="Smart retrieval endpoint for automation")
    rp2.add_argument("q", help="Query text")
    rp2.add_argument("-k", type=int, default=5, help="Max results (default 5)")
    rp2.add_argument("--status", default=None, help="Optional status filter")
    rp2.add_argument("--method", default=None, help="Optional method filter")
    rp2.add_argument(
        "--provider",
        default="local",
        help="Structured response adapter: local|default|local_fusion (default: local)",
    )
    rp2.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON payload (best for agents/tooling)",
    )
    rp2.add_argument(
        "--envelope",
        action="store_true",
        help="Emit contract envelope (requires --json)",
    )

    sub.add_parser("status", help="Status dashboard")

    wp = sub.add_parser("watch", help="Auto-rebuild on CSV change")
    wp.add_argument("--interval", type=int, default=10, help="Poll interval in seconds")

    sfp = sub.add_parser(
        "sync-feedback",
        help="Autonomously infer outcomes from tracker and sync RLHF",
    )
    sfp.set_defaults(cmd="sync-feedback")

    ap_auto = sub.add_parser(
        "autonomous",
        help="Continuous autonomous tracking: build + sync-feedback on tracker changes",
    )
    ap_auto.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Poll interval in seconds (default: 30)",
    )

    fp = sub.add_parser("feedback", help="Record application outcome")
    fp.add_argument("--app-id", required=True, help="Application ID from query output")
    fp.add_argument(
        "--outcome",
        required=True,
        choices=sorted(VALID_OUTCOMES),
        help="Outcome signal",
    )

    fbp = sub.add_parser(
        "feedback-batch", help="Replay outcome events from JSONL into RLHF model"
    )
    fbp.add_argument(
        "--source",
        choices=["memory_short", "events"],
        default="memory_short",
        help="Source stream to replay (default: memory_short)",
    )
    fbp.add_argument(
        "--dist-mode",
        choices=["off", "auto", "on"],
        default="auto",
        help="Distributed mode for batch replay (default: auto)",
    )
    fbp.add_argument(
        "--dist-backend",
        default="auto",
        help="Distributed backend: auto|gloo|nccl (default: auto)",
    )
    fbp.add_argument(
        "--world-size",
        type=int,
        default=None,
        help="Expected world size when distributed is enabled",
    )

    tp = sub.add_parser("thumb", help="Quick thumb vote alias for feedback")
    tp.add_argument(
        "--app-id",
        required=False,
        default=None,
        help="Optional application ID from query output (auto-inferred when omitted)",
    )
    tp.add_argument(
        "--vote",
        required=True,
        help="up/down vote (supports: up, down, thumbs_up, thumbs_down, 👍, 👎, +1, -1)",
    )

    rp = sub.add_parser("recommend", help="Thompson Sampling arm recommendations")
    rp.add_argument("-k", type=int, default=8, help="Top-k arms to show")

    lp = sub.add_parser("log", help="Append a manual event note")
    lp.add_argument("--app-id", required=True)
    lp.add_argument("--type", required=True)
    lp.add_argument("--msg", required=True)

    sub.add_parser("scan", help="Scan for high-risk PII")

    args = ap.parse_args()

    if args.cmd == "build":
        build(
            dist_mode=args.dist_mode,
            dist_backend=args.dist_backend,
            world_size=args.world_size,
        )
    elif args.cmd == "query":
        query(args.q, k=args.k)
    elif args.cmd == "retrieve":
        retrieve(
            args.q,
            k=args.k,
            status=args.status,
            method=args.method,
            json_output=args.json,
            envelope=args.envelope,
            provider=args.provider,
        )
    elif args.cmd == "status":
        status()
    elif args.cmd == "watch":
        watch(args.interval)
    elif args.cmd == "sync-feedback":
        sync_tracker_feedback()
    elif args.cmd == "autonomous":
        autonomous(args.interval)
    elif args.cmd == "feedback":
        feedback(args.app_id, args.outcome)
    elif args.cmd == "feedback-batch":
        feedback_batch(
            source=args.source,
            dist_mode=args.dist_mode,
            dist_backend=args.dist_backend,
            world_size=args.world_size,
        )
    elif args.cmd == "thumb":
        thumb_feedback(args.app_id, args.vote)
    elif args.cmd == "recommend":
        recommend(k=args.k)
    elif args.cmd == "log":
        log_event(args.app_id, args.type, args.msg)
    elif args.cmd == "scan":
        scan()


if __name__ == "__main__":
    main()

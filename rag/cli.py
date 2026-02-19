#!/usr/bin/env python3
"""Applications RAG CLI.

Commands:
  build      Rebuild JSONL + LanceDB index from tracker CSV.
  query      Semantic search over indexed applications.
  status     Dashboard: counts by status, pending drafts.
  watch      Auto-rebuild when tracker CSV changes (polling).
  feedback   Record an outcome for an application; updates Thompson model.
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

from memalign import normalize_row, slug
from shieldcortex import assert_no_high_risk_pii, redact
from rlhf import ThompsonModel, VALID_OUTCOMES


ROOT = Path(__file__).resolve().parents[1]  # Resume/
RAG_DIR = ROOT / "rag"
DATA_DIR = RAG_DIR / "data"
LOG_DIR = RAG_DIR / "logs"
LANCEDB_DIR = RAG_DIR / "lancedb"

TRACKER_CSV = ROOT / "applications" / "job_applications" / "application_tracker.csv"
APPLICATIONS_DIR = ROOT / "applications"
ARMS_JSON = DATA_DIR / "arms.json"


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
        txt = redact(txt)
        assert_no_high_risk_pii(txt, context=rel)
        parts.append(f"\n---\nFILE: {rel}\n{txt}")

    combined = "\n".join(parts)
    combined = redact(combined)
    assert_no_high_risk_pii(combined, context=f"{company} / {role}")
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
            pa.field("text", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), 1536)),
            pa.field("updated_at", pa.string()),
        ]
    )


def _ensure_lancedb_indexes(table, *, has_data: bool) -> None:
    """Create retrieval indexes; log and continue on index creation failures."""
    try:
        table.create_fts_index(
            ["text", "company", "role", "notes"],
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
            fts_columns=["text", "company", "role", "notes"],
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
                fts_columns=["text", "company", "role", "notes"],
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


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def build() -> None:
    """Rebuild JSONL + LanceDB index from tracker CSV."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LANCEDB_DIR.mkdir(parents=True, exist_ok=True)

    with TRACKER_CSV.open("r", newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if any(v.strip() for v in r.values())]

    records: List[Dict] = []
    seen_ids: set = set()
    for r in rows:
        try:
            rec = _build_application_record(r)
        except Exception as e:
            _append_event(
                None,
                "ingest_error",
                f"Failed to ingest {r.get('Company', '')} / {r.get('Role', '')}: {e}",
            )
            continue
        if rec["app_id"] in seen_ids:
            continue  # Deduplicate on stable app_id
        seen_ids.add(rec["app_id"])
        records.append(rec)

    apps_path = DATA_DIR / "applications.jsonl"
    with apps_path.open("w", encoding="utf-8") as out:
        for rec in records:
            out.write(json.dumps(rec, ensure_ascii=True) + "\n")

    # Bootstrap Thompson model from existing records if arms.json is empty/absent
    model = ThompsonModel(ARMS_JSON)
    if not model.arms:
        model.bootstrap_from_records(records)

    if lancedb is None:
        _append_event(None, "build_skipped", "lancedb import failed; wrote JSONL only")
        print(f"Built {len(records)} records (JSONL only; lancedb unavailable)")
        return

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
    results = results[:k]

    if not results:
        print("No results.")
        return
    for r in results:
        method = r.get("application_method", "?")
        tags = ";".join(r.get("tags", []))
        score = _display_score(r)
        print(
            f"- {r.get('app_id')} | {r.get('company'):<22} | "
            f"{r.get('role'):<45} | {r.get('status'):<8} | "
            f"score={score:0.4f} | {method:<12} | {tags}"
        )


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
    )
    print(f"✅ Recorded outcome={outcome!r} for {rec['company']} / {rec['role']}")


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


def _append_event(app_id: Optional[str], event_type: str, msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": _utc_now(),
        "app_id": app_id,
        "type": event_type,
        "msg": redact(msg),
    }
    assert_no_high_risk_pii(payload["msg"], context="events.jsonl")
    with (LOG_DIR / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")


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

    sub.add_parser("build", help="Rebuild JSONL + LanceDB index")

    qp = sub.add_parser("query", help="Semantic search")
    qp.add_argument("q", help="Query text")
    qp.add_argument("-k", type=int, default=8, help="Max results (default 8)")

    sub.add_parser("status", help="Status dashboard")

    wp = sub.add_parser("watch", help="Auto-rebuild on CSV change")
    wp.add_argument("--interval", type=int, default=10, help="Poll interval in seconds")

    fp = sub.add_parser("feedback", help="Record application outcome")
    fp.add_argument("--app-id", required=True, help="Application ID from query output")
    fp.add_argument(
        "--outcome",
        required=True,
        choices=sorted(VALID_OUTCOMES),
        help="Outcome signal",
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
        build()
    elif args.cmd == "query":
        query(args.q, k=args.k)
    elif args.cmd == "status":
        status()
    elif args.cmd == "watch":
        watch(args.interval)
    elif args.cmd == "feedback":
        feedback(args.app_id, args.outcome)
    elif args.cmd == "recommend":
        recommend(k=args.k)
    elif args.cmd == "log":
        log_event(args.app_id, args.type, args.msg)
    elif args.cmd == "scan":
        scan()


if __name__ == "__main__":
    main()

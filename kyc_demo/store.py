"""Armazenamento do demo de KYC.

SQLite com o modelo de dados do design (`docs/kyc-fluxo-lgpd.md`):
subject, consent_record, kyc_verification e audit_log com hash encadeado.

Princípios refletidos no código:
- append-only para consentimento e auditoria;
- nunca persistir PII verdadeiro nem imagem: só vereditos, scores e hashes;
- estado atual do consentimento = última linha por (subject, scope).
"""

import os
import json
import sqlite3
import hashlib
import uuid
from datetime import datetime, timezone

DB_PATH = os.environ.get("KYC_DEMO_DB", "/tmp/kyc_demo.sqlite3")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS subject (
    id           TEXT PRIMARY KEY,
    external_ref TEXT UNIQUE,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS consent_record (
    id             TEXT PRIMARY KEY,
    subject_id     TEXT NOT NULL REFERENCES subject(id),
    scope          TEXT NOT NULL CHECK (scope IN ('cadastral','biometria')),
    granted        INTEGER NOT NULL,
    policy_version TEXT NOT NULL,
    terms_version  TEXT NOT NULL,
    legal_basis    TEXT NOT NULL,
    ip             TEXT,
    device_id      TEXT,
    user_agent     TEXT,
    prev_id        TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kyc_verification (
    id              TEXT PRIMARY KEY,
    subject_id      TEXT NOT NULL REFERENCES subject(id),
    status          TEXT NOT NULL CHECK (status IN ('aprovado','em_revisao','recusado')),
    reason          TEXT,
    decided_by      TEXT NOT NULL DEFAULT 'auto',
    cadastral_ok    INTEGER,
    liveness_ok     INTEGER,
    face_similarity REAL,
    risk_flags      TEXT,
    provider_refs   TEXT,
    consent_ref     TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event      TEXT NOT NULL,
    subject_id TEXT,
    payload    TEXT NOT NULL,
    prev_hash  TEXT,
    hash       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def reset_db() -> None:
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()


# --------------------------------------------------------------------------- #
# subject
# --------------------------------------------------------------------------- #
def create_subject(external_ref: str | None = None) -> str:
    sid = _uuid()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO subject (id, external_ref, created_at) VALUES (?,?,?)",
            (sid, external_ref or sid[:8], _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return sid


# --------------------------------------------------------------------------- #
# consentimento (append-only)
# --------------------------------------------------------------------------- #
def record_consent(subject_id, scope, granted, policy_version, terms_version,
                   legal_basis, ip=None, device_id=None, user_agent=None) -> str:
    cid = _uuid()
    conn = connect()
    try:
        prev = conn.execute(
            "SELECT id FROM consent_record WHERE subject_id=? AND scope=? "
            "ORDER BY created_at DESC LIMIT 1",
            (subject_id, scope),
        ).fetchone()
        conn.execute(
            "INSERT INTO consent_record (id, subject_id, scope, granted, "
            "policy_version, terms_version, legal_basis, ip, device_id, "
            "user_agent, prev_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, subject_id, scope, 1 if granted else 0, policy_version,
             terms_version, legal_basis, ip, device_id, user_agent,
             prev["id"] if prev else None, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def consent_is_valid(subject_id, scopes) -> bool:
    """Estado atual = última linha por escopo; todos os escopos pedidos
    precisam estar concedidos (granted=1)."""
    conn = connect()
    try:
        for scope in scopes:
            row = conn.execute(
                "SELECT granted FROM consent_record WHERE subject_id=? AND scope=? "
                "ORDER BY created_at DESC LIMIT 1",
                (subject_id, scope),
            ).fetchone()
            if not row or row["granted"] != 1:
                return False
        return True
    finally:
        conn.close()


def current_consent_ref(subject_id, scope="biometria") -> str | None:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id FROM consent_record WHERE subject_id=? AND scope=? "
            "ORDER BY created_at DESC LIMIT 1",
            (subject_id, scope),
        ).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# verificação
# --------------------------------------------------------------------------- #
def record_verification(subject_id, status, reason, decided_by, cadastral_ok,
                        liveness_ok, face_similarity, risk_flags,
                        provider_refs, consent_ref) -> str:
    vid = _uuid()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO kyc_verification (id, subject_id, status, reason, "
            "decided_by, cadastral_ok, liveness_ok, face_similarity, "
            "risk_flags, provider_refs, consent_ref, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (vid, subject_id, status, reason, decided_by,
             None if cadastral_ok is None else int(cadastral_ok),
             None if liveness_ok is None else int(liveness_ok),
             face_similarity, json.dumps(risk_flags), json.dumps(provider_refs),
             consent_ref, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return vid


def update_verification_decision(verification_id, status, reason, decided_by):
    conn = connect()
    try:
        conn.execute(
            "UPDATE kyc_verification SET status=?, reason=?, decided_by=? WHERE id=?",
            (status, reason, decided_by, verification_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_reviews():
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM kyc_verification WHERE status='em_revisao' "
            "ORDER BY created_at DESC"
        ).fetchall()]
    finally:
        conn.close()


def get_verification(verification_id):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM kyc_verification WHERE id=?", (verification_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# auditoria (hash encadeado, append-only)
# --------------------------------------------------------------------------- #
def audit(event, subject_id=None, payload=None) -> str:
    payload = payload or {}
    conn = connect()
    try:
        prev = conn.execute(
            "SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev_hash = prev["hash"] if prev else ""
        body = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256((body + prev_hash).encode("utf-8")).hexdigest()
        conn.execute(
            "INSERT INTO audit_log (event, subject_id, payload, prev_hash, hash, "
            "created_at) VALUES (?,?,?,?,?,?)",
            (event, subject_id, body, prev_hash, digest, _now()),
        )
        conn.commit()
        return digest
    finally:
        conn.close()


def audit_trail(limit=200):
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()]
    finally:
        conn.close()


def verify_audit_chain() -> bool:
    """Recalcula a cadeia de hashes; qualquer adulteração quebra a verificação."""
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id ASC").fetchall()
        prev_hash = ""
        for r in rows:
            digest = hashlib.sha256(
                (r["payload"] + prev_hash).encode("utf-8")
            ).hexdigest()
            if digest != r["hash"] or r["prev_hash"] != prev_hash:
                return False
            prev_hash = r["hash"]
        return True
    finally:
        conn.close()

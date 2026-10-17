"""Append-only Proof-of-Intent hash chain backed by SQLite."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Callable

from bg.contracts.hashing import GENESIS_HASH, canonical_json, sha256_hex
from bg.contracts.models import ChainVerification, Receipt, ReceiptAnchor
from bg.contracts.params import DEMO_NOW, DEMO_RECEIPT_KEY, RECEIPT_KEY_ENV

_PARTS = ("intent", "action", "simulation", "decision", "confirmation", "result")


def _receipt_hash(seq, receipt_id, action_id, created_at, intent_hash, action_hash, simulation_hash,
                  policy_hash, confirmation_hash, result_hash, prev, summary) -> str:
    return sha256_hex({
        "seq": seq, "receipt_id": receipt_id, "action_id": action_id, "created_at": created_at,
        "intent_hash": intent_hash, "action_hash": action_hash, "simulation_hash": simulation_hash,
        "policy_hash": policy_hash, "confirmation_hash": confirmation_hash, "result_hash": result_hash,
        "prev_receipt_hash": prev, "summary": summary,
    })


class SqliteReceiptStore:
    def __init__(self, path: str = ":memory:", clock: Callable[[], datetime] = lambda: DEMO_NOW,
                 key: str | bytes | None = None):
        if key is None:
            key = os.environ.get(RECEIPT_KEY_ENV) or DEMO_RECEIPT_KEY
        self._key = key.encode() if isinstance(key, str) else bytes(key)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        self._clock = clock
        self._db.execute("""CREATE TABLE IF NOT EXISTS receipts (
            seq INTEGER PRIMARY KEY, receipt_id TEXT UNIQUE NOT NULL, action_id TEXT NOT NULL,
            user_id TEXT, created_at TEXT NOT NULL, payload TEXT NOT NULL,
            intent_hash TEXT, action_hash TEXT, simulation_hash TEXT, policy_hash TEXT,
            confirmation_hash TEXT, result_hash TEXT, prev_receipt_hash TEXT NOT NULL,
            receipt_hash TEXT NOT NULL, summary TEXT NOT NULL)""")
        self._db.commit()

    # ------------------------------------------------------------------ write
    def append(self, *, action_id, intent, action, simulation, decision, confirmation, result,
               summary: str) -> Receipt:
        parts = dict(zip(_PARTS, (intent, action, simulation, decision, confirmation, result)))
        payload = {k: json.loads(canonical_json(v)) for k, v in parts.items()}
        user_id = payload["intent"].get("user_id") if isinstance(payload["intent"], dict) else None
        with self._lock:
            row = self._db.execute(
                "SELECT seq, receipt_hash FROM receipts ORDER BY seq DESC LIMIT 1").fetchone()
            seq, prev = (row[0] + 1, row[1]) if row else (1, GENESIS_HASH)
            created = self._clock().isoformat()
            rid = f"rcpt_{seq:04d}"
            h = {k: sha256_hex(payload[k]) for k in _PARTS}
            conf_h = None if payload["confirmation"] is None else h["confirmation"]
            rh = _receipt_hash(seq, rid, action_id, created, h["intent"], h["action"], h["simulation"],
                               h["decision"], conf_h, h["result"], prev, summary)
            self._db.execute(
                "INSERT INTO receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (seq, rid, action_id, user_id, created, canonical_json(payload), h["intent"], h["action"],
                 h["simulation"], h["decision"], conf_h, h["result"], prev, rh, summary))
            self._db.commit()
        return Receipt(seq=seq, receipt_id=rid, action_id=action_id, created_at=datetime.fromisoformat(created),
                       intent_hash=h["intent"], action_hash=h["action"], simulation_hash=h["simulation"],
                       policy_hash=h["decision"], confirmation_hash=conf_h, result_hash=h["result"],
                       prev_receipt_hash=prev, receipt_hash=rh, summary=summary)

    # ------------------------------------------------------------------ read
    _COLS = ("seq, receipt_id, action_id, created_at, intent_hash, action_hash, simulation_hash, "
             "policy_hash, confirmation_hash, result_hash, prev_receipt_hash, receipt_hash, summary")

    @staticmethod
    def _to_receipt(r) -> Receipt:
        return Receipt(seq=r[0], receipt_id=r[1], action_id=r[2], created_at=datetime.fromisoformat(r[3]),
                       intent_hash=r[4], action_hash=r[5], simulation_hash=r[6], policy_hash=r[7],
                       confirmation_hash=r[8], result_hash=r[9], prev_receipt_hash=r[10],
                       receipt_hash=r[11], summary=r[12])

    def list(self, user_id: str | None = None) -> list[Receipt]:
        with self._lock:
            if user_id is None:
                rows = self._db.execute(f"SELECT {self._COLS} FROM receipts ORDER BY seq").fetchall()
            else:
                rows = self._db.execute(
                    f"SELECT {self._COLS} FROM receipts WHERE user_id=? ORDER BY seq", (user_id,)).fetchall()
        return [self._to_receipt(r) for r in rows]

    def get(self, receipt_id: str) -> Receipt | None:
        with self._lock:
            r = self._db.execute(f"SELECT {self._COLS} FROM receipts WHERE receipt_id=?",
                                 (receipt_id,)).fetchone()
        return self._to_receipt(r) if r else None

    # ------------------------------------------------------------------ anchors
    def _sign(self, seq: int, receipt_hash: str) -> str:
        return hmac.new(self._key, f"{seq}:{receipt_hash}".encode(), hashlib.sha256).hexdigest()

    def anchor(self) -> ReceiptAnchor | None:
        """Signed anchor for the current head (None when the chain is empty)."""
        with self._lock:
            row = self._db.execute(
                "SELECT seq, receipt_hash FROM receipts ORDER BY seq DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return ReceiptAnchor(seq=row[0], receipt_hash=row[1], signature=self._sign(row[0], row[1]),
                             issued_at=self._clock())

    # ------------------------------------------------------------------ verify
    def verify_chain(self, anchor: ReceiptAnchor | None = None) -> ChainVerification:
        result = self._verify_links()
        if anchor is None or not result.valid:
            return result
        n = result.length
        if not hmac.compare_digest(self._sign(anchor.seq, anchor.receipt_hash), anchor.signature):
            return ChainVerification(valid=False, length=n, first_invalid_seq=None,
                                     detail="anchor signature invalid (forged or issued with another key)")
        with self._lock:
            row = self._db.execute("SELECT receipt_hash FROM receipts WHERE seq=?", (anchor.seq,)).fetchone()
        if row is None:
            return ChainVerification(
                valid=False, length=n, first_invalid_seq=min(anchor.seq, n + 1),
                detail=f"chain truncated: anchored receipt seq {anchor.seq} is missing (chain length {n})")
        if not hmac.compare_digest(row[0], anchor.receipt_hash):
            return ChainVerification(
                valid=False, length=n, first_invalid_seq=anchor.seq,
                detail=f"chain rewritten: receipt seq {anchor.seq} no longer matches the anchored hash")
        return ChainVerification(valid=True, length=n,
                                 detail=f"chain intact, consistent with anchor at seq {anchor.seq}")

    def _verify_links(self) -> ChainVerification:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {self._COLS}, payload, user_id FROM receipts ORDER BY seq").fetchall()
        n = len(rows)

        def bad(seq: int, why: str) -> ChainVerification:
            return ChainVerification(valid=False, length=n, first_invalid_seq=seq, detail=why)

        prev = GENESIS_HASH
        for i, r in enumerate(rows, start=1):
            seq = r[0]
            if seq != i:
                return bad(i, f"sequence gap: expected {i}, found {seq} (row deleted or reordered)")
            if r[10] != prev:
                return bad(seq, "prev_receipt_hash does not match the previous receipt")
            try:
                payload = json.loads(r[13])
                h = {k: sha256_hex(payload[k]) for k in _PARTS}
            except Exception:
                return bad(seq, "payload unreadable")
            conf_h = None if payload["confirmation"] is None else h["confirmation"]
            stored = dict(intent=r[4], action=r[5], simulation=r[6], decision=r[7], result=r[9])
            for k, v in stored.items():
                if h[k] != v:
                    return bad(seq, f"{k} payload does not match its hash")
            if conf_h != r[8]:
                return bad(seq, "confirmation payload does not match its hash")
            intent = payload["intent"]
            if r[14] != (intent.get("user_id") if isinstance(intent, dict) else None):
                return bad(seq, "user_id does not match the signed intent")
            expect = _receipt_hash(seq, r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[12])
            if expect != r[11]:
                return bad(seq, "receipt_hash does not match receipt fields")
            prev = r[11]
        return ChainVerification(valid=True, length=n, detail="chain intact")

# gen_req.py
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from jinja2 import Environment, FileSystemLoader, select_autoescape

from normalize import load_profile, apply_replacements


def safe_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        tmp.replace(path)
    except PermissionError:
        alt = Path(str(path).replace(".md", f".{datetime.now().strftime('%H%M%S')}.md"))
        tmp.replace(alt)
        print(f"[warn] {path} を更新できません（開かれている可能性）。別名で保存: {alt}")


def topic_of(text: str, topic_keys: List[List[Any]]) -> str:
    for key, kws in topic_keys:
        if any(kw in text for kw in kws):
            return str(key)
    return "その他"


def to_dt(ts: str):
    try:
        return datetime.fromisoformat(ts.replace("Z", ""))
    except Exception:
        return datetime.min


def is_tentative(text: str, tentative_words: List[str]) -> bool:
    return any(w in text for w in tentative_words)


def complete_decision_text(decision: Dict[str, Any], proposals_and_info: List[Dict[str, Any]]) -> Dict[str, Any]:
    s = decision.get("statement", "")
    if s.startswith("文言") or "その文言で決定" in s:
        for p in reversed(proposals_and_info):
            if any(k in p.get("statement", "") for k in ("エラーメッセージ", "文言", "メッセージ")):
                decision["statement"] = f"エラーメッセージ文言を確定: {p.get('statement','')}"
                break
    return decision


def norm_text(t: str) -> str:
    t = re.sub(r"\s+", "", t)
    t = t.replace("。", "").replace("、", "")
    return t


def dedup(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for r in items:
        key = (r.get("feature", ""), r.get("category", ""), norm_text(r.get("statement", "")))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def generate_markdown(
    normalized_data: Dict[str, Any],
    template_path: str,
    output_md_path: str,
    source_path: str,
) -> None:
    meta = normalized_data.get("meta", {}) or {}
    recs = normalized_data.get("records", []) or []

    profile_path = meta.get("profile_path") or "./profile.json"
    profile = load_profile(profile_path)

    topic_keys = profile.get("topic_keys") or []
    tentative_words = profile.get("tentative_words") or []

    for r in recs:
        r["statement"] = apply_replacements(str(r.get("statement", "")), profile)

    fr = [r for r in recs if r.get("category") == "functional"]
    nfr = [r for r in recs if r.get("category") == "nonfunctional"]
    dec = [r for r in recs if r.get("category") == "decision"]

    dec = [d for d in dec if not is_tentative(d.get("statement", ""), tentative_words)]

    # topic集約：ただし "その他" は集約しない（汎化性の議論で不利になりやすいので）
    latest_by_topic: Dict[str, Dict[str, Any]] = {}
    keep_others: List[Dict[str, Any]] = []

    for r in dec:
        t = topic_of(r.get("statement", ""), topic_keys)
        if t == "その他":
            keep_others.append(r)
            continue
        ts = (r.get("source") or {}).get("timestamp", "")
        if (t not in latest_by_topic) or (to_dt(ts) > to_dt((latest_by_topic[t].get("source") or {}).get("timestamp", ""))):
            latest_by_topic[t] = r

    dec = list(latest_by_topic.values()) + keep_others

    proposals_and_info = [*fr, *nfr]
    dec = [complete_decision_text(d, proposals_and_info) for d in dec]

    out_of_scope: List[Dict[str, Any]] = []
    kept_fr: List[Dict[str, Any]] = []
    kept_nfr: List[Dict[str, Any]] = []

    for r in fr:
        topic = topic_of(r.get("statement", ""), topic_keys)
        d = latest_by_topic.get(topic)
        if not d:
            kept_fr.append(r)
            continue

        ds = d.get("statement", "")
        s = r.get("statement", "")

        if ("除外" in ds) or ("範囲から除外" in ds):
            out_of_scope.append(r)
            continue

        if topic == "ボタン位置":
            if ("中央下" in ds and "右下" in s) or ("右下" in ds and "中央下" in s):
                continue

        kept_fr.append(r)

    for r in nfr:
        topic = topic_of(r.get("statement", ""), topic_keys)
        d = latest_by_topic.get(topic)
        if d:
            ds = d.get("statement", "")
            if ("除外" in ds) or ("範囲から除外" in ds):
                out_of_scope.append(r)
                continue
        kept_nfr.append(r)

    fr, nfr = kept_fr, kept_nfr

    fr = dedup(fr)
    nfr = dedup(nfr)
    dec = dedup(dec)

    def to_dt_from_rec(r):
        return to_dt((r.get("source") or {}).get("timestamp", ""))

    dec = sorted(dec, key=to_dt_from_rec, reverse=True)

    tpl_path = Path(template_path)
    env = Environment(
        loader=FileSystemLoader(str(tpl_path.parent)),
        autoescape=select_autoescape(),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    tpl = env.get_template(tpl_path.name)

    rendered = tpl.render(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        source=source_path,
        labeled_meta=meta,
        fr=fr,
        nfr=nfr,
        dec=dec,
        out_of_scope=out_of_scope,
    )

    safe_write_text(Path(output_md_path), rendered)
    print(f"[ok] 仕様書を出力: {output_md_path}")
    print(f"[debug] FR:{len(fr)} NFR:{len(nfr)} DEC:{len(dec)} OOS:{len(out_of_scope)}")

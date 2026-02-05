# normalize.py
from __future__ import annotations

import json
import os
import re
import hashlib
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from openai import OpenAI


DEFAULT_PROFILE: Dict[str, Any] = {
    "text_replacements": [
        {"pattern": r"\bdesc\b", "replace": "説明"},
        {"pattern": r"\bques\b|\bquestion\b|\bq\b", "replace": "質問"},
        {"pattern": r"\breq\b|\brequirement\b", "replace": "要件"},
        {"pattern": r"\bdec\b|\bdecision\b", "replace": "決定"},
    ],
    "non_requirement_phrases": [
        "それ賛成です", "賛成です", "ありがとうございます", "了解です", "お願いします",
        "問題ありません", "助かります", "なるほど", "了解しました", "承知しました"
    ],
    "tentative_words": ["一旦", "暫定", "候補", "保留", "検討中", "仮"],
    "feature_map": {
        "ログイン": ["ログイン", "初期表示", "ボタン"],
        "通知": ["通知", "トースト"],
        "権限": ["管理者", "編集", "削除", "権限"],
        "性能": ["3秒", "表示", "パフォーマンス", "応答時間"],
        "文言": ["文言", "エラーメッセージ", "メッセージ"],
        "保持": ["保持期間", "ログ", "90日", "保存期間"],
        "チュートリアル": ["チュートリアル", "オンボーディング"],
        "ボタン位置": ["ボタン", "右下", "中央下"]
    },
    "nonfunctional_features": ["性能", "保持"],
    "topic_keys": [
        ["通知", ["通知", "トースト"]],
        ["ログイン", ["ログイン", "初期表示", "サインイン"]],
        ["権限", ["管理者", "編集", "削除", "権限"]],
        ["性能", ["3秒", "パフォーマンス", "表示", "応答時間"]],
        ["文言", ["エラーメッセージ", "文言", "メッセージ"]],
        ["保持", ["保持期間", "ログ", "90日", "保存期間"]],
        ["チュートリアル", ["チュートリアル", "オンボーディング", "ガイド"]],
        ["ボタン位置", ["ボタン", "右下", "中央下", "中央下寄せ"]],
    ],
}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_profile_exists(profile_path: str) -> None:
    p = Path(profile_path)
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(DEFAULT_PROFILE, ensure_ascii=False, indent=2), encoding="utf-8")


def load_profile(profile_path: str) -> Dict[str, Any]:
    ensure_profile_exists(profile_path)
    return json.loads(Path(profile_path).read_text(encoding="utf-8"))


def apply_replacements(text: str, profile: Dict[str, Any]) -> str:
    s = text
    for r in profile.get("text_replacements", []):
        try:
            s = re.sub(r.get("pattern", ""), r.get("replace", ""), s, flags=re.IGNORECASE)
        except re.error:
            continue
    return s


def is_non_requirement(text: str, profile: Dict[str, Any]) -> bool:
    return any(p in text for p in profile.get("non_requirement_phrases", []))


def guess_feature(text: str, profile: Dict[str, Any]) -> str:
    fmap: Dict[str, List[str]] = profile.get("feature_map", {}) or {}
    for feat, kws in fmap.items():
        if any(kw in text for kw in kws):
            return feat
    return "その他"


def next_id(seq: List[Dict[str, Any]], prefix: str) -> str:
    n = len([x for x in seq if str(x.get("id", "")).startswith(prefix)]) + 1
    return f"{prefix}-{n:03d}"


def _extract_json_text(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start : end + 1]
    return s


def llm_normalize(
    client: OpenAI,
    model: str,
    temperature: float,
    category: str,
    feature: str,
    utterance: str,
) -> Dict[str, Any]:
    sys = (
        "あなたは要件定義のテクニカルライターです。"
        "口語の発話を、仕様書調の定義文とGherkin風の受け入れ条件に変換します。"
        "数値・主体・条件を明確化し、指定スキーマのJSONのみを返してください。"
    )
    usr = f"category: {category}\nfeature: {feature}\nutterance: {utterance}\n"

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "normalized_req",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["statement", "acceptance_criteria"],
                "additionalProperties": False,
            },
        },
    }

    try:
        r = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": usr}],
            response_format=response_format,
        )
        content = r.choices[0].message.content or ""
        return json.loads(_extract_json_text(content))
    except TypeError:
        r = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": usr}],
        )
        content = r.choices[0].message.content or ""
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return {"statement": utterance, "acceptance_criteria": []}
        return json.loads(_extract_json_text(m.group(0)))


def normalize_records(
    classified_path: str,
    output_path: str,
    profile_path: str,
    model: str,
    temperature: float,
) -> Dict[str, Any]:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY が未設定です（.env または環境変数に設定してください）")

    profile = load_profile(profile_path)
    client = OpenAI(api_key=api_key)

    rows: List[Dict[str, Any]] = json.load(open(classified_path, encoding="utf-8"))
    out: List[Dict[str, Any]] = []

    nonfunc = set(profile.get("nonfunctional_features", []))

    for d in rows:
        label = d.get("label")
        if label not in ("decision", "proposal", "other", "question", "chitchat"):
            continue

        text_raw = str(d.get("text", "")).strip()
        text = apply_replacements(text_raw, profile)

        if label == "chitchat" or is_non_requirement(text, profile):
            continue

        feature = guess_feature(text, profile)
        cat = "functional" if feature not in nonfunc else "nonfunctional"
        if label == "decision":
            cat = "decision"

        norm = llm_normalize(client, model, temperature, cat, feature, text)

        rec = {
            "id": "",
            "feature": feature,
            "category": cat,
            "statement": norm.get("statement", text),
            "acceptance_criteria": norm.get("acceptance_criteria", []),
            "priority": "Must" if label == "decision" else "Should",
            "status": "決定" if label == "decision" else ("検討中" if label in ("proposal", "question") else "情報"),
            "source": {"speaker": d.get("speaker", ""), "timestamp": d.get("timestamp", "")},
            "rationale": d.get("label_reason", ""),
            "dependencies": [],
            "supersedes": [],
            "tags": [d.get("topic", "その他")],
        }
        out.append(rec)

    fr = [x for x in out if x["category"] == "functional"]
    nfr = [x for x in out if x["category"] == "nonfunctional"]
    dec = [x for x in out if x["category"] == "decision"]

    for x in fr:
        x["id"] = next_id(fr, "FR")
    for x in nfr:
        x["id"] = next_id(nfr, "NFR")
    for x in dec:
        x["id"] = next_id(dec, "DEC")

    result = {
        "meta": {
            "input_sha256": sha256_file(classified_path),
            "model": model,
            "temperature": temperature,
            "profile_path": profile_path,
            "profile_sha256": sha256_file(profile_path),
        },
        "records": out,
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[ok] 正規化要件を出力: {output_path}")
    return result

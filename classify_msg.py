# classify_msg.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from openai import OpenAI

from normalize import load_profile, apply_replacements


LABELS = ["decision", "proposal", "question", "chitchat", "other"]


def _extract_json_text(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start : end + 1]
    return s


def classify_messages(
    input_path: str,
    output_path: str,
    profile_path: str,
    model: str,
    temperature: float,
) -> None:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY が未設定です（.env または環境変数に設定してください）")

    profile = load_profile(profile_path)
    utterances: List[Dict[str, Any]] = json.load(open(input_path, encoding="utf-8"))

    # 事前のワード補正（desc/ques 等の吸収）
    enriched = []
    for i, u in enumerate(utterances):
        text = apply_replacements(str(u.get("text", "")), profile)
        enriched.append(
            {
                "index": i,
                "speaker": str(u.get("speaker", "")),
                "timestamp": str(u.get("timestamp", "")),
                "text": text,
            }
        )

    system_prompt = (
        "あなたは議事録を分類するAIです。\n"
        "各発言を次の5つのラベルで分類し、指定スキーマのJSONのみを返してください。\n"
        "- decision: 方針/仕様が明確に決まった発言\n"
        "- proposal: 提案・要望\n"
        "- question: 確認・質問\n"
        "- chitchat: 雑談\n"
        "- other: 上記以外\n"
        "topic は短い名詞句（例: 認証/通知/画面/権限/運用/表示/文言/性能 等）。該当が無ければ「その他」。\n"
        "reason は短い根拠。\n"
        "回答は、必ず日本語で返すようにしてください。"
    )

    user_prompt = (
        "以下の配列の各要素について、index を保持したまま label/topic/reason を付与してください。\n"
        "出力は JSON のみ。\n\n"
        "入力:\n" + json.dumps(enriched, ensure_ascii=False, indent=2)
    )

    client = OpenAI(api_key=api_key)

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "classification",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer"},
                                "label": {"type": "string", "enum": LABELS},
                                "topic": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["index", "label", "topic", "reason"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        },
    }

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            response_format=response_format,
        )
        content = resp.choices[0].message.content or ""
        obj = json.loads(_extract_json_text(content))
    except TypeError:
        # 古いSDK想定：デバッグ保存
        resp = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        )
        content = resp.choices[0].message.content or ""
        Path("./_debug").mkdir(parents=True, exist_ok=True)
        Path("./_debug/last_classify_output.txt").write_text(content, encoding="utf-8")
        obj = json.loads(_extract_json_text(content))

    items = obj.get("items", []) if isinstance(obj, dict) else []
    by_index = {int(it["index"]): it for it in items if isinstance(it, dict) and "index" in it}

    for i, u in enumerate(utterances):
        it = by_index.get(i)
        u["label"] = (it or {}).get("label", "other")
        u["topic"] = (it or {}).get("topic", "その他")
        u["label_reason"] = (it or {}).get("reason", "")
        u["_text_norm"] = enriched[i]["text"]

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(utterances, f, ensure_ascii=False, indent=2)

    print(f"[ok] 分類結果を保存: {output_path}")

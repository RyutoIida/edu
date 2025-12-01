import json, re, os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
import config

Path(config.OUT_DIR).mkdir(parents=True, exist_ok=True)

FEATURE_MAP = {
    "ログイン": ["ログイン", "初期表示", "ボタン"],
    "通知": ["通知", "トースト"],
    "権限": ["管理者", "編集", "削除"],
    "性能": ["3秒", "表示", "パフォーマンス"],
    "文言": ["文言", "エラーメッセージ"],
    "保持": ["保持期間", "ログ", "90日"],
    "チュートリアル": ["チュートリアル", "オンボーディング"],
    "ボタン位置": ["ボタン", "右下", "中央下"]
}

# 会話の相槌・同意など「要件でない文」を除外
NON_REQUIREMENT_PATTERNS = (
    "それ賛成です","賛成です","ありがとうございます","了解です","お願いします",
    "問題ありません","助かります","なるほど","お願いします。","了解しました","承知しました"
)
def is_non_requirement(text: str) -> bool:
    return any(p in text for p in NON_REQUIREMENT_PATTERNS)

def guess_feature(text: str) -> str:
    for feat, kws in FEATURE_MAP.items():
        if any(kw in text for kw in kws):
            return feat
    return "その他"

def next_id(seq, prefix):
    return f"{prefix}-{len([x for x in seq if x['id'].startswith(prefix)])+1:03d}"

# LLMで“仕様書調の文”と“受け入れ条件”を生成
def llm_normalize(client, category, feature, text):
    sys = ("あなたは要件定義のテクニカルライターです。"
           "口語の発話を、仕様書調の定義文とGherkin風の受け入れ条件に変換します。"
           "数値・主体・条件を明確化し、JSONのみを返してください。")
    usr = f"""category:{category}
feature:{feature}
utterance:{text}

出力JSONスキーマ:
{{
  "statement": "仕様書調の一文（〜とする/〜である/〜しなければならない）",
  "acceptance_criteria": ["Given ... When ... Then ...", "...（必要なら複数）"]
}}"""
    r = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL","gpt-3.5-turbo"),
        messages=[{"role":"system","content":sys},{"role":"user","content":usr}],
        temperature=0.2
    )
    m = re.search(r'\{.*\}', r.choices[0].message.content, re.DOTALL)
    return json.loads(m.group(0)) if m else {"statement": text, "acceptance_criteria":[]}

def main():
    load_dotenv()
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # ❌ 以前：config.DATA_DIR に JSON を上書きしていた → ここを修正
    rows = json.load(open(config.INPUT, encoding="utf-8"))

    out = []
    for d in rows:
        label = d.get("label")
        if label not in ("decision","proposal","other","question","chitchat"):
            continue

        text = d.get("text","").strip()
        if label == "chitchat" or is_non_requirement(text):
            continue  # 雑談 & 相槌などは除外

        feature = guess_feature(text)
        cat = "functional" if feature not in ("性能","保持") else "nonfunctional"
        if label == "decision":
            cat = "decision"

        norm = llm_normalize(client, cat, feature, text)
        prefix = "DEC" if cat=="decision" else ("NFR" if cat=="nonfunctional" else "FR")
        rec = {
            "id": "",
            "feature": feature,
            "category": cat,  # functional / nonfunctional / decision
            "statement": norm["statement"],
            "acceptance_criteria": norm.get("acceptance_criteria", []),
            "priority": "Must" if label=="decision" else "Should",
            "status": "決定" if label=="decision" else ("検討中" if label in ("proposal","question") else "情報"),
            "source": {"speaker": d.get("speaker",""), "timestamp": d.get("timestamp","")},
            "rationale": "",
            "dependencies": [],
            "supersedes": [],
            "tags": []
        }
        out.append(rec)

    # 採番
    fr  = [x for x in out if x["category"]=="functional"]
    nfr = [x for x in out if x["category"]=="nonfunctional"]
    dec = [x for x in out if x["category"]=="decision"]
    for x in fr:  x["id"]  = next_id(fr, "FR")
    for x in nfr: x["id"]  = next_id(nfr,"NFR")
    for x in dec: x["id"]  = next_id(dec,"DEC")

    with open(config.NORMALIZE,"w",encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[ok] 正規化要件を出力: {config.NORMALIZE}")

if __name__ == "__main__":
    main()
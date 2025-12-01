import os, json
from pathlib import Path
from datetime import datetime
from jinja2 import Environment, FileSystemLoader, select_autoescape
import config

# 提案表示　設定(1: 表示, 0: 非表示)
INCLUDE_PROPOSALS = os.getenv("INCLUDE_PROPOSALS", "1") == "1"

Path(config.OUT_DIR).mkdir(parents=True, exist_ok=True)

def parse_ts(ts: str):
    try:
        return datetime.fromisoformat(ts.replace("Z", ""))
    except Exception:
        return None

with open(config.CLASSIFIED_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

# フィルタリング/整形
TENTATIVE_WORDS = ("一旦", "暫定", "候補", "保留", "検討中", "仮")

def is_tentative(text: str) -> bool:
    return any(w in text for w in TENTATIVE_WORDS)

def is_summary_like(text: str) -> bool:
    return text.startswith(("まとめます", "総括", "振り返り"))

def pick_with_meta(label):
    return [
        (d.get("text","").strip(), d.get("speaker",""), d.get("timestamp",""))
        for d in data if d.get("label") == label
    ]

decisions_meta = [
    (t, s, ts) for (t, s, ts) in pick_with_meta("decision")
    if not is_tentative(t) and not is_summary_like(t)
]
proposals_meta = pick_with_meta("proposal")
questions_meta = pick_with_meta("question")

TOPIC_KEYS = [
    ("通知", ("通知", "トースト")),
    ("ログイン", ("ログイン", "初期表示")),
    ("権限", ("管理者", "編集", "削除")),
    ("性能", ("3秒", "パフォーマンス", "表示")),
    ("文言", ("エラーメッセージ", "文言")),
    ("保持", ("保持期間", "ログ", "90日")),
    ("チュートリアル", ("チュートリアル", "オンボーディング")),
    ("ボタン位置", ("ボタン", "右下", "中央下")),
]
def topic_of(text: str) -> str:
    for key, kws in TOPIC_KEYS:
        if any(kw in text for kw in kws):
            return key
    return "その他"

def latest_by_topic(items):
    by_topic = {}
    for t, s, ts in items:
        by_topic[topic_of(t)] = (t, s, ts)
    return [by_topic[k] for k in sorted(by_topic.keys())]

decisions_meta = latest_by_topic(decisions_meta)

# 決定の最新時刻をトピックごとにマップ化
decision_latest_ts = {}
for t, s, ts in decisions_meta:
    topic = topic_of(t)
    dt = parse_ts(ts)
    if dt and (topic not in decision_latest_ts or dt > decision_latest_ts[topic]):
        decision_latest_ts[topic] = dt

# 問い合わせ時刻より後に「同トピックの決定」があれば未解決から除外
unresolved_questions_meta = []
for t, s, ts in questions_meta:
    q_topic = topic_of(t)
    q_dt = parse_ts(ts)
    decided_after = (q_topic in decision_latest_ts) and q_dt and (decision_latest_ts[q_topic] >= q_dt)
    if not decided_after:
        unresolved_questions_meta.append((t, s, ts))
# 重複を順序保持で除去
def format_item(t, s, ts):
    who = s or "unknown"
    when = ts or ""
    return f"{t} ({who} {when})".strip()

def uniq(seq):
    seen=set(); out=[]
    for x in seq:
        if x not in seen:
            seen.add(x); out.append(x)
    return out

decisions = uniq([format_item(*x) for x in decisions_meta])
proposals = uniq([format_item(*x) for x in proposals_meta])
questions = uniq([format_item(*x) for x in unresolved_questions_meta])

env = Environment(
    loader=FileSystemLoader("templates"),
    autoescape=select_autoescape(),
    trim_blocks=True,
    lstrip_blocks=True
)
tpl = env.get_template("spec.md.j2")
rendered = tpl.render(
    generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    source=config.INPUT_FILE,
    decisions=decisions,
    proposals=proposals if INCLUDE_PROPOSALS else [],
    questions=questions,
    show_proposals=INCLUDE_PROPOSALS,
)

with open(config.SPEC_MD, "w", encoding="utf-8") as f:
    f.write(rendered)

print(f"[ok] Markdown仕様書を出力しました: {config.SPEC_MD}")
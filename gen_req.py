import json, re
from pathlib import Path
from datetime import datetime
from jinja2 import Environment, FileSystemLoader, select_autoescape
import config

# 書き込み安全化（.tmp → 置換）
def safe_write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        tmp.replace(path)
    except PermissionError:
        alt = Path(str(path).replace(".md", f".{datetime.now().strftime('%H%M%S')}.md"))
        tmp.replace(alt)
        print(f"[warn] {path} を更新できません（開かれている可能性）。別名で保存: {alt}")

# トピック判定（決定・FR/NFR の整合に使用）
TOPIC_KEYS = [
    ("通知", ("通知","トースト")),
    ("ログイン", ("ログイン","初期表示","サインイン")),
    ("権限", ("管理者","編集","削除","権限")),
    ("性能", ("3秒","パフォーマンス","表示","応答時間")),
    ("文言", ("エラーメッセージ","文言","メッセージ")),
    ("保持", ("保持期間","ログ","90日","保存期間")),
    ("チュートリアル", ("チュートリアル","オンボーディング","ガイド")),
    ("ボタン位置", ("ボタン","右下","中央下","中央下寄せ"))
]
def topic_of(text: str) -> str:
    for key, kws in TOPIC_KEYS:
        if any(kw in text for kw in kws):
            return key
    return "その他"

def to_dt(ts: str):
    try:
        return datetime.fromisoformat(ts.replace("Z",""))
    except Exception:
        return datetime.min

TENTATIVE_WORDS = ("一旦", "暫定", "候補", "保留", "検討中", "仮")
def is_tentative(text: str) -> bool:
    return any(w in text for w in TENTATIVE_WORDS)

def complete_decision_text(decision, proposals_and_info):
    s = decision["statement"]
    if s.startswith("文言") or "その文言で決定" in s:
        # 直近の“文言系”提案/情報を探す
        for p in reversed(proposals_and_info):
            if any(k in p["statement"] for k in ("エラーメッセージ","文言","メッセージ")):
                decision["statement"] = f"エラーメッセージ文言を確定: {p['statement']}"
                break
    return decision

def norm_text(t: str) -> str:
    t = re.sub(r"\s+", "", t)
    t = t.replace("。","").replace("、","")
    return t

def dedup(items):
    seen = set(); out = []
    for r in items:
        key = (r.get("feature",""), r.get("category",""), norm_text(r.get("statement","")))
        if key not in seen:
            seen.add(key); out.append(r)
    return out

def main():
    Path(config.OUT_DIR).mkdir(parents=True, exist_ok=True)
    recs = json.load(open(config.NORMALIZE, encoding="utf-8"))

    # カテゴリ仕分け
    fr  = [r for r in recs if r.get("category")=="functional"]
    nfr = [r for r in recs if r.get("category")=="nonfunctional"]
    dec = [r for r in recs if r.get("category")=="decision"]

    # 1) 決定：暫定語を含む“決定っぽい”文は決定から除外
    dec = [d for d in dec if not is_tentative(d["statement"])]

    # 2) 決定をトピックごとに最新へ集約
    latest_decision_by_topic = {}
    for r in dec:
        t  = topic_of(r["statement"])
        ts = (r.get("source") or {}).get("timestamp","")
        if (t not in latest_decision_by_topic) or (to_dt(ts) > to_dt((latest_decision_by_topic[t].get("source") or {}).get("timestamp",""))):
            latest_decision_by_topic[t] = r
    dec = list(latest_decision_by_topic.values())

    # 3) 省略的な決定の具体化
    proposals_and_info = [*fr, *nfr]  # 参照用
    dec = [complete_decision_text(d, proposals_and_info) for d in dec]

    # 4) 決定に従って FR/NFR を整理（除外/競合解消）
    out_of_scope = []
    kept_fr = []
    kept_nfr = []

    for r in fr:
        topic = topic_of(r["statement"])
        d  = latest_decision_by_topic.get(topic)
        if not d:
            kept_fr.append(r); continue
        ds = d["statement"]
        s  = r["statement"]

        # 除外決定 → 対応FRはOut-of-Scope
        if ("除外" in ds) or ("範囲から除外" in ds):
            out_of_scope.append(r); continue

        # ボタン位置の競合（中央下 vs 右下）
        if topic == "ボタン位置":
            if ("中央下" in ds and "右下" in s) or ("右下" in ds and "中央下" in s):
                continue

        kept_fr.append(r)

    for r in nfr:
        topic = topic_of(r["statement"])
        d  = latest_decision_by_topic.get(topic)
        if d:
            ds = d["statement"]
            if ("除外" in ds) or ("範囲から除外" in ds):
                out_of_scope.append(r); continue
        kept_nfr.append(r)

    fr, nfr = kept_fr, kept_nfr

    # 5) 重複の統合（FR/NFR/DECをそれぞれ）
    fr  = dedup(fr)
    nfr = dedup(nfr)
    dec = dedup(dec)

    # 6) 決定は“新しい順”に
    def to_dt_from_rec(r):
        return to_dt((r.get("source") or {}).get("timestamp",""))
    dec = sorted(dec, key=to_dt_from_rec, reverse=True)

    # 7) テンプレ適用
    env = Environment(
        loader=FileSystemLoader(str(config.TPL_DIR)),
        autoescape=select_autoescape(),
        trim_blocks=True, lstrip_blocks=True
    )
    tpl = env.get_template("req.md.j2")
    out = tpl.render(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        source=str(config.NORMALIZE),
        fr=fr, nfr=nfr, dec=dec,
        out_of_scope=out_of_scope
    )

    safe_write_text(Path(config.NORMALIZE_OUTPUT), out)
    print(f"[ok] 正規化仕様書を出力: {config.NORMALIZE_OUTPUT}")
    print(f"[debug] FR:{len(fr)} NFR:{len(nfr)} DEC:{len(dec)} OOS:{len(out_of_scope)}")

if __name__ == "__main__":
    main()
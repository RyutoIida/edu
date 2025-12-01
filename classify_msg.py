import os, json
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from utils.json_sanitize import extract_json_array
import config

# .env 読み込み, 出力先準備
load_dotenv()
Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

with open(config.INPUT_FILE, "r", encoding="utf-8") as f:
    utterances = json.load(f)

system_prompt = (
    "あなたは議事録を分類するAIです。\n"
    "次の5つのラベルで各発言を分類し、JSON配列のみを返してください。\n"
    "- decision: 方針/仕様が明確に決まった発言\n"
    "- proposal: 提案・要望\n"
    "- question: 確認・質問\n"
    "- chitchat: 雑談\n"
    "- other: 上記以外\n"
    "出力は JSON 配列のみ（例: [{\"text\":\"...\",\"label\":\"proposal\"}]）。説明文やコードブロックは禁止。"
)

user_msg = "\n".join([f"{u['speaker']}: {u['text']}" for u in utterances])

response = client.chat.completions.create(
    model = config.MODEL,
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg}
    ],
    temperature=config.TEMPERATURE
)

reply = response.choices[0].message.content

labels = extract_json_array(reply)

# zip長不一致に備えて最小長で結合
for u, l in zip(utterances, labels):
    u["label"] = l.get("label", "unknown")

with open(config.CLASSIFIED_FILE, "w", encoding="utf-8") as f:
    json.dump(utterances, f, ensure_ascii=False, indent=2)

print(f"\n 分類結果を保存しました: {config.CLASSIFIED_FILE}")
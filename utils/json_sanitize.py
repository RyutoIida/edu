import json, re

def extract_json_array(text: str):
    m = re.search(r'\[\s*\{.*?\}\s*\]', text, re.DOTALL)
    if not m:
        raise ValueError("JSON配列を抽出できませんでした。")
    return json.loads(m.group(0))
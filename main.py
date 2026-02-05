# main.py
from __future__ import annotations

import argparse
import json
import os
import shutil
import hashlib
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from classify_msg import classify_messages
from normalize import normalize_records, ensure_profile_exists


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: str, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt_sec(sec: float) -> str:
    if sec < 60:
        return f"{sec:.3f}s"
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m}m {s:.3f}s"


def copy_to_lock(lock_dir: str, profile_path: str, template_path: str, settings: dict) -> dict:
    """
    profile/template/設定を lock_dir に凍結する。
    """
    lock = Path(lock_dir)
    lock.mkdir(parents=True, exist_ok=True)

    dst_profile = lock / "profile.json"
    dst_template = lock / Path(template_path).name

    shutil.copy2(profile_path, dst_profile)
    shutil.copy2(template_path, dst_template)

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "profile_file": str(dst_profile.name),
        "template_file": str(dst_template.name),
        "profile_sha256": sha256_file(str(dst_profile)),
        "template_sha256": sha256_file(str(dst_template)),
        "settings": settings,
    }
    write_json(str(lock / "lock_manifest.json"), manifest)
    return manifest


def resolve_from_lock(lock_dir: str) -> tuple[str, str, dict]:
    lock = Path(lock_dir)
    mpath = lock / "lock_manifest.json"
    if not mpath.exists():
        raise SystemExit(f"lock_manifest.json が見つかりません: {mpath}")

    manifest = read_json(str(mpath))
    profile_path = lock / manifest["profile_file"]
    template_path = lock / manifest["template_file"]
    if not profile_path.exists():
        raise SystemExit(f"lock内 profile が見つかりません: {profile_path}")
    if not template_path.exists():
        raise SystemExit(f"lock内 template が見つかりません: {template_path}")
    return str(profile_path), str(template_path), manifest


def main() -> None:
    load_dotenv()

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="例: ./data/input_data1.json")
    ap.add_argument("--output-md", required=True, help="例: ./output/ds1/spec.md")
    ap.add_argument("--profile", default="./profile.json", help="ワード補正/除外語/トピック辞書など（凍結対象）")

    ap.add_argument("--mode", choices=["tune", "eval"], required=True)
    ap.add_argument("--lock-out", default="", help="tune時に作る lock ディレクトリ（例: ./lock/lock_ds1）")
    ap.add_argument("--lock-in", default="", help="eval時に使う lock ディレクトリ（例: ./lock/lock_ds1）")
    ap.add_argument("--template", default="", help="tune時に使用: 例 ./templates/spec.md.j2（eval時はlockのtemplateを使う）")

    ap.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4o"))
    ap.add_argument("--temperature", type=float, default=float(os.getenv("OPENAI_TEMPERATURE", "0.2")))

    args = ap.parse_args()

    input_path = str(Path(args.input).resolve())
    out_md_path = str(Path(args.output_md).resolve())
    out_dir = str(Path(out_md_path).parent)
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    if not Path(input_path).exists():
        raise SystemExit(f"input が見つかりません: {input_path}")

    # --- lock 解決 ---
    if args.mode == "tune":
        if not args.lock_out:
            raise SystemExit("tuneモードでは --lock-out が必要です（例: --lock-out ./lock/lock_ds1）")
        if not args.template:
            raise SystemExit("tuneモードでは --template が必要です（例: --template ./templates/spec.md.j2）")

        ensure_profile_exists(args.profile)  # profile.json が無ければ生成

        settings = {"model": args.model, "temperature": args.temperature}
        manifest = copy_to_lock(args.lock_out, args.profile, args.template, settings)

        lock_dir = str(Path(args.lock_out).resolve())
        profile_path = str(Path(lock_dir) / manifest["profile_file"])
        template_path = str(Path(lock_dir) / manifest["template_file"])
        model = settings["model"]
        temperature = settings["temperature"]

    else:  # eval
        if not args.lock_in:
            raise SystemExit("evalモードでは --lock-in が必要です（例: --lock-in ./lock/lock_ds1）")
        profile_path, template_path, manifest = resolve_from_lock(args.lock_in)
        lock_dir = str(Path(args.lock_in).resolve())
        model = manifest.get("settings", {}).get("model", args.model)
        temperature = manifest.get("settings", {}).get("temperature", args.temperature)

    # --- 中間出力パス（output-md と同じフォルダに置く） ---
    classified_path = str(Path(out_dir) / "classified.json")
    normalized_path = str(Path(out_dir) / "normalized.json")
    run_meta_path = str(Path(out_dir) / "run_meta.json")

    # =========================
    # 時間計測（ここから）
    # =========================
    t0 = time.perf_counter()

    # --- 1) 分類（入力読み込み〜 classified.json 出力まで含む） ---
    classify_messages(
        input_path=input_path,
        output_path=classified_path,
        profile_path=profile_path,
        model=model,
        temperature=temperature,
    )
    t1 = time.perf_counter()

    # --- 2) 正規化（classified.json 読み込み〜 normalized.json 出力まで） ---
    normalized = normalize_records(
        classified_path=classified_path,
        output_path=normalized_path,
        profile_path=profile_path,
        model=model,
        temperature=temperature,
    )
    t2 = time.perf_counter()

    # --- 3) テンプレ適用（normalized.json → spec.md） ---
    from gen_req import generate_markdown

    generate_markdown(
        normalized_data=normalized,
        template_path=template_path,
        output_md_path=out_md_path,
        source_path=normalized_path,
    )
    t3 = time.perf_counter()

    # --- 4) メタ出力（run_meta.json） ---
    meta = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mode": args.mode,
        "input": input_path,
        "input_sha256": sha256_file(input_path),
        "output_md": out_md_path,
        "classified": classified_path,
        "normalized": normalized_path,
        "lock_dir": lock_dir,
        "lock_manifest": manifest,
        "model": model,
        "temperature": temperature,
        "profile_sha256": sha256_file(profile_path),
        "template_sha256": sha256_file(template_path),
        "timing": {
            # 累積（データ読み込み開始〜各成果物が出るまで）
            "to_classified_sec": t1 - t0,
            "to_normalized_sec": t2 - t0,
            "to_spec_md_sec": t3 - t0,
            # 区間（各処理そのものの時間）
            "classify_sec": t1 - t0,
            "normalize_sec": t2 - t1,
            "render_sec": t3 - t2,
            # 総時間（仕様書出力まで）
            "total_to_spec_sec": t3 - t0,
        },
    }
    write_json(run_meta_path, meta)
    t4 = time.perf_counter()

    # =========================
    # 表示（評価用)
    # =========================
    print("[TIME]")
    print(f" - to classified.json: {fmt_sec(t1 - t0)}")
    print(f" - to normalized.json: {fmt_sec(t2 - t0)}")
    print(f" - to spec.md:         {fmt_sec(t3 - t0)}")
    print(" - segments:")
    print(f"    * classify:  {fmt_sec(t1 - t0)}")
    print(f"    * normalize: {fmt_sec(t2 - t1)}")
    print(f"    * render:    {fmt_sec(t3 - t2)}")
    print(f" - run_meta write:     {fmt_sec(t4 - t3)}")
    print(f" - total (to spec.md): {fmt_sec(t3 - t0)}")

    print("[OK]")
    print(f" - output_md:   {out_md_path}")
    print(f" - classified:  {classified_path}")
    print(f" - normalized:  {normalized_path}")
    print(f" - run_meta:    {run_meta_path}")


if __name__ == "__main__":
    main()

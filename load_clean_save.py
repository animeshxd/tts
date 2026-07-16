import os
from pathlib import Path
from typing import Dict

import kagglehub
import pandas as pd
from wordfreq import zipf_frequency

application_dir = os.path.dirname(os.path.abspath(__file__))
slang_clean_path = Path(
    application_dir,
    "slang_clean.json",
)


def load_raw_json():
    path = kagglehub.dataset_download("gowrishankarp/chat-slang-abbreviations-acronyms")
    print("Path to dataset files:", path)
    return pd.read_json(Path(path, "slang", "slang.json"), orient="index", typ="series")


ZIPF_THRESHOLD = 3.0

KEEP = {
    "lol",
    "lmao",
    "afk",
    "brb",
    "bro",
    "vro",
    "bruh",
    "ngl",
    "irl",
    "gg",
    "ez",
    "sus",
}

REMOVE = {
    "bmw",
    "amd",
    "intel",
    "nvidia",
    "cpu",
    "gpu",
    "hiv",
    "covid",
    "aids",
    "hr",
    "kg",
    "km",
    "cm",
    "mm",
}

OVERRIDE = {
    "ext": "external",
    "exts": "externals",
    "fw": "feed water",
    "fr": "for real",
    "fws": "feed waters",
    "chp": "checkpoint",
    "lo": "logistics",
    "py": "python",
    "tran": "transformer",
    "trans": "transformer",
    "wym": "what do you mean?",
    "btw": "by the way",
    "vro": "bro",
    "ima": "iama",
    "tmp": "temporary",
    "onw": "on way",
    "pm": "p m",
    "trt": "t r t",
    "ps": "p s",
    "wt": "wait",
    "ht": "have to",
    "rm": "remove",
    "cp": "copy",
    "mv": "move",
}


def clean_and_save(data):
    removed = {}
    kept = {}
    for word, value in data.items():
        w = word.strip().lower()
        freq = zipf_frequency(w, "en")

        if w in KEEP:
            kept[word] = value
            continue

        if w in REMOVE:
            removed[word] = value
            continue

        if freq >= ZIPF_THRESHOLD:
            removed[word] = value
        else:
            kept[word] = value
    for key, value in OVERRIDE.items():
        kept[key] = value

    kept = pd.Series(kept)
    removed = pd.Series(removed)

    print(f"Kept: {len(kept)}")
    print(f"Removed: {len(removed)}")
    kept.to_json(
        slang_clean_path,
        indent=2,
        force_ascii=False,
    )
    return kept


def load_clean_json(path=slang_clean_path):
    return pd.read_json(path, orient="index", typ="series")


def main():
    df = load_raw_json()
    clean_and_save(df)


if __name__ == "__main__":
    main()

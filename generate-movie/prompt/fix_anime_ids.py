import json
from pathlib import Path

FILE = Path(__file__).with_name("anime.json")

def main():
    data = json.loads(FILE.read_text(encoding="utf-8"))
    for idx, item in enumerate(data, start=1):
        item["id"] = idx
    FILE.write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()

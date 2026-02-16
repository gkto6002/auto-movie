import os
import time
import requests
from dotenv import load_dotenv

# .env読み込み
load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY")

def generate_company_intro_video():
    if not API_KEY:
        print("エラー: APIキーがありません。")
        return

    # ---------------------------------------------------------
    # プロンプト設定
    # ---------------------------------------------------------
    # ここに生成したい動画の内容を記述します
    prompt_text = (
        "Vibrant Japanese Isekai fantasy anime style with cel-shading animation. Set in a mystical ancient ruin altar. A massive, out-of-place heavy industrial steel injection molding die rests on a glowing pedestal. Steam erupts, gears whir loudly, and the mold mechanically opens to eject a blindingly glowing shining legendary sword. A young hero character in ornate shining armor catches the sword. Facing a towering, shadowy horned Demon Lord creature across the battlefield, the hero shouts intensely with visible mouth movements consistent with Japanese speech (like 'IKUZO!' or 'HISSATSU!'), swinging the sword to unleash a colossal wave of golden magical energy that obliterates the Demon Lord in a massive light explosion."
    )

    # ---------------------------------------------------------
    # APIリクエスト設定 (テキスト to 動画)
    # ---------------------------------------------------------
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    create_url = "https://api.openai.com/v1/videos"

    # JSONペイロードの作成
    # 画像ファイルを使わないため、シンプルな辞書型データにします
    payload = {
        "model": "sora-2",      # 使用するモデル
        "prompt": prompt_text,  # プロンプト
        "seconds": "12",          # 動画の長さ (数値または文字列、API仕様に合わせる)
        "size": "720x1280",     # 動画サイズ (縦長)
    }

    print("動画生成リクエストを送信中(プロンプトのみ)...")

    # requests.post で json=payload を指定すると自動的にJSON形式で送信されます
    try:
        response = requests.post(create_url, headers=headers, json=payload)
    except Exception as e:
        print(f"通信エラー: {e}")
        return

    if response.status_code != 200:
        print(f"APIエラー: {response.status_code}")
        print(response.text)
        return

    job_data = response.json()
    video_id = job_data.get("id")
    print(f"ジョブ作成成功! ID: {video_id}")

    # ---------------------------------------------------------
    # 完了待ち (ポーリング)
    # ---------------------------------------------------------
    status_url = f"https://api.openai.com/v1/videos/{video_id}"
    
    while True:
        print("生成中...", end=" ", flush=True)
        try:
            status_res = requests.get(status_url, headers=headers)
            status_data = status_res.json()
            status = status_data.get("status")
            
            if status == "completed":
                print("\n生成完了！ダウンロードします。")
                break
            elif status == "failed":
                print("\n生成失敗。理由:", status_data.get("error"))
                return
        except Exception as e:
            print(f"\nステータス確認エラー: {e}")
            pass
        
        time.sleep(5)

    # ---------------------------------------------------------
    # 保存
    # ---------------------------------------------------------
    content_url = f"https://api.openai.com/v1/videos/{video_id}/content"
    
    try:
        content_res = requests.get(content_url, headers=headers, stream=True)
        
        output_filename = "ks_intro_text_to_video.mp4"
        if content_res.status_code == 200:
            with open(output_filename, "wb") as f:
                for chunk in content_res.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"保存完了: {output_filename}")
        else:
            print("ダウンロード失敗")
            print(content_res.text)
            
    except Exception as e:
        print(f"保存処理エラー: {e}")

if __name__ == "__main__":
    generate_company_intro_video()
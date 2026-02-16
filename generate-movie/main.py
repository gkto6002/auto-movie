import os
import time
import requests
from dotenv import load_dotenv
from PIL import Image  # 画像処理ライブラリ

# .env読み込み
load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY")

def resize_image_for_sora(input_path, output_path, target_size=(720, 1280)):
    """
    画像をSoraが許容するサイズ(720x1280)にリサイズして保存する関数
    """
    try:
        with Image.open(input_path) as img:
            # 1. アルファチャンネル（透明部分）がある場合は白背景にする処理（念の為）
            if img.mode in ('RGBA', 'LA'):
                background = Image.new(img.mode[:-1], img.size, (255, 255, 255))
                background.paste(img, img.split()[-1])
                img = background
            
            # 2. リサイズ（アスペクト比を無視して指定サイズに引き伸ばします）
            # ※ロゴの比率を厳密に守りたい場合は「クロップ」処理が必要ですが、
            #   まずはエラー回避のために強制リサイズします。
            img_resized = img.resize(target_size, Image.LANCZOS)
            
            # 3. 保存
            img_resized.save(output_path, quality=95)
            print(f"画像をリサイズしました: {input_path} -> {output_path} ({target_size})")
            return True
    except Exception as e:
        print(f"画像リサイズエラー: {e}")
        return False

def generate_company_intro_video():
    if not API_KEY:
        print("エラー: APIキーがありません。")
        return

    # 元の画像ファイル名（ここを自分のファイル名に合わせてください）
    original_image = "logo.png" 
    # API送信用の一時ファイル名
    ready_image = "logo_for_sora.png"

    # 1. 画像が存在するか確認
    if not os.path.exists(original_image):
        print(f"エラー: '{original_image}' が見つかりません。")
        return

    # 2. 画像をSora用にリサイズ (720x1280)
    # エラーメッセージにあった '720x1280' をターゲットにします
    success = resize_image_for_sora(original_image, ready_image, target_size=(720, 1280))
    if not success:
        return

    # ---------------------------------------------------------
    # APIリクエスト設定
    # ---------------------------------------------------------
    prompt_text = (
        "ニュース速報のスタイル。「Breaking News」のグラフィック。工場を背景にしたニュースキャスターがデスクに座っている。見出し：「速報：クリエス精機、ミクロン単位の壁を突破」。ハイテクな測定機器と完璧な製品の映像へ切り替わる。"
    )

    headers = {"Authorization": f"Bearer {API_KEY}"}
    create_url = "https://api.openai.com/v1/videos"

    print("動画生成リクエストを送信中...")

    # リサイズした画像を開いて送信
    with open(ready_image, "rb") as image_file:
        files = {
            "prompt": (None, prompt_text),
            "input_reference": (ready_image, image_file, "image/png"),
            "model": (None, "sora-2"),
            "seconds": (None, "12"), 
            # ここ重要！リサイズした画像のサイズと一致させる
            "size": (None, "720x1280"), 
        }

        response = requests.post(create_url, headers=headers, files=files)

    if response.status_code != 200:
        print(f"APIエラー: {response.status_code}")
        print(response.text)
        return

    job_data = response.json()
    video_id = job_data.get("id")
    print(f"ジョブ作成成功! ID: {video_id}")

    # ---------------------------------------------------------
    # 完了待ち
    # ---------------------------------------------------------
    status_url = f"https://api.openai.com/v1/videos/{video_id}"
    
    while True:
        print("生成中...", end=" ")
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
        except Exception:
            pass
        
        time.sleep(5)

    # ---------------------------------------------------------
    # 保存
    # ---------------------------------------------------------
    content_url = f"https://api.openai.com/v1/videos/{video_id}/content"
    content_res = requests.get(content_url, headers=headers, stream=True)
    
    output_filename = "ks_intro_1024x1792.mp4"
    if content_res.status_code == 200:
        with open(output_filename, "wb") as f:
            for chunk in content_res.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"保存完了: {output_filename}")
    else:
        print("ダウンロード失敗")

if __name__ == "__main__":
    generate_company_intro_video()
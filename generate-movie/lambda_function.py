import os
import time
import json
import requests
import boto3
import io
import re
from PIL import Image
from datetime import datetime, timedelta, date

# ---------------------------------------------------------
# 環境設定
# ---------------------------------------------------------
API_KEY = os.environ.get("OPENAI_API_KEY")
S3_BUCKET = os.environ.get("S3_BUCKET", "kuriesu-auto-movie")

# S3フォルダ構成
PREFIX_CONFIG = "config/video_plan.json" # ★設定ファイルのパス
PREFIX_RESIZED = "resized-images/"
PREFIX_VIDEO = "incoming/"

# ★ ループの基準日設定 (今日: 2026/02/17 を開始日とする)
LOOP_START_DATE = date(2026, 2, 17)

# S3クライアント
s3 = boto3.client('s3')

def sanitize_filename(title):
    if not title:
        return ""
    cleaned = re.sub(r'[\\/:*?"<>|]+', '', title)
    cleaned = cleaned.replace(" ", "_").replace("　", "_")
    return cleaned[:200]

def resize_image_on_memory(image_bytes, target_size=(720, 1280)):
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            if img.mode in ('RGBA', 'LA'):
                background = Image.new(img.mode[:-1], img.size, (255, 255, 255))
                background.paste(img, img.split()[-1])
                img = background
            img_resized = img.resize(target_size, Image.LANCZOS)
            output_buffer = io.BytesIO()
            img_resized.save(output_buffer, format="PNG", quality=95)
            output_buffer.seek(0)
            return output_buffer
    except Exception as e:
        print(f"Resize Error: {e}")
        return None

def get_todays_plan():
    """
    S3上の config/video_plan.json を読み込み、日付に基づいてループしたプランを返す
    """
    try:
        print(f"Fetching config from s3://{S3_BUCKET}/{PREFIX_CONFIG}")
        
        # S3から設定ファイルを読み込む
        response = s3.get_object(Bucket=S3_BUCKET, Key=PREFIX_CONFIG)
        file_content = response['Body'].read().decode('utf-8')
        plans = json.loads(file_content)
        
        if not plans:
            print("Config is empty.")
            return None

        # 日本時間(JST)で現在の日付を取得
        jst_now = datetime.utcnow() + timedelta(hours=9)
        today = jst_now.date()

        # 経過日数を計算
        days_passed = (today - LOOP_START_DATE).days

        # リストの長さで割った余りを使うことでループさせる
        target_index = days_passed % len(plans)
        
        selected_plan = plans[target_index]
        print(f"--- Daily Loop Info ---")
        print(f"Today (JST): {today}")
        print(f"Days Passed: {days_passed}")
        print(f"Selected ID: {selected_plan.get('id')} (Index: {target_index})")
        print(f"Title: {selected_plan.get('title')}")
        print(f"-----------------------")
        
        return selected_plan

    except Exception as e:
        print(f"Config Load Error: {e}")
        # 設定ファイル読み込みエラー時はNoneを返してデフォルト動作に任せるか、エラーにする
        return None

def lambda_handler(event, context):
    print("--- Start Video Generation ---")

    if not API_KEY:
        return {"statusCode": 500, "body": "Error: OPENAI_API_KEY is missing"}

    try:
        # -------------------------------------------------
        # 0. パラメータの決定
        # -------------------------------------------------
        
        # S3から今日のプランを取得
        todays_plan = get_todays_plan()
        
        # 変数の初期化
        target_s3_key = None
        prompt_text = ""
        video_title = ""

        # プランが取得できていればセット
        if todays_plan:
            target_s3_key = todays_plan.get('s3_image_key')
            prompt_text = todays_plan.get('prompt')
            video_title = todays_plan.get('title')

        # event引数（手動実行時など）があればそちらを優先して上書き
        if 's3_image_key' in event:
            target_s3_key = event['s3_image_key']
        if 'prompt' in event:
            prompt_text = event['prompt']
        if 'title' in event:
            video_title = event['title']

        print(f"Target Prompt: {prompt_text[:50]}...")
        print(f"Target Title: {video_title}")

        # -------------------------------------------------
        # 1. 画像データの準備 (S3キーがある場合のみ)
        # -------------------------------------------------
        final_image_buffer = None
        
        if target_s3_key:
            print(f"Mode: Image-to-Video (S3). Key={target_s3_key}")
            try:
                response = s3.get_object(Bucket=S3_BUCKET, Key=target_s3_key)
                s3_image_bytes = response['Body'].read()
                source_filename = os.path.basename(target_s3_key)
                
                print("Resizing S3 image in memory...")
                final_image_buffer = resize_image_on_memory(s3_image_bytes, target_size=(720, 1280))
                
                if final_image_buffer is None:
                    return {"statusCode": 500, "body": "Error: Failed to resize S3 image"}
                
                # 画像加工の証跡保存
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                processed_filename = f"used_{timestamp}_{source_filename}"
                processed_key = f"{PREFIX_RESIZED}{processed_filename}"
                
                print(f"Uploading evidence image to s3://{S3_BUCKET}/{processed_key}")
                s3.put_object(
                    Bucket=S3_BUCKET,
                    Key=processed_key,
                    Body=final_image_buffer.getvalue(),
                    ContentType='image/png'
                )
                
                final_image_buffer.seek(0)

            except Exception as e:
                print(f"Error fetching/resizing from S3: {e}")
                return {"statusCode": 500, "body": f"S3 Error: {str(e)}"}

        else:
            print("Mode: Text-to-Video (No image provided).")
            final_image_buffer = None

        # -------------------------------------------------
        # 2. プロンプトの最終確認
        # -------------------------------------------------
        if not prompt_text:
            if final_image_buffer:
                prompt_text = "Bring the provided logo image to life. High-tech factory. 8k resolution."
            else:
                prompt_text = "Cinematic view of a high-tech factory. 8k resolution."

        # -------------------------------------------------
        # 3. Sora API リクエスト
        # -------------------------------------------------
        headers = {
            "Authorization": f"Bearer {API_KEY}"
        }
        create_url = "https://api.openai.com/v1/videos"
        
        data_payload = {
            "model": "sora-2", 
            "prompt": prompt_text,
            "seconds": "12",
            "size": "720x1280"
        }
        
        print(f"Sending request to Sora API... (Image provided: {final_image_buffer is not None})")

        if final_image_buffer:
            files_payload = {
                "input_reference": ("input.png", final_image_buffer, "image/png")
            }
            api_res = requests.post(create_url, headers=headers, data=data_payload, files=files_payload)
        else:
            api_res = requests.post(create_url, headers=headers, json=data_payload)
        
        if api_res.status_code != 200:
            print(f"Sora API Error: {api_res.text}")
            return {"statusCode": 500, "body": f"API Error: {api_res.text}"}

        res_json = api_res.json()
        video_id = res_json.get("id")
        print(f"Job started. ID: {video_id}")

        # -------------------------------------------------
        # 4. ポーリング (完了待ち)
        # -------------------------------------------------
        status_url = f"https://api.openai.com/v1/videos/{video_id}"
        
        for _ in range(60): 
            time.sleep(5)
            status_res = requests.get(status_url, headers=headers)
            if status_res.status_code != 200:
                print(f"Status Check Error: {status_res.text}")
                continue

            status_data = status_res.json()
            status = status_data.get("status")
            progress = status_data.get("progress", 0)
            
            print(f"Status: {status} ({progress}%)")
            
            if status == "completed":
                break
            elif status == "failed":
                return {"statusCode": 500, "body": f"Generation failed: {status_data.get('error')}"}
        else:
             return {"statusCode": 504, "body": "Timeout waiting for video generation"}

        # -------------------------------------------------
        # 5. 動画のダウンロード & S3へ保存
        # -------------------------------------------------
        print("Streaming video to S3...")
        video_content_url = f"https://api.openai.com/v1/videos/{video_id}/content"

        video_res = requests.get(video_content_url, headers=headers, stream=True)
        
        if video_res.status_code == 200:
            
            # ★ファイル名決定ロジック
            if video_title:
                safe_title = sanitize_filename(video_title)
                if not safe_title:
                     safe_title = "untitled"
                # タイトル.mp4
                filename = f"{safe_title}.mp4"
            else:
                # タイトルがない場合
                filename = f"video_{video_id}.mp4"

            incoming_key = f"{PREFIX_VIDEO}{filename}"
            print(f"Saving to S3 as: {incoming_key}")
            
            s3.upload_fileobj(
                video_res.raw,
                S3_BUCKET,
                incoming_key,
                ExtraArgs={'ContentType': 'video/mp4'}
            )
            
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "message": "Video generated successfully",
                    "s3_video": incoming_key,
                    "title_used": video_title
                })
            }
        else:
            return {"statusCode": 500, "body": "Failed to download video content"}

    except Exception as e:
        print(f"System Error: {e}")
        return {"statusCode": 500, "body": str(e)}
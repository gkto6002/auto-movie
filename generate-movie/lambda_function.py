import os
import time
import json
import requests
import boto3
import io
from PIL import Image
from datetime import datetime

# ---------------------------------------------------------
# 環境設定
# ---------------------------------------------------------
API_KEY = os.environ.get("OPENAI_API_KEY")
S3_BUCKET = os.environ.get("S3_BUCKET", "kuriesu-auto-movie")

# S3フォルダ構成
PREFIX_RESIZED = "resized-images/"  # API送信に使用した画像を保存する場所(S3経由の場合のみ)
PREFIX_VIDEO = "incoming/"  # 完成動画を置く場所

# デフォルト画像（Lambdaパッケージ内に含める・720x1280済みとする）
LOCAL_DEFAULT_IMAGE = "logo.png"

# S3クライアント
s3 = boto3.client('s3')

def resize_image_on_memory(image_bytes, target_size=(720, 1280)):
    """
    バイナリデータを受け取り、リサイズしてバイナリデータ(BytesIO)として返す
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            # 透過PNG対策
            if img.mode in ('RGBA', 'LA'):
                background = Image.new(img.mode[:-1], img.size, (255, 255, 255))
                background.paste(img, img.split()[-1])
                img = background
            
            # リサイズ (LANCZOS)
            img_resized = img.resize(target_size, Image.LANCZOS)
            
            # メモリバッファに出力
            output_buffer = io.BytesIO()
            img_resized.save(output_buffer, format="PNG", quality=95)
            output_buffer.seek(0)
            
            return output_buffer
    except Exception as e:
        print(f"Resize Error: {e}")
        return None

def lambda_handler(event, context):
    print("--- Start Video Generation ---")

    if not API_KEY:
        return {"statusCode": 500, "body": "Error: OPENAI_API_KEY is missing"}

    try:
        # 最終的にAPIに送る画像データ（バッファ）
        final_image_buffer = None
        source_filename = ""
        
        # 実行モード判定（S3の画像を使うかどうか）
        target_s3_key = event.get('s3_image_key')

        # -------------------------------------------------
        # 1. 画像データの準備
        # -------------------------------------------------
        if target_s3_key:
            # --- パターンA: S3から取得（写真はリサイズが必要）---
            print(f"Mode: S3 Fetch. Key={target_s3_key}")
            try:
                response = s3.get_object(Bucket=S3_BUCKET, Key=target_s3_key)
                s3_image_bytes = response['Body'].read()
                source_filename = os.path.basename(target_s3_key)
                
                # リサイズ実行
                print("Resizing S3 image in memory...")
                final_image_buffer = resize_image_on_memory(s3_image_bytes, target_size=(720, 1280))
                
                if final_image_buffer is None:
                    return {"statusCode": 500, "body": "Error: Failed to resize S3 image"}
                
                # ★修正ポイント: S3から取ってきた場合のみ、加工後画像をS3に保存(証跡)★
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

            except Exception as e:
                print(f"Error fetching/resizing from S3: {e}")
                return {"statusCode": 500, "body": f"S3 Error: {str(e)}"}

        else:
            # --- パターンB: ローカルのデフォルト画像（リサイズ不要・保存不要）---
            print(f"Mode: Local Default. File={LOCAL_DEFAULT_IMAGE}")
            local_path = os.path.join(os.getcwd(), LOCAL_DEFAULT_IMAGE)
            
            if not os.path.exists(local_path):
                return {"statusCode": 500, "body": "Error: Default logo.png not found"}
            
            # そのまま読み込んでバッファにする
            with open(local_path, "rb") as f:
                image_bytes = f.read()
                final_image_buffer = io.BytesIO(image_bytes)
            
            print("Skipping resize and upload for default image.")
            # ローカルの場合はS3への証跡保存（put_object）をスキップします

        # -------------------------------------------------
        # 2. Sora API リクエスト
        # -------------------------------------------------
        final_image_buffer.seek(0) # ポインタを戻す
        
        prompt_text = (
            "Bring the provided logo image to life. "
            "The static logo glows blue and transforms into a high-tech factory. "
            "Close-up of a mold being carved with sparks flying. "
            "Futuristic, clean, cinematic lighting, 8k resolution."
        )
        
        headers = {"Authorization": f"Bearer {API_KEY}"}
        create_url = "https://api.openai.com/v1/videos"
        
        print("Sending request to Sora API...")
        
        files_payload = {
            "input_reference": ("input.png", final_image_buffer, "image/png")
        }
        
        data_payload = {
            "model": "sora-2",
            "prompt": prompt_text,
            "seconds": "12",
            "size": "720x1280"
        }

        api_res = requests.post(create_url, headers=headers, data=data_payload, files=files_payload)
        
        if api_res.status_code != 200:
            print(f"Sora API Error: {api_res.text}")
            return {"statusCode": 500, "body": f"API Error: {api_res.text}"}

        video_id = api_res.json().get("id")
        print(f"Job started. ID: {video_id}")

        # -------------------------------------------------
        # 3. ポーリング (完了待ち)
        # -------------------------------------------------
        status_url = f"https://api.openai.com/v1/videos/{video_id}"
        video_content_url = f"https://api.openai.com/v1/videos/{video_id}/content"
        
        for _ in range(60): 
            time.sleep(5)
            status_res = requests.get(status_url, headers=headers)
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
        # 4. 動画のダウンロード & S3 (generated-videos/) へ保存
        # -------------------------------------------------
        print("Streaming video to S3...")
        video_res = requests.get(video_content_url, headers=headers, stream=True)
        
        if video_res.status_code == 200:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            incoming_key = f"{PREFIX_VIDEO}video_{timestamp}_{video_id}.mp4"
            
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
                    "used_image_source": "S3" if target_s3_key else "Local Default",
                    "s3_video": incoming_key
                })
            }
        else:
            return {"statusCode": 500, "body": "Failed to download video content"}

    except Exception as e:
        print(f"System Error: {e}")
        return {"statusCode": 500, "body": str(e)}
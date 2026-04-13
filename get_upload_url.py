import json
import boto3
import os
import uuid

# Ortam değişkenleri ile Lambda yetkilerini kullanacak s3 client'ı oluşturuyoruz
s3_client = boto3.client("s3")
BUCKET_NAME = os.environ.get("UPLOAD_BUCKET")


def lambda_handler(event, context):
    try:
        # Arayüzden (query parameters) beklentiler:
        # Orn: /get-upload-url?filename=mycv.pdf&jobId=python-backend-dev
        query_params = event.get("queryStringParameters") or {}

        filename = query_params.get("filename")
        job_id = query_params.get("jobId")

        if not filename or not job_id:
            return {
                "statusCode": 400,
                "headers": {
                    "Access-Control-Allow-Origin": "*",
                    "Content-Type": "application/json",
                },
                "body": json.dumps({"error": "Eksik parametre (filename veya jobId)"}),
            }

        # Dosya ismini unique (benzersiz) yapıyoruz ve ilan id'sini dosya comprises'ne gömüyoruz
        unique_filename = f"{job_id}_{uuid.uuid4().hex[:8]}_{filename}"

        # Presigned URL Üretimi (GET için değil, PUT için)
        # İmza (Signature) hatası almamak için ContentType ve Metadata özelliklerini URL parametrelerinden siliyoruz
        presigned_url = s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": BUCKET_NAME,
                "Key": unique_filename
            },
            ExpiresIn=300,  # 5 dakika (300 saniye) geçerlilik süresi
        )

        return {
            "statusCode": 200,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Content-Type": "application/json",
            },
            "body": json.dumps(
                {"uploadUrl": presigned_url, "filename": unique_filename}
            ),
        }

    except Exception as e:
        print(f"URL uretim hatasi: {e}")
        return {
            "statusCode": 500,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Content-Type": "application/json",
            },
            "body": json.dumps({"error": "URL olusturulamadi."}),
        }

import json
import boto3
import os
import uuid


s3_client = boto3.client("s3")
BUCKET_NAME = os.environ.get("UPLOAD_BUCKET")


def lambda_handler(event, context):
    try:
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
                "body": json.dumps({"error": "Missing Parameters (filename or jobId)"}),
            }

        unique_filename = f"{job_id}_{uuid.uuid4().hex[:8]}_{filename}"

        presigned_url = s3_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": BUCKET_NAME, "Key": unique_filename},
            ExpiresIn=300,
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
        print(f"URL generation error: {e}")
        return {
            "statusCode": 500,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Content-Type": "application/json",
            },
            "body": json.dumps({"error": "URL generation failed."}),
        }

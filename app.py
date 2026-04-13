import json
import os
import io
import urllib.parse
import boto3
import uuid
from datetime import datetime, timezone
from docx import Document
from pypdf import PdfReader
from dotenv import load_dotenv

load_dotenv()


s3_client = boto3.client("s3")
bedrock_client = boto3.client("bedrock-runtime")
sns_client = boto3.client("sns")
dynamodb = boto3.resource("dynamodb")


MIN_MATCH_SCORE = int(os.environ.get("MIN_MATCH_SCORE", "75"))
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "eu.amazon.nova-pro-v1:0")
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "hr-ops-candidates")
JOBS_BUCKET_NAME = os.environ.get("JOBS_BUCKET_NAME")


candidates_table = dynamodb.Table(DYNAMODB_TABLE_NAME) if DYNAMODB_TABLE_NAME else None


def get_job_description(job_id):
    """Retrieves the specific job description from S3 or local jobs.json."""
    try:
        if JOBS_BUCKET_NAME:
            file_obj = s3_client.get_object(Bucket=JOBS_BUCKET_NAME, Key="jobs.json")
            jobs = json.loads(file_obj["Body"].read().decode("utf-8"))
        else:

            with open("jobs.json", "r", encoding="utf-8") as f:
                jobs = json.load(f)

        for job in jobs:
            if job["id"] == job_id:
                return f"Position: {job['title']}\n{job['description']}"
    except Exception as e:
        print(f"Error reading jobs file: {e}")

    return "Target Job Description not found."


def extract_text_from_pdf(bucket, key):
    """Extracts text from a PDF file using pypdf."""
    file_obj = s3_client.get_object(Bucket=bucket, Key=key)
    file_content = file_obj["Body"].read()

    reader = PdfReader(io.BytesIO(file_content))
    text = ""
    for page in reader.pages:
        extracted = page.extract_text()
        if extracted:
            text += extracted + "\n"
    return text


def extract_text_from_docx(bucket, key):
    """Extracts text from a Word document (.docx) using python-docx."""
    file_obj = s3_client.get_object(Bucket=bucket, Key=key)
    file_content = file_obj["Body"].read()

    doc = Document(io.BytesIO(file_content))
    text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
    return text


def analyze_cv_with_bedrock(cv_text, job_description):
    """Analyzes the CV against the Job Description using Amazon Bedrock."""
    prompt = f"""You are a professional, objective, and fair technical recruiter and an automated ATS evaluator. Your task is to evaluate a Candidate CV against a Job Description.

CRITICAL INSTRUCTION: You must evaluate the candidate holistically. Base your scoring on clear evidence found in the CV text, but you can consider transferable skills, related experiences, and side projects favorably.

SCORING GUIDELINES & RULES:
1. Evaluate based on evidence. You can reasonably infer basic familiarity if strong related skills exist (e.g., knowing React implies some knowledge of JavaScript).
2. Wrong Field: If the candidate's core roles are largely unrelated to the job field, reflect this with a lower score (e.g., cap around 50), but be sure to acknowledge any relevant side projects, certifications, or hobbies.
3. Missing Core Tech: Deduct points proportionally if core technologies are missing, but do not harshly cap the entire score if they demonstrate strong fundamentals or fast learning potential in similar tools.
4. Seniority Mismatch: If there is a seniority gap, lower the experience score, but do not automatically fail them if their technical skills and projects are exceptionally strong.
5. Balance hard technical skills with soft skills, giving fair credit for leadership, communication, and problem-solving.

CHAIN OF THOUGHT PROCESS:
To prevent grade inflation and ensure logical deduction, you MUST structure your response in this exact order:
1. Extract must-have and nice-to-have requirements, and check if they are explicitly met.
2. List the gaps, risks, and missing requirements.
3. Evaluate strengths.
4. Calculate a strict, reasoned score for EACH dimension. You must provide a short text explaining the "Why" behind the score BEFORE giving the number.
5. Finally, calculate the overall_score strictly based on the weighted sum of your dimensional evaluations.

DIMENSION WEIGHTS:
- experience_match: 25%
- skills_match: 25%
- tools_technologies_match: 15%
- responsibilities_match: 15%
- seniority_match: 10%
- education_match: 5%
- domain_match: 5%

OUTPUT RULES:
Return ONLY a valid JSON object.
Do not include markdown, comments, or extra text.

JSON SCHEMA:
{{
  "must_have_requirements": [
    {{
      "requirement": "string",
      "status": "met | partially_met | not_met | not_evidenced",
      "evidence": "short evidence from CV or explanation why strictly missing"
    }}
  ],
  "nice_to_have_requirements": [
    {{
      "requirement": "string",
      "status": "met | partially_met | not_met | not_evidenced",
      "evidence": "short evidence from CV or explanation why missing"
    }}
  ],
  "gaps": ["specific gap 1", "specific gap 2"],
  "risk_flags": ["missing years of experience", "seniority mismatch", "required technology not evidenced"],
  "strengths": ["specific matching strength 1", "specific matching strength 2"],
  "dimension_evaluations": {{
    "experience_match": {{"reasoning": "Explain why this score", "score": 0}},
    "skills_match": {{"reasoning": "Explain why this score", "score": 0}},
    "tools_technologies_match": {{"reasoning": "Explain why this score", "score": 0}},
    "responsibilities_match": {{"reasoning": "Explain why this score", "score": 0}},
    "seniority_match": {{"reasoning": "Explain why this score", "score": 0}},
    "education_match": {{"reasoning": "Explain why this score", "score": 0}},
    "domain_match": {{"reasoning": "Explain why this score", "score": 0}}
  }},
  "summary": "Objective and constructive 3-sentence summary of candidate fit detailing their strengths and areas for improvement.",
  "hiring_recommendation": "Strong interview | Consider interview | Borderline | Reject",
  "score_band": "Strong Match | Moderate Match | Weak Match | Not Suitable",
  "overall_score": 0
}}

<Job_Description>
{job_description}
</Job_Description>

<Candidate_CV>
{cv_text}
</Candidate_CV>
"""

    body = json.dumps(
        {
            "system": [
                {
                    "text": "You are an expert HR manager and ATS system prioritizing strict JSON outputs without markdown."
                }
            ],
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": 1000, "temperature": 0.1},
        }
    )

    response = bedrock_client.invoke_model(
        body=body,
        modelId=BEDROCK_MODEL_ID,
        accept="application/json",
        contentType="application/json",
    )

    response_body = json.loads(response.get("body").read())
    result_text = (
        response_body.get("output", {})
        .get("message", {})
        .get("content", [{}])[0]
        .get("text", "")
    )

    try:
        start_idx = result_text.find("{")
        end_idx = result_text.rfind("}") + 1
        return json.loads(result_text[start_idx:end_idx])
    except Exception as e:
        print(f"JSON parsing error: {e}, Raw Output: {result_text}")
        return {
            "overall_score": 0,
            "summary": "Analysis failed.",
            "strengths": [],
            "gaps": [],
            "dimension_evaluations": {},
        }


def save_to_dynamodb(job_id, s3_key, analysis):
    """Saves the candidate evaluation results to DynamoDB."""
    if not candidates_table:
        print("DynamoDB table not configured, skipping DB save.")
        return

    candidate_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    item = {
        "JobId": job_id,
        "CandidateId": candidate_id,
        "CreatedAt": timestamp,
        "S3Key": s3_key,
        "Score": int(analysis.get("overall_score", 0)),
        "ScoreBand": str(analysis.get("score_band", "Unknown")),
        "Summary": str(analysis.get("summary", "")),
        "Strengths": analysis.get("strengths", []),
        "Gaps": analysis.get("gaps", []),
        "RiskFlags": analysis.get("risk_flags", []),
        "MustHaveRequirements": json.dumps(analysis.get("must_have_requirements", [])),
        "NiceToHaveRequirements": json.dumps(
            analysis.get("nice_to_have_requirements", [])
        ),
        "HiringRecommendation": str(analysis.get("hiring_recommendation", "Unknown")),
        "DimensionEvaluations": json.dumps(analysis.get("dimension_evaluations", {})),
    }

    try:
        candidates_table.put_item(Item=item)
        print(f"Successfully saved candidate {candidate_id} to DynamoDB.", flush=True)
    except Exception as e:
        print(f"Error saving to DynamoDB: {e}", flush=True)


def send_sns_notification(analysis, cv_filename):
    """Sends an SNS alert for outstanding candidates."""
    score = analysis.get("overall_score", 0)

    message = (
        f"New Top Candidate Alert: {cv_filename}\n"
        f"----------------------------------------\n"
        f"Match Score: {score}/100\n"
        f"Recommendation: {analysis.get('hiring_recommendation', 'N/A')}\n\n"
        f"Summary: {analysis.get('summary', '')}\n\n"
        f"Strengths:\n- " + "\n- ".join(analysis.get("strengths", [])) + "\n\n"
        f"Gaps:\n- " + "\n- ".join(analysis.get("gaps", []))
    )

    sns_client.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=f"Outstanding Candidate ({score}/100): {cv_filename}",
        Message=message,
    )


def lambda_handler(event, context):
    print("Event Received:", json.dumps(event))

    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"]).strip()

        try:
            print(f"Processing: s3://{bucket}/{key}")

            try:
                job_id = key.split("_")[0]
            except Exception:
                job_id = "python-backend-dev"

            print(f"Target Job ID: {job_id}")

            if key.lower().endswith(".pdf"):
                cv_text = extract_text_from_pdf(bucket, key)
            elif key.lower().endswith(".docx"):
                cv_text = extract_text_from_docx(bucket, key)
            else:
                print(f"Unsupported file format: {key}")
                continue

            print(f"CV Text Extracted Successfully (Length: {len(cv_text)})")

            current_job_description = get_job_description(job_id)
            print(f"Sending content to Bedrock for analysis...")

            analysis = analyze_cv_with_bedrock(cv_text, current_job_description)
            score = analysis.get("overall_score", 0)

            print(f"Analysis Complete. Score: {score}")

            save_to_dynamodb(job_id, key, analysis)

            if score >= MIN_MATCH_SCORE:
                print(
                    f"Score meets threshold ({score} >= {MIN_MATCH_SCORE})! Sending SNS notification..."
                )
                send_sns_notification(analysis, key)
            else:
                print(
                    f"Candidate score too low ({score} < {MIN_MATCH_SCORE}), SNS skipped."
                )

        except Exception as e:
            print(f"Error processing ({key}): {e}")
            raise e

    return {"statusCode": 200, "body": json.dumps("Processing complete!")}

import os
import requests
import jwt
import time
import json
import logging
import uuid
from cryptography.hazmat.primitives import serialization
import boto3
from boto3.dynamodb.conditions import Attr

# Initialize boto3 clients
secrets_client = boto3.client('secretsmanager')
dynamodb = boto3.resource('dynamodb')

# Load environment variables
API_KEY_SECRET = os.getenv('API_KEY_SECRET')
DYNAMODB_TABLE = os.getenv('DYNAMODB_TABLE')
PRIVATE_KEY_SECRET = os.getenv('PRIVATE_KEY_SECRET')
KEY_ID = os.getenv('KEY_ID')
ADDITIONAL_FIELD = os.getenv('ADDITIONAL_FIELD')
LOGGER_LEVEL = os.getenv('LOGGER_LEVEL', 'WARNING').upper()

# Configure logging
logger = logging.getLogger()
logger.setLevel(getattr(logging, LOGGER_LEVEL, logging.WARNING))

# Set base URL for 'int' environment
BASE_URL = 'https://int.api.service.nhs.uk/oauth2'
CONTENT_API_BASE_URL = 'https://int.api.service.nhs.uk/nhs-website-content'


def get_secret(secret_arn):
    response = secrets_client.get_secret_value(SecretId=secret_arn)
    return response['SecretString']


def generate_jwt_token(api_key, private_key, key_id):
    current_time = int(time.time())
    payload = {
        "iss": api_key,
        "sub": api_key,
        "aud": BASE_URL + '/token',
        "jti": str(uuid.uuid4()),
        "exp": current_time + 300,
    }
    headers = {
        "alg": "RS512",
        "typ": "JWT",
        "kid": key_id
    }

    logger.info(f"JWT Payload: {payload}")
    logger.info(f"JWT Headers: {headers}")

    token = jwt.encode(payload, private_key, algorithm='RS512', headers=headers)

    # Decode the token to inspect it
    decoded_token = jwt.decode(token, options={"verify_signature": False})
    logger.info(f"Decoded JWT: {json.dumps(decoded_token, indent=2)}")

    return token


def get_access_token(api_key, private_key, key_id):
    jwt_token = generate_jwt_token(api_key, private_key, key_id)
    logger.info(f"Generated JWT: {jwt_token}")
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    data = {
        'grant_type': 'client_credentials',
        'client_assertion_type': 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
        'client_assertion': jwt_token
    }

    response = requests.post(BASE_URL + '/token', headers=headers, data=data)
    if response.status_code == 200:
        return response.json().get('access_token')
    else:
        raise Exception(f"Failed to get access token: {response.status_code}, {response.text}")


def fetch_medicine_detail(api_key, access_token, medicine_url, retries=5, backoff_factor=1.5):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "apikey": api_key,
        "Content-Type": "application/json"
    }

    for attempt in range(retries):
        response = requests.get(medicine_url, headers=headers)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 429:
            wait_time = backoff_factor ** attempt
            logger.warning(f"Rate limit hit. Retrying in {wait_time} seconds...")
            time.sleep(wait_time)
        else:
            logger.error(f"Failed to retrieve medicine detail: {response.status_code}")
            return None
    raise Exception("Max retries exceeded")


def update_dynamodb(entry_id, field_name, field_value):
    table = dynamodb.Table(DYNAMODB_TABLE)
    table.update_item(
        Key={'EntryId': entry_id},
        UpdateExpression=f"SET #field = :value",
        ExpressionAttributeNames={
            "#field": field_name
        },
        ExpressionAttributeValues={
            ":value": field_value
        }
    )


def lambda_handler(event, context):
    # Fetch the secrets from Secrets Manager
    api_key = get_secret(API_KEY_SECRET)
    private_key = get_secret(PRIVATE_KEY_SECRET)

    # Decode the API key if it is in JSON format
    try:
        api_key = json.loads(api_key)['API_KEY']
    except json.JSONDecodeError:
        pass  # It's already a plain string

    private_key = serialization.load_pem_private_key(
        private_key.encode(),
        password=None,
    )

    # Log the KEY_ID
    logger.info(f"Using KEY_ID: {KEY_ID}")

    access_token = get_access_token(api_key, private_key, KEY_ID)
    table = dynamodb.Table(DYNAMODB_TABLE)

    # Process items in batches
    last_evaluated_key = event.get('LastEvaluatedKey')
    processed_count = 0

    scan_params = {
        'FilterExpression': Attr(ADDITIONAL_FIELD).not_exists(),
        'Limit': 25  # Adjust the limit as necessary
    }
    if last_evaluated_key:
        scan_params['ExclusiveStartKey'] = last_evaluated_key

    response = table.scan(**scan_params)
    items = response['Items']

    for item in items:
        entry_id = item['EntryId']
        medicine_url = item['URL']

        if ADDITIONAL_FIELD in item:
            logger.info(f"Skipping EntryId: {entry_id}, already has field: {ADDITIONAL_FIELD}")
            continue

        logger.info(f"Fetching details for EntryId: {entry_id} from URL: {medicine_url}")
        medicine_detail = fetch_medicine_detail(api_key, access_token, medicine_url)

        if medicine_detail and ADDITIONAL_FIELD in medicine_detail:
            field_value = medicine_detail[ADDITIONAL_FIELD]
            logger.info(f"Updating EntryId: {entry_id} with {ADDITIONAL_FIELD}: {field_value}")
            update_dynamodb(entry_id, ADDITIONAL_FIELD, field_value)

        processed_count += 1

    last_evaluated_key = response.get('LastEvaluatedKey')
    more_items = last_evaluated_key is not None

    return {
        'statusCode': 200,
        'moreItems': more_items,
        'lastEvaluatedKey': last_evaluated_key,
        'processedCount': processed_count
    }

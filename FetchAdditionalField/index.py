import os
import requests
import jwt
import time
import json
import logging
import uuid
from cryptography.hazmat.primitives import serialization
import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

# Initialize boto3 clients
secrets_client = boto3.client('secretsmanager')
dynamodb = boto3.resource('dynamodb')

# Load environment variables
API_KEY_SECRET = os.getenv('API_KEY_SECRET')
DYNAMODB_TABLE = os.getenv('DYNAMODB_TABLE')
PRIVATE_KEY_SECRET = os.getenv('PRIVATE_KEY_SECRET')
KEY_ID = os.getenv('KEY_ID')
ADDITIONAL_FIELD = os.getenv('ADDITIONAL_FIELD')
LOGGER_LEVEL = os.getenv('LOGGER_LEVEL', 'INFO').upper()

# Configure logging
logger = logging.getLogger()
logger.setLevel(getattr(logging, LOGGER_LEVEL, logging.INFO))
log_handler = logging.StreamHandler()
log_handler.setLevel(getattr(logging, LOGGER_LEVEL, logging.WARNING))
logger.addHandler(log_handler)

# Set base URL for 'int' environment
BASE_URL = 'https://int.api.service.nhs.uk/oauth2'
CONTENT_API_BASE_URL = 'https://int.api.service.nhs.uk/nhs-website-content'


def get_secret(secret_arn):
    try:
        logger.info(f"Retrieving secret for ARN: {secret_arn}")
        response = secrets_client.get_secret_value(SecretId=secret_arn)
        return response['SecretString']
    except ClientError as e:
        logger.error(f"Error retrieving secret: {e}")
        raise


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

    logger.info("Generating JWT token")
    token = jwt.encode(payload, private_key, algorithm='RS512', headers=headers)
    return token


def get_access_token(api_key, private_key, key_id):
    logger.info("Generating access token")
    jwt_token = generate_jwt_token(api_key, private_key, key_id)
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
        logger.error(f"Failed to get access token: {response.status_code}, {response.text}")
        raise Exception(f"Failed to get access token: {response.status_code}")


def fetch_medicine_detail(api_key, access_token, medicine_url, retries=5, backoff_factor=1.5):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "apikey": api_key,
        "Content-Type": "application/json"
    }

    logger.info(f"Fetching medicine details from URL: {medicine_url}")
    for attempt in range(retries):
        response = requests.get(medicine_url, headers=headers)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 429:
            wait_time = backoff_factor ** attempt
            logger.warning(f"Rate limit hit. Retrying in {wait_time} seconds...")
            time.sleep(wait_time)
        else:
            logger.error(f"Failed to retrieve medicine detail: {response.status_code}, {response.text}")
            return None
    logger.error("Max retries exceeded")
    return None


def update_dynamodb(url, name, field_name, field_value):
    table = dynamodb.Table(DYNAMODB_TABLE)
    try:
        logger.info(f"Updating DynamoDB for URL: {url}")
        response = table.update_item(
            Key={
                'URL': url,
                'Name': name
            },
            UpdateExpression=f"SET #field = :value",
            ExpressionAttributeNames={
                "#field": field_name
            },
            ExpressionAttributeValues={
                ":value": field_value
            },
            ReturnValues="UPDATED_NEW"
        )
        logger.info(f"DynamoDB update response: {response}")
        return True
    except ClientError as e:
        logger.error(f"Error updating DynamoDB: {e}")
        return False


def lambda_handler(event, context):
    logger.info(f"Event received: {json.dumps(event)}")

    # Fetch the secrets from Secrets Manager
    try:
        api_key = get_secret(API_KEY_SECRET)
        private_key = get_secret(PRIVATE_KEY_SECRET)
    except Exception as e:
        logger.error(f"Error fetching secrets: {e}")
        return {
            'statusCode': 500,
            'body': f"Error fetching secrets: {e}"
        }

    # Decode the API key if it is in JSON format
    try:
        api_key = json.loads(api_key)['API_KEY']
    except json.JSONDecodeError:
        pass  # It's already a plain string

    try:
        private_key = serialization.load_pem_private_key(
            private_key.encode(),
            password=None,
        )
    except Exception as e:
        logger.error(f"Error loading private key: {e}")
        return {
            'statusCode': 500,
            'body': f"Error loading private key: {e}"
        }

    try:
        access_token = get_access_token(api_key, private_key, KEY_ID)
    except Exception as e:
        logger.error(f"Error getting access token: {e}")
        return {
            'statusCode': 500,
            'body': f"Error getting access token: {e}"
        }

    table = dynamodb.Table(DYNAMODB_TABLE)

    # Query for the first 25 items without the ADDITIONAL_FIELD
    accumulated_items = []
    last_evaluated_key = None

    while len(accumulated_items) < 25:
        query_params = {
            'FilterExpression': Attr(ADDITIONAL_FIELD).not_exists(),
            'Limit': 25
        }
        if last_evaluated_key:
            query_params['ExclusiveStartKey'] = last_evaluated_key

        response = table.scan(**query_params)
        items = response['Items']
        accumulated_items.extend(items)

        last_evaluated_key = response.get('LastEvaluatedKey')
        if not last_evaluated_key:
            break

    accumulated_items = accumulated_items[:25]  # Ensure we only have up to 25 items

    logger.info(f"Number of items retrieved: {len(accumulated_items)}")
    if accumulated_items:
        logger.info(f"Items retrieved: {json.dumps(accumulated_items)}")
    else:
        logger.info("No items retrieved")

    processed_items = set()

    for item in accumulated_items:
        url = item['URL']
        name = item['Name']

        logger.info(f"Fetching details for URL: {url}")
        medicine_detail = fetch_medicine_detail(api_key, access_token, url)

        if medicine_detail and ADDITIONAL_FIELD in medicine_detail:
            field_value = medicine_detail[ADDITIONAL_FIELD]
            logger.info(f"Updating URL: {url} with {ADDITIONAL_FIELD}: {field_value}")
            update_success = update_dynamodb(url, name, ADDITIONAL_FIELD, field_value)
            if update_success:
                processed_items.add(url)
            else:
                logger.error(f"Failed to update DynamoDB for URL: {url}")
        else:
            logger.warning(f"No {ADDITIONAL_FIELD} found for URL: {url}")

    more_items = len(accumulated_items) == 25

    result = {
        'statusCode': 200,
        'moreItems': more_items,
        'ProcessedItems': list(processed_items)  # Convert set to list for JSON serialization
    }
    logger.info(f"Lambda function result: {json.dumps(result)}")
    return result

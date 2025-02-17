import os
import requests
import jwt
import time
import uuid
import json
import logging
from cryptography.hazmat.primitives import serialization
import boto3
from boto3.dynamodb.conditions import Key

# Initialize boto3 clients
secrets_client = boto3.client('secretsmanager')
dynamodb = boto3.resource('dynamodb')

# Load environment variables
API_KEY_SECRET = os.getenv('API_KEY_SECRET')
DYNAMODB_TABLE = os.getenv('DYNAMODB_TABLE')
PRIVATE_KEY_SECRET = os.getenv('PRIVATE_KEY_SECRET')
KEY_ID = os.getenv('KEY_ID')
LOGGER_LEVEL = os.getenv('LOGGER_LEVEL', 'WARNING').upper()

# Configure logging
logger = logging.getLogger()
logger.setLevel(getattr(logging, LOGGER_LEVEL, logging.WARNING))

# Set base URL for 'int' environment
BASE_URL = 'https://int.api.service.nhs.uk/oauth2'
CONTENT_API_BASE_URL = 'https://int.api.service.nhs.uk/nhs-website-content'


def get_secret(secret_arn):
    """Retrieve a secret from AWS Secrets Manager."""
    response = secrets_client.get_secret_value(SecretId=secret_arn)
    return response['SecretString']


def generate_jwt_token(api_key, private_key, key_id):
    """Generate a JWT token for authentication."""
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
    """Get an access token from the NHS API."""
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


def list_medicines(api_key, access_token, page=1, retries=5, backoff_factor=1.5):
    """Fetch the list of medicines from the NHS API."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "apikey": api_key,
        "Content-Type": "application/json"
    }
    url = f"{CONTENT_API_BASE_URL}/medicines?page={page}"

    for attempt in range(retries):
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 429:
            wait_time = backoff_factor ** attempt
            logger.warning(f"Rate limit hit. Retrying in {wait_time} seconds...")
            time.sleep(wait_time)
        else:
            logger.error(f"Failed to retrieve medicines list: {response.status_code}")
            return None
    raise Exception("Max retries exceeded")


def write_to_dynamodb(medicines_data):
    """Write the list of medicines to the DynamoDB table."""
    table = dynamodb.Table(DYNAMODB_TABLE)
    for item in medicines_data:
        entry_id = item['url'].rstrip('/').split('/')[-1]
        table.put_item(
            Item={
                'EntryId': entry_id,
                'Name': item['name'],
                'URL': item['url']
            }
        )


def lambda_handler(event, context):
    """Main Lambda handler function."""
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
    medicines_data = []
    page = 1

    while True:
        logger.info(f"Fetching page {page}...")
        medicines = list_medicines(api_key, access_token, page)
        if not medicines or not medicines.get('significantLink'):
            break

        # Extract medicine names and URLs
        for item in medicines['significantLink']:
            medicines_data.append({'name': item['name'], 'url': item['url']})

        # Check if there is a next page
        next_page_link = next((link for link in medicines.get('relatedLink', []) if link.get('name') == 'Next Page'), None)
        if not next_page_link:
            break

        page += 1

    # Save each medicine name and URL to DynamoDB as a separate entry
    write_to_dynamodb(medicines_data)

    return {
        'statusCode': 200,
        'body': json.dumps('Medicines names and URLs have been saved to DynamoDB successfully')
    }

import os
import json
import base64
import logging
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import boto3

# Setup logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables for file names and keys
BASE_NAME = os.environ['BASE_NAME']
KEYS_DIR = '/tmp'  # Lambda's writable directory
PRIVATE_KEY_FILE = os.path.join(KEYS_DIR, f'{BASE_NAME}-private-key.pem')
PUBLIC_KEY_FILE = os.path.join(KEYS_DIR, f'{BASE_NAME}-public-key.pem')
JWKS_FILE = os.path.join(KEYS_DIR, f'{BASE_NAME}-jwks.json')
KID = os.environ['BASE_NAME']

# Initialize boto3 clients
secrets_client = boto3.client('secretsmanager')
ssm_client = boto3.client('ssm')


def generate_rsa_keys():
    os.makedirs(KEYS_DIR, exist_ok=True)

    # Generate private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=4096
    )

    # Save private key to file
    with open(PRIVATE_KEY_FILE, 'wb') as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))

    # Generate public key
    public_key = private_key.public_key()

    # Save public key to file
    with open(PUBLIC_KEY_FILE, 'wb') as f:
        f.write(public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ))


def extract_modulus_exponent(public_key):
    numbers = public_key.public_numbers()
    modulus = numbers.n
    exponent = numbers.e
    return modulus, exponent


def base64_url_encode(data):
    return base64.urlsafe_b64encode(data).decode('utf-8').rstrip('=')


def create_jwks(modulus, exponent):
    n = base64_url_encode(modulus.to_bytes((modulus.bit_length() + 7) // 8, 'big'))
    e = base64_url_encode(exponent.to_bytes((exponent.bit_length() + 7) // 8, 'big'))
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "kid": KID,
                "n": n,
                "e": e,
                "alg": "RS512"
            }
        ]
    }
    return jwks


def save_secret(secret_name, secret_value):
    secrets_client.put_secret_value(SecretId=secret_name, SecretString=secret_value)


def save_parameter(param_name, param_value):
    ssm_client.put_parameter(Name=param_name, Value=param_value, Type='String', Overwrite=True)


def lambda_handler(event, context):
    logger.info("Generating RSA key pair...")
    generate_rsa_keys()

    logger.info("Extracting modulus and exponent...")
    with open(PUBLIC_KEY_FILE, 'rb') as f:
        public_key = serialization.load_pem_public_key(f.read())
    modulus, exponent = extract_modulus_exponent(public_key)

    logger.info("Creating JWKS file...")
    jwks = create_jwks(modulus, exponent)
    jwks_json = json.dumps(jwks, indent=4)

    # Save secrets
    save_secret(f'{BASE_NAME}-private-key', open(PRIVATE_KEY_FILE).read())

    # Save JWKS and public key to SSM Parameter Store
    save_parameter(f'/{BASE_NAME}/jwks', jwks_json)
    public_key_content = open(PUBLIC_KEY_FILE).read()
    save_parameter(f'/{BASE_NAME}/public-key', public_key_content)

    # Return public key parameter name for CloudFormation output
    public_key_param_name = f'/{BASE_NAME}/public-key'
    return {
        'StatusCode': 200,
        'PublicKeyParamName': public_key_param_name,
        'JWKSParamName': f'/{BASE_NAME}/jwks'
    }


if __name__ == "__main__":
    lambda_handler({}, {})

# sam nhs queries

This projects demonstrates authenticating to the NHS content API and archiving data from it methodically using Serverless Application Model (SAM) with Python.

## Components 

### GetAuth Function

Set up authentication for API access to NHS digital 'NHS Web Content' API. This lambda creates a public/private RSA key pair, then extracts the modulus from the public key to create a matching JWKS (JSON Web Key Set). The private key is put to Secrets Manager, the public key and JWKS are put to parameter store. The JWKS needs to be posted to NHS digital separately for use.

### ListAllMedicines Function

This lambda uses an NHS Digital API key together with the RSA Private key and JWKS created by the GetAuth function above to authenticate to the 'NHS web content API', get a JWT bearer token (valid for 5 minutes), and get a list of all the medicines described there. The API is rate limited and so we have exponential back off to assist retries. At the time of writing there are only 274 medicines listed there and so this can still be done reasonably with a single Lambda. The output is written to DynamoDB using the last segment of the medicine URL as the partition key, e.g:

```json
{
  "EntryId": {
    "S": "aspirin-for-pain-relief"
  },
  "Name": {
    "S": "Aspirin for pain relief"
  },
  "URL": {
    "S": "https://int.api.service.nhs.uk/nhs-website-content/medicines/aspirin-for-pain-relief/"
  }
}
```

### DynamoDBTable

This is where we output our retrieved information

## Deployment/use

### At [NHS Digital onboarding](https://onboarding.prod.api.platform.nhs.uk/):

1. Create NHS developer account
2. Register your new application in environment 'Integration test'
3. Ensure `Connected APIs' includes `NHS Website Content API (Integration Testing Environment)`
4. Edit API - create and get 'key' (secret not needed)

### In AWS

5. Create API key secret (`API_KEY`:`<value_from_above>`) and get arn for SAM stack

6. Deploy with e.g.

```bash
sam build && \
sam deploy \
    --stack-name NHSMedicines \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides $(jq -r 'to_entries | map("\(.key)=\(.value|tostring)") | .[]' vars.json) \
    --region eu-west-2 \
    --resolve-s3 
```

7. Trigger our newly created 'GetAuth' Lambda function manually, e.g. in the Console with 'test'. This should populate the private key and SSM parameter public key and jwks
8. Collect jwks from parameter store and save as, e.g. 'key.json'

### At NHS Digital onboarding/Home/My applications and teams:

9. 'Edit' Public key URL
10. upload 'key.json' created above and save
11. Ensure key is recognised as valid

### In AWS

12. Trigger `ListAllMedicinesFunction` 
13. Review DynamoDB table


## Cleanup

```bash
sam delete \
    --stack-name NHSMedicines \
    --region eu-west-2
```
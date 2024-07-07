# sam nhs queries

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

## cleanup
```bash
sam delete \
    --stack-name NHSMedicines \
    --region eu-west-2
```
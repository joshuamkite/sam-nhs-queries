# sam nhs queries

This projects demonstrates authenticating to the NHS content API and archiving data from it methodically to DynamoDB using Serverless Application Model (SAM) with Python.

- [sam nhs queries](#sam-nhs-queries)
  - [Components](#components)
    - [GetAuth Function](#getauth-function)
    - [ListAllMedicines Function](#listallmedicines-function)
    - [FetchAdditionalField Lambda](#fetchadditionalfield-lambda)
    - [State Machine](#state-machine)
    - [DynamoDBTable](#dynamodbtable)
  - [Deployment/use](#deploymentuse)
    - [At NHS Digital onboarding:](#at-nhs-digital-onboarding)
    - [In AWS](#in-aws)
    - [At NHS Digital onboarding/Home/My applications and teams:](#at-nhs-digital-onboardinghomemy-applications-and-teams)
    - [In AWS](#in-aws-1)
- [Warning](#warning)
  - [Cleanup](#cleanup)

## Components 

For each Lambda logs there is a configurable Logger Level set based on an environment variable with a default to show only errors or warnings.

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

### FetchAdditionalField Lambda

This Lambda function is designed to fetch an additional field for each medicine from the NHS API and update the DynamoDB table with the retrieved information. It processes items in batches to handle large datasets efficiently and ensures only one instance runs at a time using a Step Function.

**Available Fields**: You can configure the Lambda to fetch any additional field provided by the NHS API. Common fields include:
- `description`
- `about`

**Configuration**:
- **AdditionalField**: This parameter allows you to specify which field to fetch for each medicine. Update the `AdditionalField` parameter in the `template.yaml` to select the field you want. But see [warning](#warning) below.
 
**Batch Processing**:
- The Lambda function processes items in batches of 25 and uses DynamoDB pagination to handle large datasets efficiently. It accumulates up to 25 items that do not have the specified additional field and processes them in each iteration.

**Retry Logic**:
- The Lambda function includes retry logic to handle temporary API rate limits. It uses exponential backoff to retry requests if rate limits are hit.

**DynamoDB Update**:
- The Lambda function updates the DynamoDB table with the new field for each medicine entry, ensuring no duplicate work is done. Only items that do not already have the additional field populated are processed.

**Pagination Handling**:
- The function handles DynamoDB pagination by checking the `LastEvaluatedKey` and continuing to scan until it accumulates the required number of items or reaches the end of the table.

**Template Configuration**:
```yaml
Parameters:
  AdditionalField:
    Type: String
    Description: "The additional field to fetch for each medicine"
    Default: "description"
```

**Example Usage**:
- To change the field fetched by the Lambda, modify the `AdditionalField` parameter in the CloudFormation template to the desired field name (e.g., `sideEffects`).

### State Machine

The state machine orchestrates the FetchAdditionalField Lambda function to ensure only one instance runs at a time. This helps in efficiently managing the processing of large datasets without overwhelming the system.

**State Machine Definition**:
- The state machine is defined in the CloudFormation template using the `AWS::StepFunctions::StateMachine` resource.
- The state machine starts with the `FetchAdditionalField` task, which invokes the FetchAdditionalField Lambda function.
- If there are more items to process (`moreItems` is true), the state machine waits for 1 second and then invokes the Lambda function again.
- The state machine exits when there are no more items to process.

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
14. Start the state machine execution from the AWS Step Functions console to begin processing the items in DynamoDB. The state machine will ensure that only one instance of the FetchAdditionalField Lambda function runs at a time, processing the items in batches.
15.  Review DynamoDB table

# Warning

Be aware that due to [this open issue with SAM](https://github.com/aws/aws-sam-cli/issues/4404) updating parameters on a deployed stack may not be recognised. If your changes don't appear to make a difference then you may need to verify these are correct manually.

By default directly deployed resources, such as the DynamoDB table are deleted on stack deletion. Whilst this is suitable for PoC work, it may not be suitable for your use case.

## Cleanup

```bash
sam delete \
    --stack-name NHSMedicines \
    --region eu-west-2
```

Delete your log groups 

You will need to suspend versioning in your SAM S3 bucket if you opted to have this managed for you before you can truly empty and delete it.

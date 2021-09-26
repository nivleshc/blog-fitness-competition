# blog-fitness-competition
This repository contains code for deploying a fitness competition game, using AWS Lambda, AWS S3 bucket, AWS Systems Manager Parameter Store and Fitbit devices.

The solution uses AWS Serverless Application Model (SAM) to deploy resources in to an AWS Account. The AWS Lambda function is written in Python 3.7.

## Preparation
### Fitbit Data Access
Use the instructions at https://nivleshc.wordpress.com/2021/09/21/create-a-fitness-competition-using-aws-lambda-and-fitbit-part-1/ to configure access to the individual player's Fitbit steps.

### Code
Clone this repository using the following command.
```
git clone https://github.com/nivleshc/blog-fitness-competition.git
```

Export the following environment variables.

```
export AWS_PROFILE_NAME={aws profile to use}

export AWS_S3_BUCKET_NAME={name of aws s3 bucket to store SAM artefacts in}

export SLACK_WEBHOOK_URL={slack webhook url to use for sending slack notifications}
```

## Commands

For help, run the following command:
```
make
```
To deploy the code in this repository to your AWS account, use the following steps:

```
make package
make deploy
```

If you make any changes to **template.yaml**, first validate the changes by using the following command (validation is not required if you change other files):
```
make validate
```

After validation is successful, use the following command to deploy the changes:
```
make update
```


#!/bin/bash
# deploy.sh — Deploy all Lambda functions to AWS
# Usage: ./deploy.sh [environment]

set -e

ENV=${1:-dev}
REGION="us-east-1"
RUNTIME="python3.12"
TIMEOUT=60
MEMORY=256

FUNCTIONS=(
  "ec2_health_monitor"
  "cost_anomaly_detector"
  "stale_resource_cleaner"
)

echo "Deploying Lambda functions to ${ENV}..."

for func in "${FUNCTIONS[@]}"; do
  FUNC_NAME="${func}-${ENV}"
  FUNC_DIR="functions/${func}"

  echo ""
  echo "--- Deploying: ${FUNC_NAME} ---"

  # Package
  cd "${FUNC_DIR}"
  zip -q function.zip lambda_function.py
  cd ../..

  # Check if function exists
  if aws lambda get-function --function-name "${FUNC_NAME}" --region "${REGION}" 2>/dev/null; then
    echo "Updating existing function..."
    aws lambda update-function-code \
      --function-name "${FUNC_NAME}" \
      --zip-file "fileb://${FUNC_DIR}/function.zip" \
      --region "${REGION}"
  else
    echo "Creating new function..."
    aws lambda create-function \
      --function-name "${FUNC_NAME}" \
      --runtime "${RUNTIME}" \
      --handler "lambda_function.lambda_handler" \
      --zip-file "fileb://${FUNC_DIR}/function.zip" \
      --timeout "${TIMEOUT}" \
      --memory-size "${MEMORY}" \
      --region "${REGION}" \
      --role "arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/lambda-execution-role"
  fi

  # Cleanup
  rm -f "${FUNC_DIR}/function.zip"
  echo "✓ ${FUNC_NAME} deployed"
done

echo ""
echo "All functions deployed successfully!"

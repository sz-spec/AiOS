"""
Deployment Module
=================
Production deployment patterns for LangGraph agents.

Available Deployments:
- AWS Lambda with DynamoDB checkpointing
- Streaming support
- Cold start optimization

Usage:
    from deployment import LambdaHandler, LambdaConfig

    handler = LambdaHandler(workflow)

    def lambda_handler(event, context):
        return handler.handle(event, context)

CDK Deployment:
    See aws_lambda.CDK_TEMPLATE for AWS CDK stack

Terraform:
    See aws_lambda.TERRAFORM_TEMPLATE for Terraform config
"""

from .aws_lambda import (
    LambdaConfig,
    LambdaAgentState,
    LambdaHandler,
    DynamoDBCheckpointer,
    keep_warm_handler,
    create_local_handler,
    MockContext,
    CDK_TEMPLATE,
    TERRAFORM_TEMPLATE,
    AWS_AVAILABLE,
    DYNAMODB_CHECKPOINTER_AVAILABLE,
)

__all__ = [
    "LambdaConfig",
    "LambdaAgentState",
    "LambdaHandler",
    "DynamoDBCheckpointer",
    "keep_warm_handler",
    "create_local_handler",
    "MockContext",
    "CDK_TEMPLATE",
    "TERRAFORM_TEMPLATE",
    "AWS_AVAILABLE",
    "DYNAMODB_CHECKPOINTER_AVAILABLE",
]

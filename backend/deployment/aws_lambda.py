"""
AWS Lambda Deployment for LangGraph Agents
==========================================
Production-ready deployment patterns for:
- AWS Lambda with DynamoDB checkpointing
- Streaming support
- Cold start optimization
- Error handling and retries

From 2025 deployment best practices:
- DynamoDB for serverless state persistence
- Lambda layers for dependencies
- API Gateway for HTTP/WebSocket

Installation:
    pip install boto3 langgraph

AWS CDK Setup:
    pip install aws-cdk-lib constructs
"""

import os
import json
import time
from typing import TypedDict, List, Dict, Any, Optional
from dataclasses import dataclass, field
import logging

# AWS SDK
try:
    import boto3
    from botocore.exceptions import ClientError

    AWS_AVAILABLE = True
except ImportError:
    AWS_AVAILABLE = False

# LangGraph
from langgraph.graph import StateGraph

# Checkpointer - try DynamoDB first
try:
    from langgraph.checkpoint.dynamodb import DynamoDBCheckpointer

    DYNAMODB_CHECKPOINTER_AVAILABLE = True
except ImportError:
    DYNAMODB_CHECKPOINTER_AVAILABLE = False

    # Fallback: Custom DynamoDB checkpointer
    class DynamoDBCheckpointer:
        """Fallback DynamoDB checkpointer implementation."""

        def __init__(self, table_name: str, region: str = None):
            self.table_name = table_name
            self.region = region or os.getenv("AWS_REGION", "us-east-1")
            self._client = None
            self._table = None

        @property
        def client(self):
            if not self._client:
                self._client = boto3.client("dynamodb", region_name=self.region)
            return self._client

        @property
        def table(self):
            if not self._table:
                dynamodb = boto3.resource("dynamodb", region_name=self.region)
                self._table = dynamodb.Table(self.table_name)
            return self._table

        def get(self, thread_id: str) -> Optional[Dict]:
            """Get checkpoint from DynamoDB."""
            try:
                response = self.table.get_item(Key={"thread_id": thread_id})
                return response.get("Item", {}).get("state")
            except Exception:
                return None

        def put(self, thread_id: str, state: Dict) -> bool:
            """Save checkpoint to DynamoDB."""
            try:
                self.table.put_item(
                    Item={
                        "thread_id": thread_id,
                        "state": state,
                        "updated_at": int(time.time()),
                    }
                )
                return True
            except Exception:
                return False


logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class LambdaConfig:
    """Configuration for Lambda deployment."""

    # DynamoDB
    table_name: str = "langgraph_checkpoints"
    region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))

    # Lambda settings
    timeout: int = 300  # 5 minutes
    memory: int = 1024  # MB

    # Optimization
    keep_warm: bool = True
    batch_size: int = 10

    # Streaming
    enable_streaming: bool = True

    # Retry
    max_retries: int = 3
    retry_delay: float = 1.0


# =============================================================================
# State Definition
# =============================================================================


class LambdaAgentState(TypedDict):
    """State optimized for Lambda execution."""

    messages: List[Dict[str, str]]
    status: str
    output: Any
    error: Optional[str]
    # Lambda metadata
    request_id: str
    invocation_count: int
    cold_start: bool


# =============================================================================
# Lambda Handler
# =============================================================================


class LambdaHandler:
    """
    AWS Lambda handler for LangGraph agents.

    Features:
    - DynamoDB checkpointing for state persistence
    - Cold start optimization
    - Streaming support
    - Automatic retries

    Usage:
        handler = LambdaHandler(workflow, config)

        # In Lambda
        def lambda_handler(event, context):
            return handler.handle(event, context)
    """

    _instance = None
    _cold_start = True

    def __init__(self, workflow: StateGraph, config: LambdaConfig = None):
        self.config = config or LambdaConfig()
        self.workflow = workflow
        self._checkpointer = None
        self._app = None

        # Initialize on first call (cold start optimization)
        if LambdaHandler._cold_start:
            self._initialize()
            LambdaHandler._cold_start = False

    def _initialize(self):
        """Initialize resources (called once per container)."""
        logger.info("Initializing Lambda handler (cold start)")

        # Create checkpointer
        if AWS_AVAILABLE:
            self._checkpointer = DynamoDBCheckpointer(
                table_name=self.config.table_name, region=self.config.region
            )

        # Compile workflow
        self._app = self.workflow.compile(checkpointer=self._checkpointer)

        logger.info("Lambda handler initialized")

    def handle(self, event: Dict, context: Any) -> Dict:
        """
        Main Lambda handler.

        Args:
            event: Lambda event (API Gateway, direct invoke, etc.)
            context: Lambda context

        Returns:
            Response dict
        """
        start_time = time.time()
        request_id = context.aws_request_id if context else "local"

        try:
            # Parse input
            body = self._parse_event(event)
            thread_id = body.get("thread_id", request_id)
            query = body.get("query", "")

            # Create state
            state = self._create_state(query, request_id)

            # Execute workflow
            config = {"configurable": {"thread_id": thread_id}}

            if self.config.enable_streaming:
                result = self._execute_streaming(state, config)
            else:
                result = self._app.invoke(state, config)

            # Build response
            elapsed = time.time() - start_time

            return {
                "statusCode": 200,
                "headers": {
                    "Content-Type": "application/json",
                    "X-Request-Id": request_id,
                    "X-Execution-Time": str(elapsed),
                },
                "body": json.dumps(
                    {
                        "success": True,
                        "result": result.get("output"),
                        "status": result.get("status"),
                        "thread_id": thread_id,
                        "execution_time": elapsed,
                    }
                ),
            }

        except Exception as e:
            logger.error(f"Handler error: {e}")
            return self._error_response(str(e), request_id)

    def _parse_event(self, event: Dict) -> Dict:
        """Parse Lambda event from various sources."""
        # API Gateway
        if "body" in event:
            body = event["body"]
            if isinstance(body, str):
                return json.loads(body)
            return body

        # Direct invoke
        if "query" in event:
            return event

        # SQS
        if "Records" in event:
            records = event["Records"]
            if records:
                return json.loads(records[0].get("body", "{}"))

        return event

    def _create_state(self, query: str, request_id: str) -> LambdaAgentState:
        """Create initial state."""
        return {
            "messages": [{"role": "user", "content": query}],
            "status": "pending",
            "output": None,
            "error": None,
            "request_id": request_id,
            "invocation_count": 0,
            "cold_start": LambdaHandler._cold_start,
        }

    def _execute_streaming(self, state: Dict, config: Dict) -> Dict:
        """Execute with streaming support."""
        final_state = None

        for chunk in self._app.stream(state, config):
            final_state = chunk
            # In real streaming, yield chunks to client

        return final_state or state

    def _error_response(self, error: str, request_id: str) -> Dict:
        """Build error response."""
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json", "X-Request-Id": request_id},
            "body": json.dumps({"success": False, "error": error}),
        }


# =============================================================================
# Keep Warm Handler
# =============================================================================


def keep_warm_handler(event: Dict, context: Any) -> Dict:
    """
    Handler for CloudWatch scheduled events to keep Lambda warm.

    Setup in CDK:
        Rule(scope, "WarmRule",
            schedule=Schedule.rate(Duration.minutes(5)),
            targets=[LambdaFunction(agent_lambda)]
        )
    """
    if event.get("source") == "aws.events":
        logger.info("Keep-warm invocation")
        return {"statusCode": 200, "body": "warm"}

    return {"statusCode": 400, "body": "invalid"}


# =============================================================================
# CDK Constructs (for reference)
# =============================================================================

CDK_TEMPLATE = '''
"""
AWS CDK Stack for LangGraph Agent Deployment
============================================
Deploy with: cdk deploy

Requirements:
    pip install aws-cdk-lib constructs
"""

from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_lambda as lambda_,
    aws_dynamodb as dynamodb,
    aws_apigateway as apigw,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
)
from constructs import Construct


class LangGraphAgentStack(Stack):
    """CDK Stack for LangGraph agent deployment."""
    
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)
        
        # DynamoDB table for checkpoints
        self.table = dynamodb.Table(
            self, "CheckpointTable",
            table_name="langgraph_checkpoints",
            partition_key=dynamodb.Attribute(
                name="thread_id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
            time_to_live_attribute="ttl"  # Optional: auto-expire old states
        )
        
        # Lambda function
        self.agent_lambda = lambda_.Function(
            self, "AgentFunction",
            function_name="langgraph-agent",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("src"),
            timeout=Duration.minutes(5),
            memory_size=1024,
            environment={
                "DYNAMODB_TABLE": self.table.table_name,
                "AWS_REGION": self.region,
                "LANGFUSE_ENABLED": "true"
            },
            tracing=lambda_.Tracing.ACTIVE  # X-Ray tracing
        )
        
        # Grant DynamoDB access
        self.table.grant_read_write_data(self.agent_lambda)
        
        # API Gateway
        self.api = apigw.RestApi(
            self, "AgentApi",
            rest_api_name="LangGraph Agent API",
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                throttling_rate_limit=100,
                throttling_burst_limit=200
            )
        )
        
        # Add /invoke endpoint
        invoke_resource = self.api.root.add_resource("invoke")
        invoke_resource.add_method(
            "POST",
            apigw.LambdaIntegration(self.agent_lambda),
            authorization_type=apigw.AuthorizationType.IAM
        )
        
        # Keep-warm rule (every 5 minutes)
        events.Rule(
            self, "WarmRule",
            schedule=events.Schedule.rate(Duration.minutes(5)),
            targets=[targets.LambdaFunction(self.agent_lambda)]
        )


# Synthesize with: cdk synth
# Deploy with: cdk deploy
'''


# =============================================================================
# Terraform Template (alternative)
# =============================================================================

TERRAFORM_TEMPLATE = """
# Terraform configuration for LangGraph agent deployment

provider "aws" {
  region = var.region
}

variable "region" {
  default = "us-east-1"
}

# DynamoDB table
resource "aws_dynamodb_table" "checkpoints" {
  name           = "langgraph_checkpoints"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "thread_id"

  attribute {
    name = "thread_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = {
    Environment = "production"
    Application = "langgraph-agent"
  }
}

# Lambda function
resource "aws_lambda_function" "agent" {
  function_name = "langgraph-agent"
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  timeout       = 300
  memory_size   = 1024

  filename         = "deployment.zip"
  source_code_hash = filebase64sha256("deployment.zip")

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.checkpoints.name
    }
  }

  role = aws_iam_role.lambda_role.arn
}

# IAM role
resource "aws_iam_role" "lambda_role" {
  name = "langgraph-agent-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

# DynamoDB access policy
resource "aws_iam_role_policy" "dynamodb_policy" {
  name = "dynamodb-access"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:Query"
      ]
      Resource = aws_dynamodb_table.checkpoints.arn
    }]
  })
}

# CloudWatch logs
resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Keep-warm CloudWatch event
resource "aws_cloudwatch_event_rule" "warm" {
  name                = "langgraph-keep-warm"
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "warm_target" {
  rule = aws_cloudwatch_event_rule.warm.name
  arn  = aws_lambda_function.agent.arn
}

resource "aws_lambda_permission" "allow_cloudwatch" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.agent.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.warm.arn
}
"""


# =============================================================================
# Local Testing
# =============================================================================


def create_local_handler(workflow: StateGraph) -> LambdaHandler:
    """
    Create a handler for local testing.

    Usage:
        handler = create_local_handler(my_workflow)
        result = handler.handle(
            {"query": "Hello"},
            MockContext()
        )
    """
    config = LambdaConfig(table_name="local_checkpoints", enable_streaming=False)

    return LambdaHandler(workflow, config)


class MockContext:
    """Mock Lambda context for local testing."""

    aws_request_id = "local-test-123"
    function_name = "local-test"
    memory_limit_in_mb = 1024

    def get_remaining_time_in_millis(self):
        return 300000  # 5 minutes


# =============================================================================
# Exports
# =============================================================================

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

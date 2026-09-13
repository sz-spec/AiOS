"""
Tools API for Code Execution
=============================
Internal API that allows code executed in the sandbox to call other tools.
This implements the "Code Execution with MCP" pattern from Anthropic.

Based on: https://www.anthropic.com/engineering/code-execution-with-mcp

Key Benefits:
- Progressive tool disclosure (load only what's needed)
- Context-efficient results (filter data in code)
- Privacy-preserving operations (data stays in sandbox)
- State persistence across operations
"""

import json
from typing import Any, Dict, Optional
from pathlib import Path

from ai.agents.tool_output_sanitization import sanitize_tool_output


class ToolsAPI:
    """
    API for accessing V-OS tools from within code execution.

    This class provides a programmatic interface to all V-OS tools,
    allowing code to compose complex operations without passing
    intermediate results through the model's context.
    """

    def __init__(self, workspace_dir: str = "/tmp/v-os-workspace"):
        """
        Initialize Tools API.

        Args:
            workspace_dir: Directory for persisting files and state
        """
        self.workspace_dir = Path(workspace_dir)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        # Import tools - lazy loading to avoid dependencies
        self._web_search_impl = None
        self._calculator_impl = None
        self._file_analyzer_impl = None
        self._stripe_impl = None
        self._gmail_impl = None
        self._whatsapp_impl = None

    def web_search(self, query: str) -> Dict[str, Any]:
        """
        Perform web search and return structured results.

        Args:
            query: Search query

        Returns:
            Dict with 'results' (list) and 'summary' (str)
        """
        if self._web_search_impl is None:
            self._web_search_impl = self._load_web_search()

        try:
            raw_results = self._web_search_impl(query)

            # AA2 boundary — sanitize tool output before it reaches the
            # model. Strips HTML script/iframe/object vectors, dangerous
            # ``javascript:``/``data:``/``vbscript:`` URLs, ANSI escapes,
            # zero-width chars, bidi-override chars; wraps content matching
            # prompt-injection patterns in [UNTRUSTED-DATA-*] tags; caps at
            # 64 KB. The original (raw) text is **not** retained on the
            # returned dict — only the cleaned form is exposed to callers.
            sanitization = sanitize_tool_output(
                raw_results if isinstance(raw_results, str) else str(raw_results),
                source="web_search",
            )

            # Parse and structure results
            results = {
                "query": query,
                "raw_text": sanitization.cleaned,
                "sanitization": {
                    "detected_patterns": sanitization.detected_patterns,
                    "is_suspicious": sanitization.is_suspicious,
                    "truncated": sanitization.truncated,
                    "wrapped": sanitization.wrapped,
                },
                "success": True,
            }

            return results

        except Exception as e:
            return {"query": query, "error": str(e), "success": False}

    def calculator(self, expression: str) -> Dict[str, Any]:
        """
        Evaluate mathematical expression.

        Args:
            expression: Math expression to evaluate

        Returns:
            Dict with 'result' and 'expression'
        """
        if self._calculator_impl is None:
            self._calculator_impl = self._load_calculator()

        try:
            result = self._calculator_impl(expression)
            return {
                "expression": expression,
                "result": float(result) if result else None,
                "success": True,
            }
        except Exception as e:
            return {"expression": expression, "error": str(e), "success": False}

    def file_analyzer(self, content: str, analysis_type: str = "summary") -> Dict[str, Any]:
        """
        Analyze text content.

        Args:
            content: Text to analyze
            analysis_type: Type of analysis (summary, keywords, topics, sentiment)

        Returns:
            Dict with analysis results
        """
        if self._file_analyzer_impl is None:
            self._file_analyzer_impl = self._load_file_analyzer()

        try:
            result = self._file_analyzer_impl(content, analysis_type)

            # AA2 boundary — sanitize tool output before it reaches the
            # model. The 256 KB cap here is higher than ``web_search``
            # because file analysis can legitimately produce larger
            # summaries; HTML stripping is **not** applied because a
            # file under analysis may legitimately contain ``<script>``
            # literals the user wants summarized. We still strip
            # obfuscation chars and wrap injection-pattern text.
            sanitization = sanitize_tool_output(
                result if isinstance(result, str) else str(result),
                source="file_analyzer",
            )

            return {
                "analysis_type": analysis_type,
                "result": sanitization.cleaned,
                "content_length": len(content),
                "sanitization": {
                    "detected_patterns": sanitization.detected_patterns,
                    "is_suspicious": sanitization.is_suspicious,
                    "truncated": sanitization.truncated,
                    "wrapped": sanitization.wrapped,
                },
                "success": True,
            }
        except Exception as e:
            return {"analysis_type": analysis_type, "error": str(e), "success": False}

    def save_to_workspace(self, filename: str, data: Any, format: str = "json") -> str:
        """
        Save data to workspace filesystem for persistence.

        Args:
            filename: Name of file to save
            data: Data to save
            format: Format (json, text, csv)

        Returns:
            Path to saved file
        """
        filepath = self.workspace_dir / filename

        if format == "json":
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
        elif format == "text":
            with open(filepath, "w") as f:
                f.write(str(data))
        elif format == "csv":
            # Simple CSV writing
            with open(filepath, "w") as f:
                if isinstance(data, list) and data:
                    if isinstance(data[0], dict):
                        # List of dicts
                        keys = data[0].keys()
                        f.write(",".join(keys) + "\n")
                        for row in data:
                            f.write(",".join(str(row.get(k, "")) for k in keys) + "\n")
                    else:
                        # List of values
                        for item in data:
                            f.write(str(item) + "\n")

        return str(filepath)

    def load_from_workspace(self, filename: str, format: str = "json") -> Any:
        """
        Load data from workspace filesystem.

        Args:
            filename: Name of file to load
            format: Format (json, text, csv)

        Returns:
            Loaded data
        """
        filepath = self.workspace_dir / filename

        if not filepath.exists():
            raise FileNotFoundError(f"File not found: {filename}")

        if format == "json":
            with open(filepath, "r") as f:
                return json.load(f)
        elif format == "text":
            with open(filepath, "r") as f:
                return f.read()
        elif format == "csv":
            # Simple CSV reading
            with open(filepath, "r") as f:
                lines = f.readlines()
                if not lines:
                    return []

                headers = lines[0].strip().split(",")
                data = []
                for line in lines[1:]:
                    values = line.strip().split(",")
                    data.append(dict(zip(headers, values)))
                return data

    def list_workspace_files(self) -> list:
        """List all files in workspace."""
        return [f.name for f in self.workspace_dir.iterdir() if f.is_file()]

    def create_payment_session(
        self,
        amount: float,
        currency: str = "usd",
        description: str = "",
        customer_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a Stripe payment session (PRD Section 4.6).

        Args:
            amount: Amount in currency units (e.g., 50.00 for $50)
            currency: Currency code (default: usd)
            description: Payment description
            customer_email: Optional customer email

        Returns:
            Dict with 'payment_url', 'session_id', 'amount', 'currency'
        """
        if self._stripe_impl is None:
            self._stripe_impl = self._load_stripe()

        try:
            result = self._stripe_impl(amount, currency, description, customer_email)
            return {
                "payment_url": result.get("url", ""),
                "session_id": result.get("id", ""),
                "amount": amount,
                "currency": currency,
                "description": description,
                "success": True,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def send_email(
        self, to: str, subject: str, body: str, from_email: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send email via Gmail API (PRD Section 1.6 - Communication Hub).

        Args:
            to: Recipient email address
            subject: Email subject
            body: Email body (plain text or HTML)
            from_email: Optional sender email (uses default if not provided)

        Returns:
            Dict with 'message_id', 'success'
        """
        if self._gmail_impl is None:
            self._gmail_impl = self._load_gmail()

        try:
            result = self._gmail_impl(to, subject, body, from_email)
            return {
                "message_id": result.get("id", ""),
                "to": to,
                "subject": subject,
                "success": True,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def send_whatsapp(self, to: str, message: str) -> Dict[str, Any]:
        """
        Send WhatsApp message (PRD Section 1.6 - Communication Hub).

        Args:
            to: Recipient phone number (international format: +1234567890)
            message: Message text

        Returns:
            Dict with 'message_id', 'success'
        """
        if self._whatsapp_impl is None:
            self._whatsapp_impl = self._load_whatsapp()

        try:
            result = self._whatsapp_impl(to, message)
            return {"message_id": result.get("id", ""), "to": to, "success": True}
        except Exception as e:
            return {"error": str(e), "success": False}

    # Private methods for lazy loading tool implementations

    def _load_web_search(self):
        """Load web search implementation."""
        try:
            from crewai_tools import SerperDevTool

            tool = SerperDevTool()
            return lambda q: tool.run(query=q)
        except ImportError:
            try:
                from langchain_tavily import TavilySearchResults

                search = TavilySearchResults(max_results=5)
                return lambda q: "\n".join(
                    [r.get("content", "") for r in search.invoke(q)]
                )
            except:
                return lambda q: f"Search unavailable. Query was: {q}"

    def _load_calculator(self):
        """Load calculator implementation."""
        import math

        safe_dict = {
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "sum": sum,
            "pow": pow,
            "sqrt": math.sqrt,
            "log": math.log,
            "sin": math.sin,
            "cos": math.cos,
            "tan": math.tan,
            "pi": math.pi,
            "e": math.e,
        }

        def calc(expression: str) -> str:
            import ast
            import operator

            _safe_operators = {
                ast.Add: operator.add,
                ast.Sub: operator.sub,
                ast.Mult: operator.mul,
                ast.Div: operator.truediv,
                ast.FloorDiv: operator.floordiv,
                ast.Mod: operator.mod,
                ast.Pow: operator.pow,
                ast.USub: operator.neg,
                ast.UAdd: operator.pos,
            }
            _safe_constants = {"pi": math.pi, "e": math.e}

            def _eval_node(node):
                if isinstance(node, ast.Expression):
                    return _eval_node(node.body)
                elif isinstance(node, ast.Constant) and isinstance(
                    node.value, (int, float, complex)
                ):
                    return node.value
                elif isinstance(node, ast.Name) and node.id in _safe_constants:
                    return _safe_constants[node.id]
                elif isinstance(node, ast.BinOp):
                    op_func = _safe_operators.get(type(node.op))
                    if not op_func:
                        raise ValueError(
                            f"Unsupported operator: {type(node.op).__name__}"
                        )
                    return op_func(_eval_node(node.left), _eval_node(node.right))
                elif isinstance(node, ast.UnaryOp):
                    op_func = _safe_operators.get(type(node.op))
                    if not op_func:
                        raise ValueError(f"Unsupported unary: {type(node.op).__name__}")
                    return op_func(_eval_node(node.operand))
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id in safe_dict and callable(safe_dict[node.func.id]):
                        args = [_eval_node(arg) for arg in node.args]
                        return safe_dict[node.func.id](*args)
                    raise ValueError(f"Unknown function: {node.func.id}")
                raise ValueError(f"Unsupported expression: {type(node).__name__}")

            try:
                tree = ast.parse(expression, mode="eval")
                result = _eval_node(tree)
                return str(result)
            except Exception as e:
                return f"Error: {e}"

        return calc

    def _load_file_analyzer(self):
        """Load file analyzer implementation."""

        def analyze(content: str, analysis_type: str = "summary") -> str:
            # Simple analysis (can be enhanced with NLP libraries)
            if analysis_type == "summary":
                lines = content.split("\n")
                return f"Content has {len(lines)} lines, {len(content)} characters"
            elif analysis_type == "keywords":
                words = content.lower().split()
                word_freq = {}
                for word in words:
                    if len(word) > 3:
                        word_freq[word] = word_freq.get(word, 0) + 1
                top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[
                    :10
                ]
                return ", ".join([w[0] for w in top_words])
            elif analysis_type == "sentiment":
                # Very basic sentiment
                positive = ["good", "great", "excellent", "happy", "love"]
                negative = ["bad", "terrible", "awful", "hate", "sad"]
                words = content.lower().split()
                pos_count = sum(1 for w in words if w in positive)
                neg_count = sum(1 for w in words if w in negative)
                if pos_count > neg_count:
                    return "positive"
                elif neg_count > pos_count:
                    return "negative"
                else:
                    return "neutral"
            else:
                return f"Analysis type '{analysis_type}' not supported"

        return analyze

    def _load_stripe(self):
        """
        Load Stripe payment implementation (PRD Section 4.6).

        Supports:
        - Test mode (mock for development)
        - Production mode (real Stripe API)
        """
        import os

        stripe_secret = os.getenv("STRIPE_SECRET_KEY", "")

        # Test mode: mock implementation
        if not stripe_secret or stripe_secret.startswith("sk_test_mock"):

            def mock_stripe(amount, currency, description, customer_email):
                import uuid

                session_id = f"cs_test_{uuid.uuid4().hex[:24]}"
                return {
                    "id": session_id,
                    "url": f"https://checkout.stripe.com/pay/{session_id}",
                    "mode": "test",
                    "amount": amount,
                    "currency": currency,
                }

            return mock_stripe

        # Production mode: real Stripe
        try:
            import stripe

            stripe.api_key = stripe_secret

            def create_session(amount, currency, description, customer_email):
                # Convert amount to cents for Stripe
                amount_cents = int(amount * 100)

                session = stripe.checkout.Session.create(
                    payment_method_types=["card"],
                    line_items=[
                        {
                            "price_data": {
                                "currency": currency,
                                "product_data": {
                                    "name": description or "Payment",
                                },
                                "unit_amount": amount_cents,
                            },
                            "quantity": 1,
                        }
                    ],
                    mode="payment",
                    success_url="https://your-domain.com/success",
                    cancel_url="https://your-domain.com/cancel",
                    customer_email=customer_email,
                )

                return {"id": session.id, "url": session.url, "mode": "production"}

            return create_session

        except ImportError:
            # Fallback to mock if stripe not installed
            def mock_stripe(amount, currency, description, customer_email):
                import uuid

                session_id = f"cs_test_{uuid.uuid4().hex[:24]}"
                return {
                    "id": session_id,
                    "url": f"https://checkout.stripe.com/pay/{session_id}",
                    "mode": "mock_fallback",
                    "note": "Install stripe package for production: pip install stripe",
                }

            return mock_stripe

    def _load_gmail(self):
        """
        Load Gmail API implementation (PRD Section 1.6).

        Supports:
        - Test mode (saves to file)
        - Production mode (real Gmail API)
        """
        import os

        gmail_credentials = os.getenv("GMAIL_CREDENTIALS_PATH", "")

        # Test mode: save to file
        if not gmail_credentials:

            def mock_gmail(to, subject, body, from_email):
                import uuid
                import json
                from datetime import datetime, timezone

                message_id = f"msg_{uuid.uuid4().hex[:16]}"

                # Save email to workspace for inspection
                email_data = {
                    "message_id": message_id,
                    "to": to,
                    "from": from_email or "noreply@v-os.dev",
                    "subject": subject,
                    "body": body,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "mode": "test",
                }

                # Save to workspace
                workspace = Path("/tmp/v-os-workspace")
                workspace.mkdir(parents=True, exist_ok=True)
                with open(workspace / f"email_{message_id}.json", "w") as f:
                    json.dump(email_data, f, indent=2)

                return {
                    "id": message_id,
                    "mode": "test",
                    "saved_to": str(workspace / f"email_{message_id}.json"),
                }

            return mock_gmail

        # Production mode: real Gmail API
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from email.mime.text import MIMEText
            import base64

            def send_gmail(to, subject, body, from_email):
                creds = Credentials.from_authorized_user_file(gmail_credentials)
                service = build("gmail", "v1", credentials=creds)

                message = MIMEText(body)
                message["to"] = to
                message["subject"] = subject
                if from_email:
                    message["from"] = from_email

                raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
                result = (
                    service.users()
                    .messages()
                    .send(userId="me", body={"raw": raw_message})
                    .execute()
                )

                return {"id": result.get("id", ""), "mode": "production"}

            return send_gmail

        except ImportError:
            # Fallback to mock
            def mock_gmail(to, subject, body, from_email):
                import uuid

                return {
                    "id": f"msg_{uuid.uuid4().hex[:16]}",
                    "mode": "mock_fallback",
                    "note": "Install google-api-python-client for production",
                }

            return mock_gmail

    def _load_whatsapp(self):
        """
        Load WhatsApp implementation (PRD Section 1.6).

        Supports:
        - Test mode (saves to file)
        - Production mode (Twilio WhatsApp API)
        """
        import os

        twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
        twilio_whatsapp_from = os.getenv("TWILIO_WHATSAPP_FROM", "")

        # Test mode: save to file
        if not all([twilio_account_sid, twilio_auth_token, twilio_whatsapp_from]):

            def mock_whatsapp(to, message):
                import uuid
                import json
                from datetime import datetime, timezone

                message_id = f"whatsapp_{uuid.uuid4().hex[:16]}"

                # Save message to workspace
                whatsapp_data = {
                    "message_id": message_id,
                    "to": to,
                    "message": message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "mode": "test",
                }

                workspace = Path("/tmp/v-os-workspace")
                workspace.mkdir(parents=True, exist_ok=True)
                with open(workspace / f"whatsapp_{message_id}.json", "w") as f:
                    json.dump(whatsapp_data, f, indent=2)

                return {
                    "id": message_id,
                    "mode": "test",
                    "saved_to": str(workspace / f"whatsapp_{message_id}.json"),
                }

            return mock_whatsapp

        # Production mode: Twilio WhatsApp
        try:
            from twilio.rest import Client

            def send_whatsapp(to, message):
                client = Client(twilio_account_sid, twilio_auth_token)

                # Ensure 'whatsapp:' prefix
                to_number = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
                from_number = (
                    twilio_whatsapp_from
                    if twilio_whatsapp_from.startswith("whatsapp:")
                    else f"whatsapp:{twilio_whatsapp_from}"
                )

                result = client.messages.create(
                    body=message, from_=from_number, to=to_number
                )

                return {"id": result.sid, "mode": "production"}

            return send_whatsapp

        except ImportError:
            # Fallback to mock
            def mock_whatsapp(to, message):
                import uuid

                return {
                    "id": f"whatsapp_{uuid.uuid4().hex[:16]}",
                    "mode": "mock_fallback",
                    "note": "Install twilio package for production: pip install twilio",
                }

            return mock_whatsapp


# Global instance for code execution
tools = ToolsAPI()


# Convenience functions for easier use in code
def web_search(query: str) -> Dict[str, Any]:
    """Search the web."""
    return tools.web_search(query)


def calculator(expression: str) -> Dict[str, Any]:
    """Calculate mathematical expression."""
    return tools.calculator(expression)


def file_analyzer(content: str, analysis_type: str = "summary") -> Dict[str, Any]:
    """Analyze text content."""
    return tools.file_analyzer(content, analysis_type)


def save_data(filename: str, data: Any, format: str = "json") -> str:
    """Save data to persistent workspace."""
    return tools.save_to_workspace(filename, data, format)


def load_data(filename: str, format: str = "json") -> Any:
    """Load data from persistent workspace."""
    return tools.load_from_workspace(filename, format)


def list_files() -> list:
    """List all files in workspace."""
    return tools.list_workspace_files()


def create_payment_session(
    amount: float,
    currency: str = "usd",
    description: str = "",
    customer_email: str = None,
) -> Dict[str, Any]:
    """
    Create Stripe payment session (PRD Section 4.6).

    Args:
        amount: Amount in currency units (e.g., 50.00)
        currency: Currency code (default: usd)
        description: Payment description
        customer_email: Optional customer email

    Returns:
        Dict with 'payment_url', 'session_id', 'success'
    """
    return tools.create_payment_session(amount, currency, description, customer_email)


def send_email(
    to: str, subject: str, body: str, from_email: str = None
) -> Dict[str, Any]:
    """
    Send email via Gmail (PRD Section 1.6).

    Args:
        to: Recipient email
        subject: Email subject
        body: Email body
        from_email: Optional sender email

    Returns:
        Dict with 'message_id', 'success'
    """
    return tools.send_email(to, subject, body, from_email)


def send_whatsapp(to: str, message: str) -> Dict[str, Any]:
    """
    Send WhatsApp message (PRD Section 1.6).

    Args:
        to: Recipient phone number (+1234567890)
        message: Message text

    Returns:
        Dict with 'message_id', 'success'
    """
    return tools.send_whatsapp(to, message)

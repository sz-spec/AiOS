"""
Integrations Module
====================
Third-party integrations for AI App Builder.

Based on community recommendations from:
- r/vbuilder
- r/vibecoding
- r/nocode
- r/stripe
- r/SaaS

Available Integrations:
- GitHub Sync: Auto-push generated code to GitHub
- Convex: Reactive backend database
- JWT Auth: Token-based authentication
- Socket.io: Real-time bi-directional communication
- GraphQL: Efficient API queries (alternative to REST)
- Stripe: Payment processing (subscriptions + credits)
- Analytics: PostHog + Mixpanel user tracking
- Email: Resend transactional emails
- Monitoring: Sentry error tracking
"""

from .github_sync import (
    GitHubSyncService,
    GitHubFile,
    SyncResult,
    sync_project_to_github,
)

from .auth import (
    AuthService,
    JWTService,
    UserStore,
    User,
    UserRole,
    TokenResponse,
    hash_password,
    verify_password,
    get_auth_service,
)

from .realtime import (
    RealtimeServer,
    get_realtime_server,
    add_realtime_to_app,
)

from .graphql_api import (
    schema as graphql_schema,
    get_graphql_router,
)

from .stripe_service import (
    StripeService,
    get_stripe_service,
    PlanType,
    BillingInterval,
    SubscriptionStatus,
    CheckoutRequest,
    TokenPurchaseRequest,
)

from .analytics import (
    AnalyticsService,
    get_analytics,
    shutdown_analytics,
    Events as AnalyticsEvents,
    EventCategory,
    GA4Client,
    AzureLogAnalyticsClient,
)

from .email_service import (
    EmailService,
    get_email_service,
    shutdown_email_service,
    EmailType,
    EmailEvent,
    EmailWebhookHandler,
    EmailAlertService,
)

from .monitoring import (
    init_sentry,
    capture_exception,
    capture_message,
    set_user as sentry_set_user,
    set_tag as sentry_set_tag,
    add_breadcrumb,
    track_errors,
    track_performance,
    add_sentry_to_fastapi,
    AlertIntegration,
    KQLQueries,
)

from .templates import (
    TemplateService,
    get_template_service,
    Template,
    TemplateCategory,
    TemplateFramework,
    TemplateDifficulty,
    TemplateFile,
    TEMPLATES,
)

from .teams import (
    TeamService,
    get_team_service,
    init_team_service,
    Team,
    TeamMember,
    TeamInvite,
    TeamRole,
    ProjectPermission,
    ProjectShare,
    Activity,
    ActivityType,
    PermissionChecker,
)

__all__ = [
    # GitHub
    "GitHubSyncService",
    "GitHubFile",
    "SyncResult",
    "sync_project_to_github",
    # Auth
    "AuthService",
    "JWTService",
    "UserStore",
    "User",
    "UserRole",
    "TokenResponse",
    "hash_password",
    "verify_password",
    "get_auth_service",
    # Realtime
    "RealtimeServer",
    "get_realtime_server",
    "add_realtime_to_app",
    # GraphQL
    "graphql_schema",
    "get_graphql_router",
    # Stripe
    "StripeService",
    "get_stripe_service",
    "PlanType",
    "BillingInterval",
    "SubscriptionStatus",
    "CheckoutRequest",
    "TokenPurchaseRequest",
    # Analytics
    "AnalyticsService",
    "get_analytics",
    "shutdown_analytics",
    "AnalyticsEvents",
    "EventCategory",
    "GA4Client",
    "AzureLogAnalyticsClient",
    # Email
    "EmailService",
    "get_email_service",
    "shutdown_email_service",
    "EmailType",
    "EmailEvent",
    "EmailWebhookHandler",
    "EmailAlertService",
    # Monitoring
    "init_sentry",
    "capture_exception",
    "capture_message",
    "sentry_set_user",
    "sentry_set_tag",
    "add_breadcrumb",
    "track_errors",
    "track_performance",
    "add_sentry_to_fastapi",
    "AlertIntegration",
    "KQLQueries",
    # Templates
    "TemplateService",
    "get_template_service",
    "Template",
    "TemplateCategory",
    "TemplateFramework",
    "TemplateDifficulty",
    "TemplateFile",
    "TEMPLATES",
    # Teams
    "TeamService",
    "get_team_service",
    "init_team_service",
    "Team",
    "TeamMember",
    "TeamInvite",
    "TeamRole",
    "ProjectPermission",
    "ProjectShare",
    "Activity",
    "ActivityType",
    "PermissionChecker",
]

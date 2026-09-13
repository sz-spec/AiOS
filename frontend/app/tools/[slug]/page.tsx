'use client';

import { useState, useEffect, useCallback } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useSettings } from '@/hooks/useSettings';
import { useConfirm } from '@/hooks/useConfirm';

interface ToolConfig {
  name: string;
  description: string;
  icon: string;
  color: string;
  connectLabel: string;
  connectDescription: string;
  fields: { key: string; label: string; placeholder: string; type?: string; description?: string }[];
  features: string[];
}

const TOOL_CONFIGS: Record<string, ToolConfig> = {
  github: {
    name: 'GitHub',
    description: 'Sync repositories, enable AI-powered code assistance, and automate workflows',
    icon: 'GH',
    color: '#24292e',
    connectLabel: 'Connect GitHub',
    connectDescription: 'Connect your GitHub account to automatically create repositories, sync code, and enable AI-powered code assistance.',
    fields: [
      { key: 'token', label: 'Personal Access Token', placeholder: 'ghp_xxxxxxxxxxxxxxxxxxxx', type: 'password', description: 'Create at github.com/settings/tokens with repo scope' },
    ],
    features: ['Repository creation', 'Code sync & push', 'AI-powered code review', 'Automated workflows'],
  },
  'google-cloud': {
    name: 'Google Cloud',
    description: 'Cloud infrastructure, storage, compute, and AI/ML services',
    icon: 'GC',
    color: '#4285F4',
    connectLabel: 'Connect Google Cloud',
    connectDescription: 'Link your Google Cloud project to enable infrastructure management, Cloud Storage, BigQuery, and Vertex AI integrations.',
    fields: [
      { key: 'project_id', label: 'Project ID', placeholder: 'my-gcp-project-123' },
      { key: 'service_account_key', label: 'Service Account Key (JSON)', placeholder: 'Paste your service account key JSON...', type: 'textarea', description: 'Download from GCP Console > IAM > Service Accounts' },
      { key: 'region', label: 'Default Region', placeholder: 'us-central1' },
    ],
    features: ['Cloud Storage file sync', 'BigQuery data queries', 'Vertex AI model access', 'Cloud Functions deployment'],
  },
  tavily: {
    name: 'Tavily Search',
    description: 'AI-optimized web search API for real-time information retrieval',
    icon: 'TV',
    color: '#6366F1',
    connectLabel: 'Connect Tavily',
    connectDescription: 'Add your Tavily API key to enable AI-powered web search for agents and workflows.',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'tvly-...', type: 'password', description: 'Get your key at tavily.com/dashboard' },
      { key: 'search_depth', label: 'Search Depth', placeholder: 'basic' },
      { key: 'max_results', label: 'Max Results', placeholder: '5' },
    ],
    features: ['Real-time web search', 'AI-optimized results', 'Content extraction', 'Domain filtering'],
  },
  greenapi: {
    name: 'GreenAPI',
    description: 'WhatsApp messaging integration for notifications and customer communication',
    icon: 'WA',
    color: '#25D366',
    connectLabel: 'Connect GreenAPI',
    connectDescription: 'Connect your GreenAPI account to send and receive WhatsApp messages from workflows and agents.',
    fields: [
      { key: 'instance_id', label: 'Instance ID', placeholder: '1234567890' },
      { key: 'api_token', label: 'API Token', placeholder: 'Enter your GreenAPI token...', type: 'password', description: 'Get credentials at green-api.com' },
      { key: 'webhook_url', label: 'Webhook URL (optional)', placeholder: 'https://your-domain.com/api/webhooks/greenapi' },
    ],
    features: ['Send WhatsApp messages', 'Receive incoming messages', 'Media attachments', 'Group messaging'],
  },
  elevenlabs: {
    name: 'ElevenLabs',
    description: 'AI voice synthesis and text-to-speech for natural voice interactions',
    icon: 'EL',
    color: '#000000',
    connectLabel: 'Connect ElevenLabs',
    connectDescription: 'Add your ElevenLabs API key to enable AI voice synthesis in your agents and workflows.',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'Enter your ElevenLabs API key...', type: 'password', description: 'Get your key at elevenlabs.io/settings' },
      { key: 'default_voice', label: 'Default Voice ID', placeholder: 'pNInz6obpgDQGcFmaJgB' },
      { key: 'model', label: 'Model', placeholder: 'eleven_multilingual_v2' },
    ],
    features: ['Text-to-speech generation', 'Voice cloning', 'Multilingual support', 'Streaming audio'],
  },
  n8n: {
    name: 'n8n / Make',
    description: 'Workflow automation and integration with hundreds of external services',
    icon: 'N8',
    color: '#FF6D5A',
    connectLabel: 'Connect n8n',
    connectDescription: 'Link your n8n or Make instance to trigger automations from VOS3 agents and workflows.',
    fields: [
      { key: 'instance_url', label: 'Instance URL', placeholder: 'https://your-n8n.example.com' },
      { key: 'api_key', label: 'API Key', placeholder: 'Enter your n8n API key...', type: 'password' },
      { key: 'webhook_path', label: 'Webhook Base Path', placeholder: '/webhook/' },
    ],
    features: ['Trigger n8n workflows', 'Receive webhook events', 'Pass data between systems', 'Error handling & retries'],
  },
  vercel: {
    name: 'Vercel',
    description: 'Deploy, preview, and scale frontend applications with zero configuration',
    icon: 'VC',
    color: '#000000',
    connectLabel: 'Connect Vercel',
    connectDescription: 'Link your Vercel account to deploy and manage applications directly from VOS3.',
    fields: [
      { key: 'api_token', label: 'API Token', placeholder: 'Enter your Vercel token...', type: 'password', description: 'Generate at vercel.com/account/tokens' },
      { key: 'team_id', label: 'Team ID (optional)', placeholder: 'team_...' },
      { key: 'project_id', label: 'Default Project', placeholder: 'prj_...' },
    ],
    features: ['One-click deployments', 'Preview URLs per branch', 'Environment variables', 'Deployment logs'],
  },
  stripe: {
    name: 'Stripe',
    description: 'Payment processing, subscriptions, and billing management',
    icon: 'ST',
    color: '#635BFF',
    connectLabel: 'Connect Stripe',
    connectDescription: 'Add your Stripe API keys to enable payment processing and billing within workflows.',
    fields: [
      { key: 'secret_key', label: 'Secret Key', placeholder: 'sk_live_...', type: 'password', description: 'Find at dashboard.stripe.com/apikeys' },
      { key: 'publishable_key', label: 'Publishable Key', placeholder: 'pk_live_...' },
      { key: 'webhook_secret', label: 'Webhook Secret', placeholder: 'whsec_...', type: 'password' },
    ],
    features: ['Payment processing', 'Subscription management', 'Invoice generation', 'Webhook events'],
  },
  twilio: {
    name: 'Twilio',
    description: 'SMS, voice calls, and programmable messaging for agent communication',
    icon: 'TW',
    color: '#F22F46',
    connectLabel: 'Connect Twilio',
    connectDescription: 'Add your Twilio credentials to enable SMS, voice calls, and messaging from agents and workflows.',
    fields: [
      { key: 'account_sid', label: 'Account SID', placeholder: 'AC...', description: 'Find at console.twilio.com' },
      { key: 'auth_token', label: 'Auth Token', placeholder: 'Enter your auth token...', type: 'password' },
      { key: 'phone_number', label: 'Phone Number', placeholder: '+1234567890', description: 'Your Twilio phone number' },
    ],
    features: ['Send & receive SMS', 'Voice calls', 'WhatsApp messaging', 'Programmable conversations'],
  },
  slack: {
    name: 'Slack',
    description: 'Team messaging, notifications, and interactive bot commands',
    icon: 'SL',
    color: '#4A154B',
    connectLabel: 'Connect Slack',
    connectDescription: 'Install the VOS3 Slack app to send notifications, receive commands, and interact with agents from Slack.',
    fields: [
      { key: 'bot_token', label: 'Bot Token', placeholder: 'xoxb-...', type: 'password', description: 'From api.slack.com/apps > OAuth & Permissions' },
      { key: 'signing_secret', label: 'Signing Secret', placeholder: 'Enter signing secret...', type: 'password' },
      { key: 'default_channel', label: 'Default Channel', placeholder: '#general' },
    ],
    features: ['Send messages & notifications', 'Slash commands', 'Interactive buttons', 'Thread conversations'],
  },
  sendgrid: {
    name: 'SendGrid',
    description: 'Transactional and marketing email delivery at scale',
    icon: 'SG',
    color: '#1A82E2',
    connectLabel: 'Connect SendGrid',
    connectDescription: 'Add your SendGrid API key to send transactional emails from agents and workflows.',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'SG...', type: 'password', description: 'Create at app.sendgrid.com/settings/api_keys' },
      { key: 'from_email', label: 'Default From Email', placeholder: 'noreply@yourdomain.com' },
      { key: 'from_name', label: 'Default From Name', placeholder: 'VOS3' },
    ],
    features: ['Transactional emails', 'Email templates', 'Delivery tracking', 'Bounce handling'],
  },
  notion: {
    name: 'Notion',
    description: 'Knowledge base, documentation, and structured data for agent context',
    icon: 'NT',
    color: '#000000',
    connectLabel: 'Connect Notion',
    connectDescription: 'Connect your Notion workspace to give agents access to pages, databases, and knowledge bases.',
    fields: [
      { key: 'api_key', label: 'Integration Token', placeholder: 'ntn_...', type: 'password', description: 'Create at notion.so/my-integrations' },
      { key: 'root_page_id', label: 'Root Page ID (optional)', placeholder: 'abc123def456' },
    ],
    features: ['Read pages & databases', 'Create & update content', 'Search workspace', 'Agent knowledge base'],
  },
  linear: {
    name: 'Linear',
    description: 'Issue tracking and project management with agent-driven workflows',
    icon: 'LN',
    color: '#5E6AD2',
    connectLabel: 'Connect Linear',
    connectDescription: 'Link your Linear workspace to create issues, track progress, and automate project workflows.',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'lin_api_...', type: 'password', description: 'Generate at linear.app/settings/api' },
      { key: 'team_id', label: 'Default Team ID (optional)', placeholder: 'TEAM-...' },
    ],
    features: ['Create & update issues', 'Project tracking', 'Cycle management', 'Webhook notifications'],
  },
  pinecone: {
    name: 'Pinecone',
    description: 'Vector database for semantic search, RAG, and long-term agent memory',
    icon: 'PC',
    color: '#000000',
    connectLabel: 'Connect Pinecone',
    connectDescription: 'Add your Pinecone credentials to enable vector search, RAG pipelines, and persistent agent memory.',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'Enter your Pinecone API key...', type: 'password', description: 'From app.pinecone.io > API Keys' },
      { key: 'environment', label: 'Environment', placeholder: 'us-east-1-aws' },
      { key: 'index_name', label: 'Default Index', placeholder: 'vos3-memory' },
    ],
    features: ['Vector similarity search', 'RAG retrieval', 'Agent long-term memory', 'Namespace isolation'],
  },
  browserbase: {
    name: 'Browserbase',
    description: 'Headless browser infrastructure for web browsing, scraping, and interaction',
    icon: 'BB',
    color: '#FF6B00',
    connectLabel: 'Connect Browserbase',
    connectDescription: 'Add your Browserbase API key to let agents browse the web, fill forms, and extract content.',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'bb_live_...', type: 'password', description: 'From browserbase.com/settings' },
      { key: 'project_id', label: 'Project ID', placeholder: 'Enter project ID...' },
    ],
    features: ['Headless web browsing', 'Page interaction & forms', 'Screenshot capture', 'Content extraction'],
  },
  e2b: {
    name: 'E2B',
    description: 'Secure cloud sandboxes for AI-generated code execution',
    icon: 'E2',
    color: '#FF8800',
    connectLabel: 'Connect E2B',
    connectDescription: 'Add your E2B API key to give agents secure sandboxes for running code, installing packages, and file operations.',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'e2b_...', type: 'password', description: 'From e2b.dev/dashboard' },
      { key: 'template', label: 'Default Template', placeholder: 'base' },
      { key: 'timeout', label: 'Sandbox Timeout (seconds)', placeholder: '300' },
    ],
    features: ['Secure code execution', 'Multi-language support', 'File system access', 'Package installation'],
  },
  langfuse: {
    name: 'Langfuse',
    description: 'LLM observability — traces, evaluations, prompt management, and cost tracking',
    icon: 'LF',
    color: '#6D28D9',
    connectLabel: 'Connect Langfuse',
    connectDescription: 'Link your Langfuse project to trace LLM calls, track costs, manage prompts, and run evaluations.',
    fields: [
      { key: 'public_key', label: 'Public Key', placeholder: 'pk-lf-...', description: 'From cloud.langfuse.com > Settings' },
      { key: 'secret_key', label: 'Secret Key', placeholder: 'sk-lf-...', type: 'password' },
      { key: 'host', label: 'Host URL', placeholder: 'https://cloud.langfuse.com' },
    ],
    features: ['LLM call tracing', 'Cost tracking', 'Prompt versioning', 'Evaluation scoring'],
  },
  firecrawl: {
    name: 'Firecrawl',
    description: 'Web crawling and scraping API optimized for LLM-ready content extraction',
    icon: 'FC',
    color: '#FF4F00',
    connectLabel: 'Connect Firecrawl',
    connectDescription: 'Add your Firecrawl API key to crawl websites and extract clean, structured content for agents.',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'fc-...', type: 'password', description: 'From firecrawl.dev/dashboard' },
      { key: 'max_pages', label: 'Max Pages per Crawl', placeholder: '100' },
    ],
    features: ['Website crawling', 'Clean markdown extraction', 'Structured data output', 'JavaScript rendering'],
  },
};

// ── GitHub-specific integration ──
function GitHubToolConfig({ config: toolConfig, router }: { config: ToolConfig; router: ReturnType<typeof useRouter> }) {
  const { config: settingsConfig, updateApiKey, deleteApiKey, getGitHubUser, isSaving } = useSettings();
  const [isEditing, setIsEditing] = useState(false);
  const [tokenInput, setTokenInput] = useState('');
  const [gitHubUser, setGitHubUser] = useState<{ login: string; name: string | null; avatar_url: string; html_url: string } | null>(null);
  const [isLoadingUser, setIsLoadingUser] = useState(false);
  const { confirm, ConfirmDialog: ConfirmMount } = useConfirm();

  const loadGitHubUser = useCallback(async () => {
    if (!settingsConfig?.github?.configured) { setGitHubUser(null); return; }
    setIsLoadingUser(true);
    const result = await getGitHubUser();
    if (result?.user) setGitHubUser(result.user);
    setIsLoadingUser(false);
  }, [settingsConfig?.github?.configured, getGitHubUser]);

  useEffect(() => { loadGitHubUser(); }, [loadGitHubUser]);

  const isConnected = !!(settingsConfig?.github?.configured && gitHubUser);

  const handleSaveToken = async () => {
    if (!tokenInput.trim()) return;
    const success = await updateApiKey('github', tokenInput.trim());
    if (success) { setIsEditing(false); setTokenInput(''); await loadGitHubUser(); }
  };

  const handleDisconnect = async () => {
    const ok = await confirm({
      title: 'Remove GitHub token?',
      description: 'You will lose access to repository sync and AI code assistance until you reconnect.',
      confirmLabel: 'Remove',
      destructive: true,
    });
    if (ok) {
      await deleteApiKey('github');
      setGitHubUser(null);
    }
  };

  return (
    <div style={{ padding: '24px', maxWidth: '1000px', margin: '0 auto' }}>
      <ToolBackLink router={router} />
      <ToolHeader config={toolConfig} connected={isConnected} />

      {!isConnected && !isEditing ? (
        <ToolConnectPrompt config={toolConfig} onConnect={() => setIsEditing(true)} />
      ) : (
        <>
          {isConnected && (
            <div style={{ padding: '14px 16px', backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-light)', marginBottom: '24px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#22c55e' }} />
                <span style={{ fontSize: '14px' }}>Connected to GitHub</span>
              </div>
              <button onClick={handleDisconnect} disabled={isSaving} style={{ padding: '6px 14px', backgroundColor: 'transparent', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', color: 'var(--text-secondary)', fontSize: '13px', cursor: 'pointer' }}>
                Disconnect
              </button>
            </div>
          )}

          {/* Connected user info */}
          {isConnected && gitHubUser && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '16px 20px', backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-light)', marginBottom: '24px' }}>
              <img src={gitHubUser.avatar_url} alt={gitHubUser.login} style={{ width: '48px', height: '48px', borderRadius: '50%' }} />
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600, fontSize: '15px' }}>{gitHubUser.name || gitHubUser.login}</div>
                <a href={gitHubUser.html_url} target="_blank" rel="noopener noreferrer" style={{ fontSize: '13px', color: 'var(--text-secondary)', textDecoration: 'none' }}>
                  @{gitHubUser.login}
                </a>
              </div>
              <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontFamily: 'monospace' }}>
                {settingsConfig?.github?.masked_key}
              </div>
            </div>
          )}

          {/* Token input */}
          <div style={{ backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-light)', padding: '24px', marginBottom: '24px' }}>
            <h3 style={{ margin: '0 0 20px', fontSize: '16px', fontWeight: 600 }}>
              {isConnected ? 'Update Token' : 'Connect GitHub'}
            </h3>
            <div style={{ marginBottom: '12px', padding: '12px 16px', backgroundColor: 'var(--bg-primary)', borderRadius: 'var(--radius-md)', fontSize: '13px', color: 'var(--text-secondary)' }}>
              Create a Personal Access Token with <strong>repo</strong> scope:
              <a href="https://github.com/settings/tokens/new?description=VOS3%20AI%20Builder&scopes=repo" target="_blank" rel="noopener noreferrer" style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', color: 'var(--accent)', textDecoration: 'none', fontWeight: 500, marginLeft: '8px' }}>
                Create Token on GitHub
              </a>
            </div>
            {isEditing || !isConnected ? (
              <div style={{ display: 'flex', gap: '8px' }}>
                <input type="password" value={tokenInput} onChange={(e) => setTokenInput(e.target.value)} placeholder="ghp_xxxxxxxxxxxxxxxxxxxx" autoFocus style={{ flex: 1, padding: '10px 14px', backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', fontSize: '13px', fontFamily: 'monospace', outline: 'none' }} />
                <button onClick={handleSaveToken} disabled={isSaving || !tokenInput.trim()} style={{ padding: '10px 20px', backgroundColor: toolConfig.color, borderRadius: 'var(--radius-md)', border: 'none', color: 'white', fontSize: '13px', fontWeight: 500, cursor: isSaving || !tokenInput.trim() ? 'not-allowed' : 'pointer', opacity: isSaving || !tokenInput.trim() ? 0.6 : 1 }}>
                  {isSaving ? 'Saving...' : 'Save Token'}
                </button>
                {isConnected && (
                  <button onClick={() => setIsEditing(false)} style={{ padding: '10px 16px', backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', fontSize: '13px', cursor: 'pointer' }}>
                    Cancel
                  </button>
                )}
              </div>
            ) : (
              <button onClick={() => setIsEditing(true)} style={{ padding: '10px 20px', backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', fontSize: '13px', fontWeight: 500, cursor: 'pointer' }}>
                Update Token
              </button>
            )}
          </div>

          <ToolFeatures config={toolConfig} />
        </>
      )}
      <ConfirmMount />
    </div>
  );
}

// ── Tavily-specific integration ──
function TavilyToolConfig({ config: toolConfig, router }: { config: ToolConfig; router: ReturnType<typeof useRouter> }) {
  const { config: settingsConfig, updateApiKey, deleteApiKey, isSaving } = useSettings();
  const [isEditing, setIsEditing] = useState(false);
  const [keyInput, setKeyInput] = useState('');
  const { confirm, ConfirmDialog: ConfirmMount } = useConfirm();

  const isConnected = !!settingsConfig?.tavily?.configured;

  const handleSaveKey = async () => {
    if (!keyInput.trim()) return;
    const success = await updateApiKey('tavily', keyInput.trim());
    if (success) { setIsEditing(false); setKeyInput(''); }
  };

  const handleDisconnect = async () => {
    const ok = await confirm({
      title: 'Remove Tavily API key?',
      description: 'Search functionality will stop working until you reconnect.',
      confirmLabel: 'Remove',
      destructive: true,
    });
    if (ok) {
      await deleteApiKey('tavily');
    }
  };

  return (
    <div style={{ padding: '24px', maxWidth: '1000px', margin: '0 auto' }}>
      <ToolBackLink router={router} />
      <ToolHeader config={toolConfig} connected={isConnected} />

      {!isConnected && !isEditing ? (
        <ToolConnectPrompt config={toolConfig} onConnect={() => setIsEditing(true)} />
      ) : (
        <>
          {isConnected && (
            <div style={{ padding: '14px 16px', backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-light)', marginBottom: '24px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <div style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#22c55e' }} />
                <span style={{ fontSize: '14px' }}>Connected to Tavily</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontFamily: 'monospace' }}>
                  {settingsConfig?.tavily?.masked_key}
                </span>
                <button onClick={handleDisconnect} disabled={isSaving} style={{ padding: '6px 14px', backgroundColor: 'transparent', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', color: 'var(--text-secondary)', fontSize: '13px', cursor: 'pointer' }}>
                  Disconnect
                </button>
              </div>
            </div>
          )}

          {/* API key input */}
          <div style={{ backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-light)', padding: '24px', marginBottom: '24px' }}>
            <h3 style={{ margin: '0 0 20px', fontSize: '16px', fontWeight: 600 }}>
              {isConnected ? 'Update API Key' : 'Connect Tavily'}
            </h3>
            <div style={{ marginBottom: '12px', padding: '12px 16px', backgroundColor: 'var(--bg-primary)', borderRadius: 'var(--radius-md)', fontSize: '13px', color: 'var(--text-secondary)' }}>
              Get your API key from{' '}
              <a href="https://tavily.com/#api" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent)', textDecoration: 'none', fontWeight: 500 }}>
                tavily.com
              </a>
            </div>
            {isEditing || !isConnected ? (
              <div style={{ display: 'flex', gap: '8px' }}>
                <input type="password" value={keyInput} onChange={(e) => setKeyInput(e.target.value)} placeholder="tvly-xxxxxxxxxxxxxxxxxxxx" autoFocus style={{ flex: 1, padding: '10px 14px', backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', fontSize: '13px', fontFamily: 'monospace', outline: 'none' }} />
                <button onClick={handleSaveKey} disabled={isSaving || !keyInput.trim()} style={{ padding: '10px 20px', backgroundColor: toolConfig.color, borderRadius: 'var(--radius-md)', border: 'none', color: 'white', fontSize: '13px', fontWeight: 500, cursor: isSaving || !keyInput.trim() ? 'not-allowed' : 'pointer', opacity: isSaving || !keyInput.trim() ? 0.6 : 1 }}>
                  {isSaving ? 'Saving...' : 'Save Key'}
                </button>
                {isConnected && (
                  <button onClick={() => setIsEditing(false)} style={{ padding: '10px 16px', backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', fontSize: '13px', cursor: 'pointer' }}>
                    Cancel
                  </button>
                )}
              </div>
            ) : (
              <button onClick={() => setIsEditing(true)} style={{ padding: '10px 20px', backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', fontSize: '13px', fontWeight: 500, cursor: 'pointer' }}>
                Update Key
              </button>
            )}
          </div>

          <ToolFeatures config={toolConfig} />
        </>
      )}
      <ConfirmMount />
    </div>
  );
}

// ── Shared sub-components ──
function ToolBackLink({ router }: { router: ReturnType<typeof useRouter> }) {
  return (
    <button onClick={() => router.push('/tools')} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '0', marginBottom: '20px', backgroundColor: 'transparent', border: 'none', color: 'var(--text-secondary)', fontSize: '13px', cursor: 'pointer' }}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
      All Tools
    </button>
  );
}

function ToolHeader({ config, connected }: { config: ToolConfig; connected: boolean }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '24px' }}>
      <div style={{ width: '56px', height: '56px', backgroundColor: config.color, borderRadius: 'var(--radius-md)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '18px', fontWeight: 700, color: 'white', letterSpacing: '-0.5px' }}>
        {config.icon}
      </div>
      <div style={{ flex: 1 }}>
        <h1 style={{ fontSize: '24px', fontWeight: 600, marginBottom: '4px' }}>{config.name}</h1>
        <p style={{ color: 'var(--text-secondary)', margin: 0, fontSize: '14px' }}>{config.description}</p>
      </div>
      {connected && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <div style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#22c55e' }} />
          <span style={{ fontSize: '13px', color: '#22c55e', fontWeight: 500 }}>Connected</span>
        </div>
      )}
    </div>
  );
}

function ToolConnectPrompt({ config, onConnect }: { config: ToolConfig; onConnect: () => void }) {
  return (
    <div style={{ padding: '48px', textAlign: 'center', backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-light)' }}>
      <div style={{ width: '80px', height: '80px', backgroundColor: config.color, borderRadius: '16px', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '28px', fontWeight: 700, color: 'white', margin: '0 auto 20px', letterSpacing: '-0.5px' }}>
        {config.icon}
      </div>
      <h2 style={{ marginBottom: '8px', fontSize: '20px', fontWeight: 600 }}>{config.connectLabel}</h2>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '24px', maxWidth: '500px', margin: '0 auto 24px', fontSize: '14px', lineHeight: 1.6 }}>
        {config.connectDescription}
      </p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center', marginBottom: '28px' }}>
        {config.features.map((feature) => (
          <span key={feature} style={{ padding: '4px 12px', backgroundColor: 'var(--bg-primary)', borderRadius: 'var(--radius-full)', fontSize: '12px', color: 'var(--text-secondary)', border: '1px solid var(--border-light)' }}>
            {feature}
          </span>
        ))}
      </div>
      <button onClick={onConnect} style={{ padding: '12px 28px', backgroundColor: config.color, color: 'white', borderRadius: 'var(--radius-md)', fontWeight: 500, fontSize: '14px', display: 'inline-flex', alignItems: 'center', gap: '8px', border: 'none', cursor: 'pointer' }}>
        {config.connectLabel}
      </button>
    </div>
  );
}

function ToolFeatures({ config }: { config: ToolConfig }) {
  return (
    <div style={{ backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-light)', padding: '24px' }}>
      <h3 style={{ margin: '0 0 16px', fontSize: '16px', fontWeight: 600 }}>Features</h3>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
        {config.features.map((feature) => (
          <div key={feature} style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', color: 'var(--text-secondary)' }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12" /></svg>
            {feature}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ToolConfigPage() {
  const params = useParams();
  const router = useRouter();
  const slug = params.slug as string;
  const config = TOOL_CONFIGS[slug];

  const [connected, setConnected] = useState(false);
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [isSaving, setIsSaving] = useState(false);

  if (!config) {
    return (
      <div style={{ padding: '24px', maxWidth: '1000px', margin: '0 auto' }}>
        <div style={{ textAlign: 'center', padding: '48px', color: 'var(--text-secondary)' }}>
          <div style={{ fontSize: '48px', marginBottom: '16px', opacity: 0.4 }}>?</div>
          <h2 style={{ fontSize: '20px', fontWeight: 600, marginBottom: '8px', color: 'var(--text-primary)' }}>Tool not found</h2>
          <p style={{ marginBottom: '24px' }}>The tool &quot;{slug}&quot; doesn&apos;t exist or isn&apos;t available yet.</p>
          <button
            onClick={() => router.push('/tools')}
            style={{
              padding: '10px 20px',
              backgroundColor: 'var(--accent)',
              color: 'white',
              border: 'none',
              borderRadius: 'var(--radius-md)',
              fontSize: '14px',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            Back to Tools
          </button>
        </div>
      </div>
    );
  }

  // Route to specialized components for tools with real API integration
  if (slug === 'github') return <GitHubToolConfig config={config} router={router} />;
  if (slug === 'tavily') return <TavilyToolConfig config={config} router={router} />;

  const handleConnect = () => {
    setConnected(true);
  };

  const handleSave = async () => {
    setIsSaving(true);
    // Simulate API call
    await new Promise((r) => setTimeout(r, 800));
    setIsSaving(false);
  };

  const handleDisconnect = () => {
    setConnected(false);
    setFieldValues({});
  };

  return (
    <div style={{ padding: '24px', maxWidth: '1000px', margin: '0 auto' }}>
      <ToolBackLink router={router} />
      <ToolHeader config={config} connected={connected} />

      {!connected ? (
        <ToolConnectPrompt config={config} onConnect={handleConnect} />
      ) : (
        <>
          {/* Connection status bar */}
          <div style={{ padding: '14px 16px', backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-light)', marginBottom: '24px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <div style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#22c55e' }} />
              <span style={{ fontSize: '14px' }}>Connected to {config.name}</span>
            </div>
            <button onClick={handleDisconnect} style={{ padding: '6px 14px', backgroundColor: 'transparent', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', color: 'var(--text-secondary)', fontSize: '13px', cursor: 'pointer' }}>
              Disconnect
            </button>
          </div>

          {/* Configuration fields */}
          <div style={{ backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-light)', padding: '24px', marginBottom: '24px' }}>
            <h3 style={{ margin: '0 0 20px', fontSize: '16px', fontWeight: 600 }}>Configuration</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
              {config.fields.map((field) => (
                <div key={field.key}>
                  <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px', color: 'var(--text-primary)' }}>
                    {field.label}
                  </label>
                  {field.description && (
                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>
                      {field.description}
                    </div>
                  )}
                  {field.type === 'textarea' ? (
                    <textarea
                      value={fieldValues[field.key] || ''}
                      onChange={(e) => setFieldValues((prev) => ({ ...prev, [field.key]: e.target.value }))}
                      placeholder={field.placeholder}
                      rows={4}
                      style={{ width: '100%', padding: '10px 12px', backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', fontSize: '13px', outline: 'none', resize: 'vertical', fontFamily: "'SF Mono', 'Fira Code', Consolas, monospace" }}
                    />
                  ) : (
                    <input
                      type={field.type || 'text'}
                      value={fieldValues[field.key] || ''}
                      onChange={(e) => setFieldValues((prev) => ({ ...prev, [field.key]: e.target.value }))}
                      placeholder={field.placeholder}
                      style={{ width: '100%', padding: '10px 12px', backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', fontSize: '13px', outline: 'none' }}
                    />
                  )}
                </div>
              ))}
            </div>
            <div style={{ marginTop: '24px', display: 'flex', gap: '12px' }}>
              <button onClick={handleSave} disabled={isSaving} style={{ padding: '10px 24px', backgroundColor: config.color, color: 'white', border: 'none', borderRadius: 'var(--radius-md)', fontSize: '14px', fontWeight: 500, cursor: isSaving ? 'not-allowed' : 'pointer', opacity: isSaving ? 0.7 : 1 }}>
                {isSaving ? 'Saving...' : 'Save Configuration'}
              </button>
              <button onClick={() => router.push('/tools')} style={{ padding: '10px 24px', backgroundColor: 'transparent', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', fontSize: '14px', cursor: 'pointer' }}>
                Cancel
              </button>
            </div>
          </div>

          <ToolFeatures config={config} />
        </>
      )}
    </div>
  );
}

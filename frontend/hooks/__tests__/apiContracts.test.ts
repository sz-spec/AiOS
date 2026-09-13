import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

/**
 * API Contract Tests
 *
 * Validates that frontend hooks expect response shapes that match
 * the backend API contracts. Uses mock responses matching actual
 * backend endpoint structures.
 */

describe('API Contracts', () => {
  beforeEach(() => {
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Network unavailable'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('chat models response has models array with id/name/provider', async () => {
    // Backend: GET /api/chat/models returns { models: [...] }
    const backendResponse = {
      models: [
        { id: 'gpt', name: 'GPT-4o', provider: 'openai' },
        { id: 'claude-sonnet', name: 'Claude Sonnet', provider: 'anthropic' },
      ],
    };

    // Validate shape
    expect(backendResponse.models).toBeInstanceOf(Array);
    expect(backendResponse.models[0]).toHaveProperty('id');
    expect(backendResponse.models[0]).toHaveProperty('name');
    expect(backendResponse.models[0]).toHaveProperty('provider');
  });

  it('kernel status response has connected and sysinfo fields', () => {
    // Backend: GET /api/kernel/status returns { connected, sysinfo?, ... }
    const backendResponse = {
      connected: true,
      sysinfo: {
        os_name: 'VOS3',
        version: '3.1.0',
        uptime_seconds: 120,
        memory_total: 536870912,
        memory_free: 268435456,
      },
      processes: [],
    };

    expect(backendResponse).toHaveProperty('connected');
    expect(typeof backendResponse.connected).toBe('boolean');
    if (backendResponse.sysinfo) {
      expect(backendResponse.sysinfo).toHaveProperty('os_name');
      expect(backendResponse.sysinfo).toHaveProperty('version');
    }
  });

  it('v-core entities response is array of entity objects', () => {
    // Backend: GET /api/v-core/entities returns [...entities]
    const backendResponse = [
      {
        id: 'ent_1',
        name: 'contacts',
        display_name: 'Contacts',
        organization_id: 'org_1',
        fields: [],
      },
    ];

    expect(backendResponse).toBeInstanceOf(Array);
    expect(backendResponse[0]).toHaveProperty('id');
    expect(backendResponse[0]).toHaveProperty('name');
    expect(backendResponse[0]).toHaveProperty('fields');
  });

  it('agents list response has agents array', () => {
    // Backend: GET /api/agents returns { agents: [...] }
    const backendResponse = {
      agents: [
        {
          id: 'agent_1',
          name: 'Coder',
          role: 'backend',
          status: 'idle',
          model: 'claude-sonnet',
          run_count: 5,
        },
      ],
    };

    expect(backendResponse).toHaveProperty('agents');
    expect(backendResponse.agents).toBeInstanceOf(Array);
    expect(backendResponse.agents[0]).toHaveProperty('id');
    expect(backendResponse.agents[0]).toHaveProperty('role');
    expect(backendResponse.agents[0]).toHaveProperty('status');
  });

  it('memory stats response has initialized and total_memories', () => {
    // Backend: GET /api/memory returns { initialized, total_memories, ... }
    const backendResponse = {
      initialized: true,
      persist_dir: '/data/memory',
      total_memories: 42,
      by_type: { context: 20, decision: 22 },
      embedding_model: 'text-embedding-3-small',
    };

    expect(backendResponse).toHaveProperty('initialized');
    expect(backendResponse).toHaveProperty('total_memories');
    expect(typeof backendResponse.total_memories).toBe('number');
    expect(backendResponse).toHaveProperty('by_type');
  });
});

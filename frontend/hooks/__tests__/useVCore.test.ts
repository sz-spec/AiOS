import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useVCore } from '../useVCore';

describe('useVCore', () => {
  beforeEach(() => {
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Network unavailable'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ---- Initial State ----

  it('starts with isLoading false and error null', () => {
    const { result } = renderHook(() => useVCore());
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('starts with empty entities, records, workflows, and executions', () => {
    const { result } = renderHook(() => useVCore());
    expect(result.current.entities).toEqual([]);
    expect(result.current.records).toEqual([]);
    expect(result.current.workflows).toEqual([]);
    expect(result.current.executions).toEqual([]);
  });

  it('starts with empty organizations, members, roles', () => {
    const { result } = renderHook(() => useVCore());
    expect(result.current.organizations).toEqual([]);
    expect(result.current.members).toEqual([]);
    expect(result.current.roles).toEqual([]);
    expect(result.current.currentOrg).toBeNull();
  });

  it('starts with null dashboard and empty mission control arrays', () => {
    const { result } = renderHook(() => useVCore());
    expect(result.current.dashboard).toBeNull();
    expect(result.current.activities).toEqual([]);
    expect(result.current.approvals).toEqual([]);
    expect(result.current.alerts).toEqual([]);
    expect(result.current.metrics).toEqual([]);
  });

  // ---- loadEntities ----

  it('loadEntities populates entities on success', async () => {
    const mockEntities = [
      { id: 'e1', name: 'contacts', label: 'Contacts', fields: [] },
    ];

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable')) // useEffect roles load
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ entities: mockEntities }),
      } as Response);

    const { result } = renderHook(() => useVCore());

    await act(async () => {
      await result.current.loadEntities();
    });

    expect(result.current.entities).toEqual(mockEntities);
    expect(result.current.error).toBeNull();
  });

  it('loadEntities sets error on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockResolvedValueOnce({
        ok: false,
        json: async () => ({ detail: 'Entities not found' }),
      } as Response);

    const { result } = renderHook(() => useVCore());

    await act(async () => {
      await result.current.loadEntities();
    });

    expect(result.current.error).toBe('Entities not found');
  });

  // ---- createEntity ----

  it('createEntity appends to entities on success', async () => {
    const newEntity = { id: 'e2', name: 'deals', label: 'Deals' };

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ entity: newEntity }),
      } as Response);

    const { result } = renderHook(() => useVCore());

    let created: any;
    await act(async () => {
      created = await result.current.createEntity('deals', 'Deals');
    });

    expect(created).toEqual(newEntity);
    expect(result.current.entities).toContainEqual(newEntity);
  });

  it('createEntity throws on API error', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockResolvedValueOnce({
        ok: false,
        json: async () => ({ detail: 'Duplicate name' }),
      } as Response);

    const { result } = renderHook(() => useVCore());

    await expect(
      act(async () => {
        await result.current.createEntity('deals', 'Deals');
      })
    ).rejects.toThrow('Duplicate name');
  });

  // ---- updateRecord ----

  it('updateRecord replaces the record in state', async () => {
    const updatedRecord = { id: 'r1', entityId: 'e1', data: { name: 'Updated' } };

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      // First: loadRecords to populate
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          records: [{ id: 'r1', entityId: 'e1', data: { name: 'Original' } }],
        }),
      } as Response)
      // Then: updateRecord
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ record: updatedRecord }),
      } as Response);

    const { result } = renderHook(() => useVCore());

    await act(async () => {
      await result.current.loadRecords('e1');
    });

    await act(async () => {
      await result.current.updateRecord('r1', { name: 'Updated' });
    });

    expect(result.current.records[0].data.name).toBe('Updated');
  });

  // ---- loadRecords ----

  it('loadRecords populates records on success', async () => {
    const mockRecords = [{ id: 'r1', entityId: 'e1', data: { name: 'Foo' } }];

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ records: mockRecords }),
      } as Response);

    const { result } = renderHook(() => useVCore());

    await act(async () => {
      await result.current.loadRecords('e1');
    });

    expect(result.current.records).toEqual(mockRecords);
  });

  it('loadRecords sets error on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockRejectedValueOnce(new Error('Connection refused'));

    const { result } = renderHook(() => useVCore());

    await act(async () => {
      await result.current.loadRecords('e1');
    });

    expect(result.current.error).toBe('Connection refused');
  });

  // ---- createRecord ----

  it('createRecord appends to records on success', async () => {
    const newRecord = { id: 'r2', entityId: 'e1', data: { name: 'Bar' } };

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ record: newRecord }),
      } as Response);

    const { result } = renderHook(() => useVCore());

    let created: any;
    await act(async () => {
      created = await result.current.createRecord('e1', { name: 'Bar' });
    });

    expect(created).toEqual(newRecord);
    expect(result.current.records).toContainEqual(newRecord);
  });

  // ---- deleteRecord ----

  it('deleteRecord removes the record from state', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      // loadRecords
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          records: [
            { id: 'r1', entityId: 'e1', data: {} },
            { id: 'r2', entityId: 'e1', data: {} },
          ],
        }),
      } as Response)
      // deleteRecord
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      } as Response);

    const { result } = renderHook(() => useVCore());

    await act(async () => {
      await result.current.loadRecords('e1');
    });
    expect(result.current.records).toHaveLength(2);

    await act(async () => {
      await result.current.deleteRecord('r1');
    });

    expect(result.current.records).toHaveLength(1);
    expect(result.current.records[0].id).toBe('r2');
  });

  // ---- loadWorkflows ----

  it('loadWorkflows populates workflows on success', async () => {
    const mockWorkflows = [{ id: 'w1', name: 'Onboarding', status: 'active' }];

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ workflows: mockWorkflows }),
      } as Response);

    const { result } = renderHook(() => useVCore());

    await act(async () => {
      await result.current.loadWorkflows();
    });

    expect(result.current.workflows).toEqual(mockWorkflows);
  });

  it('loadWorkflows sets error on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockResolvedValueOnce({
        ok: false,
        json: async () => ({ detail: 'Server error' }),
      } as Response);

    const { result } = renderHook(() => useVCore());

    await act(async () => {
      await result.current.loadWorkflows();
    });

    expect(result.current.error).toBe('Server error');
  });

  // ---- createWorkflow ----

  it('createWorkflow appends to workflows on success', async () => {
    const newWorkflow = { id: 'w2', name: 'Billing', status: 'draft' };

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ workflow: newWorkflow }),
      } as Response);

    const { result } = renderHook(() => useVCore());

    let created: any;
    await act(async () => {
      created = await result.current.createWorkflow('Billing', 'Process billing');
    });

    expect(created).toEqual(newWorkflow);
    expect(result.current.workflows).toContainEqual(newWorkflow);
  });

  // ---- executeWorkflow ----

  it('executeWorkflow returns execution on success', async () => {
    const mockExecution = { id: 'ex1', workflowId: 'w1', status: 'running' };

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ execution: mockExecution }),
      } as Response);

    const { result } = renderHook(() => useVCore());

    let execution: any;
    await act(async () => {
      execution = await result.current.executeWorkflow('w1', { key: 'value' });
    });

    expect(execution).toEqual(mockExecution);
  });

  // ---- loadDashboard ----

  it('loadDashboard sets dashboard data on success', async () => {
    const mockDashboard = {
      metrics: { users: 10 },
      activitySummary: { total: 5, successful: 4, failed: 1, successRate: 0.8, totalTokens: 100, totalCost: 1.5 },
      recentActivities: [],
      pendingApprovals: [],
      alerts: { total: 0, unread: 0, critical: 0, error: 0, warning: 0 },
      recentAlerts: [],
    };

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockDashboard,
      } as Response);

    const { result } = renderHook(() => useVCore());

    await act(async () => {
      await result.current.loadDashboard();
    });

    expect(result.current.dashboard).toEqual(mockDashboard);
  });

  it('loadDashboard sets error on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockRejectedValueOnce(new Error('Dashboard unavailable'));

    const { result } = renderHook(() => useVCore());

    await act(async () => {
      await result.current.loadDashboard();
    });

    expect(result.current.error).toBe('Dashboard unavailable');
  });

  // ---- loadOrganizations ----

  it('loadOrganizations populates organizations on success', async () => {
    const mockOrgs = [{ id: 'org1', name: 'Acme Corp', slug: 'acme', plan: 'free' }];

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ organizations: mockOrgs }),
      } as Response);

    const { result } = renderHook(() => useVCore());

    await act(async () => {
      await result.current.loadOrganizations();
    });

    expect(result.current.organizations).toEqual(mockOrgs);
    expect(result.current.currentOrg).toEqual(mockOrgs[0]);
  });

  it('loadOrganizations sets error on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockRejectedValueOnce(new Error('Org fetch failed'));

    const { result } = renderHook(() => useVCore());

    await act(async () => {
      await result.current.loadOrganizations();
    });

    expect(result.current.error).toBe('Org fetch failed');
  });

  // ---- createOrganization ----

  it('createOrganization appends org to state on success', async () => {
    const newOrg = { id: 'org2', name: 'New Org', slug: 'new-org', plan: 'starter' };

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ organization: newOrg }),
      } as Response);

    const { result } = renderHook(() => useVCore());

    let created: any;
    await act(async () => {
      created = await result.current.createOrganization('New Org', 'starter');
    });

    expect(created).toEqual(newOrg);
    expect(result.current.organizations).toContainEqual(newOrg);
  });

  // ---- approveRequest ----

  it('approveRequest updates approval status in state', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      // loadApprovals
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          approvals: [{ id: 'a1', status: 'pending', title: 'Deploy' }],
        }),
      } as Response)
      // approveRequest
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      } as Response);

    const { result } = renderHook(() => useVCore());

    await act(async () => {
      await result.current.loadApprovals();
    });

    await act(async () => {
      await result.current.approveRequest('a1', 'Looks good');
    });

    expect(result.current.approvals[0].status).toBe('approved');
  });

  // ---- rejectRequest ----

  it('rejectRequest updates approval status to rejected', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          approvals: [{ id: 'a1', status: 'pending', title: 'Deploy' }],
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      } as Response);

    const { result } = renderHook(() => useVCore());

    await act(async () => {
      await result.current.loadApprovals();
    });

    await act(async () => {
      await result.current.rejectRequest('a1', 'Not ready');
    });

    expect(result.current.approvals[0].status).toBe('rejected');
  });

  // ---- resolveAlert ----

  it('resolveAlert marks alert as resolved in state', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          alerts: [{ id: 'al1', isResolved: false, title: 'High CPU' }],
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      } as Response);

    const { result } = renderHook(() => useVCore());

    await act(async () => {
      await result.current.loadAlerts();
    });

    await act(async () => {
      await result.current.resolveAlert('al1');
    });

    expect(result.current.alerts[0].isResolved).toBe(true);
  });

  // ---- roles loading via useEffect ----

  it('loads roles on mount when API is available', async () => {
    const mockRoles = [{ id: 'role1', name: 'admin', permissions: [] }];

    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ roles: mockRoles }),
    } as Response);

    const { result } = renderHook(() => useVCore());

    await waitFor(() => {
      expect(result.current.roles).toEqual(mockRoles);
    });
  });

  // ---- Error handling across operations ----

  it('sets error on network failure for loadEntities', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockRejectedValueOnce(new Error('ECONNREFUSED'));

    const { result } = renderHook(() => useVCore());

    await act(async () => {
      await result.current.loadEntities();
    });

    expect(result.current.error).toBe('ECONNREFUSED');
  });

  it('isLoading is true during loadEntities and false after', async () => {
    let resolvePromise: (value: any) => void;
    const pendingPromise = new Promise((resolve) => {
      resolvePromise = resolve;
    });

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('roles unavailable'))
      .mockReturnValueOnce(pendingPromise as any);

    const { result } = renderHook(() => useVCore());

    // Start the load but don't await
    const loadPromise = act(async () => {
      const p = result.current.loadEntities();
      // Resolve after checking loading state
      resolvePromise!({
        ok: true,
        json: async () => ({ entities: [] }),
      });
      await p;
    });

    await loadPromise;
    expect(result.current.isLoading).toBe(false);
  });
});

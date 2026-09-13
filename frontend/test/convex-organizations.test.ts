/// <reference types="vite/client" />
import { convexTest } from 'convex-test';
import { describe, expect, it } from 'vitest';
import schema from '../convex/schema';
import { api } from '../convex/_generated/api';

const modules = import.meta.glob('../convex/**/*.ts');

describe('organization identity integration', () => {
  it('resolves Clerk identity to a real user ID and enrolls its owner atomically', async () => {
    const t = convexTest(schema, modules);
    const ownerId = await t.run(ctx => ctx.db.insert('users', {
      clerkId: 'clerk-owner', email: 'owner@example.test',
    }));
    const owner = t.withIdentity({ subject: 'clerk-owner' });
    const id = await owner.mutation(api.organizations.create, {
      ownerId: 'clerk-owner', name: 'VOS workspace', slug: 'vos-workspace',
    });
    const organization = await owner.query(api.organizations.getById, { id });
    expect(organization?.ownerId).toBe(ownerId);
    const memberships = await owner.query(api.organizations.listMembers, { organizationId: id });
    expect(memberships).toHaveLength(1);
    expect(memberships[0]).toMatchObject({ userId: ownerId, role: 'owner' });
    expect(await owner.query(api.organizations.listByUser, { userId: ownerId })).toHaveLength(1);
    const outsider = t.withIdentity({ subject: 'clerk-outsider' });
    await expect(outsider.query(api.organizations.getById, { id })).rejects.toThrow();
    await expect(outsider.query(api.organizations.getBySlug, { slug: 'vos-workspace' })).rejects.toThrow();
  });

  it('rejects an impersonated owner and leaves no partial organization', async () => {
    const t = convexTest(schema, modules);
    await expect(t.withIdentity({ subject: 'attacker' }).mutation(api.organizations.create, {
      ownerId: 'victim', name: 'Forged', slug: 'forged',
    })).rejects.toThrow('identity mismatch');
    expect(await t.run(ctx => ctx.db.query('organizations').collect())).toEqual([]);
    expect(await t.run(ctx => ctx.db.query('members').collect())).toEqual([]);
  });
});

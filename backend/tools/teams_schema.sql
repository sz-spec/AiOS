-- =============================================================================
-- Team Collaboration Schema
-- =============================================================================
-- Run this in your PostgreSQL SQL editor

-- -----------------------------------------------------------------------------
-- Teams table
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS teams (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    description TEXT,
    logo_url TEXT,
    website TEXT,
    
    -- Limits (based on plan)
    max_members INTEGER DEFAULT 5,
    max_projects INTEGER DEFAULT 10,
    
    -- Counts (denormalized for performance)
    member_count INTEGER DEFAULT 0,
    project_count INTEGER DEFAULT 0,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast slug lookup
CREATE INDEX IF NOT EXISTS idx_teams_slug ON teams(slug);
CREATE INDEX IF NOT EXISTS idx_teams_owner ON teams(owner_id);

-- -----------------------------------------------------------------------------
-- Team members table
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS team_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    invited_by UUID REFERENCES auth.users(id),
    joined_at TIMESTAMPTZ DEFAULT NOW(),
    
    UNIQUE(team_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_team_members_team ON team_members(team_id);
CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members(user_id);

-- -----------------------------------------------------------------------------
-- Team invitations table
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS team_invites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'member', 'viewer')),
    invited_by UUID NOT NULL REFERENCES auth.users(id),
    token TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'declined', 'expired')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '7 days',
    accepted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_team_invites_token ON team_invites(token);
CREATE INDEX IF NOT EXISTS idx_team_invites_team ON team_invites(team_id);
CREATE INDEX IF NOT EXISTS idx_team_invites_email ON team_invites(email);

-- -----------------------------------------------------------------------------
-- Project shares table
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_shares (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    shared_by UUID NOT NULL REFERENCES auth.users(id),
    permission TEXT NOT NULL CHECK (permission IN ('owner', 'editor', 'commenter', 'viewer')),
    
    -- Share with team OR user (one must be set)
    team_id UUID REFERENCES teams(id) ON DELETE CASCADE,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Ensure at least one of team_id or user_id is set
    CONSTRAINT share_target CHECK (team_id IS NOT NULL OR user_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_project_shares_project ON project_shares(project_id);
CREATE INDEX IF NOT EXISTS idx_project_shares_team ON project_shares(team_id);
CREATE INDEX IF NOT EXISTS idx_project_shares_user ON project_shares(user_id);

-- -----------------------------------------------------------------------------
-- Team activity feed table
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS team_activities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id),
    activity_type TEXT NOT NULL,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    target_user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_team_activities_team ON team_activities(team_id);
CREATE INDEX IF NOT EXISTS idx_team_activities_created ON team_activities(created_at DESC);

-- -----------------------------------------------------------------------------
-- Functions
-- -----------------------------------------------------------------------------

-- Increment team member count
CREATE OR REPLACE FUNCTION increment_team_members(p_team_id UUID)
RETURNS void AS $$
BEGIN
    UPDATE teams 
    SET member_count = member_count + 1,
        updated_at = NOW()
    WHERE id = p_team_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Decrement team member count
CREATE OR REPLACE FUNCTION decrement_team_members(p_team_id UUID)
RETURNS void AS $$
BEGIN
    UPDATE teams 
    SET member_count = GREATEST(0, member_count - 1),
        updated_at = NOW()
    WHERE id = p_team_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Increment team project count
CREATE OR REPLACE FUNCTION increment_team_projects(p_team_id UUID)
RETURNS void AS $$
BEGIN
    UPDATE teams 
    SET project_count = project_count + 1,
        updated_at = NOW()
    WHERE id = p_team_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Auto-update member count on insert
CREATE OR REPLACE FUNCTION on_team_member_insert()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM increment_team_members(NEW.team_id);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Auto-update member count on delete
CREATE OR REPLACE FUNCTION on_team_member_delete()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM decrement_team_members(OLD.team_id);
    RETURN OLD;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Triggers
DROP TRIGGER IF EXISTS trigger_team_member_insert ON team_members;
CREATE TRIGGER trigger_team_member_insert
    AFTER INSERT ON team_members
    FOR EACH ROW
    EXECUTE FUNCTION on_team_member_insert();

DROP TRIGGER IF EXISTS trigger_team_member_delete ON team_members;
CREATE TRIGGER trigger_team_member_delete
    AFTER DELETE ON team_members
    FOR EACH ROW
    EXECUTE FUNCTION on_team_member_delete();

-- -----------------------------------------------------------------------------
-- Row Level Security (RLS)
-- -----------------------------------------------------------------------------

ALTER TABLE teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_shares ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_activities ENABLE ROW LEVEL SECURITY;

-- Teams: Members can view, owner/admin can update
CREATE POLICY "Team members can view team"
    ON teams FOR SELECT
    USING (
        id IN (
            SELECT team_id FROM team_members WHERE user_id = auth.uid()
        )
    );

CREATE POLICY "Owner can update team"
    ON teams FOR UPDATE
    USING (owner_id = auth.uid())
    WITH CHECK (owner_id = auth.uid());

CREATE POLICY "Anyone can create team"
    ON teams FOR INSERT
    WITH CHECK (owner_id = auth.uid());

CREATE POLICY "Owner can delete team"
    ON teams FOR DELETE
    USING (owner_id = auth.uid());

-- Team members: Team members can view, admin+ can manage
CREATE POLICY "Team members can view members"
    ON team_members FOR SELECT
    USING (
        team_id IN (
            SELECT team_id FROM team_members WHERE user_id = auth.uid()
        )
    );

CREATE POLICY "Admin can manage members"
    ON team_members FOR ALL
    USING (
        team_id IN (
            SELECT team_id FROM team_members 
            WHERE user_id = auth.uid() 
            AND role IN ('owner', 'admin')
        )
    );

-- Team invites: Admin+ can manage
CREATE POLICY "Admin can view invites"
    ON team_invites FOR SELECT
    USING (
        team_id IN (
            SELECT team_id FROM team_members 
            WHERE user_id = auth.uid() 
            AND role IN ('owner', 'admin')
        )
        OR email = (SELECT email FROM auth.users WHERE id = auth.uid())
    );

CREATE POLICY "Admin can create invites"
    ON team_invites FOR INSERT
    WITH CHECK (
        team_id IN (
            SELECT team_id FROM team_members 
            WHERE user_id = auth.uid() 
            AND role IN ('owner', 'admin')
        )
    );

-- Project shares: Project owner or team admin can manage
CREATE POLICY "Can view project shares"
    ON project_shares FOR SELECT
    USING (
        shared_by = auth.uid()
        OR user_id = auth.uid()
        OR team_id IN (
            SELECT team_id FROM team_members WHERE user_id = auth.uid()
        )
    );

CREATE POLICY "Can create project shares"
    ON project_shares FOR INSERT
    WITH CHECK (shared_by = auth.uid());

-- Team activities: Members can view
CREATE POLICY "Team members can view activities"
    ON team_activities FOR SELECT
    USING (
        team_id IN (
            SELECT team_id FROM team_members WHERE user_id = auth.uid()
        )
    );

CREATE POLICY "System can insert activities"
    ON team_activities FOR INSERT
    WITH CHECK (true);


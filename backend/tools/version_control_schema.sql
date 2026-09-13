-- ============================================
-- Version Control Schema
-- Git-like version control for AI App Builder
-- ============================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- Branches Table
-- ============================================

CREATE TABLE IF NOT EXISTS branches (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    head_commit_id TEXT,
    base_branch TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT NOT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    is_protected BOOLEAN DEFAULT FALSE,
    description TEXT,
    
    -- Ensure unique branch names per project
    UNIQUE(project_id, name)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_branches_project 
ON branches(project_id);

CREATE INDEX IF NOT EXISTS idx_branches_project_default 
ON branches(project_id, is_default) 
WHERE is_default = true;

-- ============================================
-- Commits Table
-- ============================================

CREATE TABLE IF NOT EXISTS commits (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    branch TEXT NOT NULL,
    message TEXT NOT NULL,
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    parent_id TEXT REFERENCES commits(id),
    files JSONB NOT NULL DEFAULT '{}',
    changes JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}',
    
    -- Full-text search on message
    message_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', message)) STORED
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_commits_project_branch 
ON commits(project_id, branch, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_commits_parent 
ON commits(parent_id);

CREATE INDEX IF NOT EXISTS idx_commits_author 
ON commits(author_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_commits_message_search 
ON commits USING GIN(message_tsv);

CREATE INDEX IF NOT EXISTS idx_commits_files 
ON commits USING GIN(files);

-- ============================================
-- File Snapshots Table (Content-Addressable)
-- ============================================

CREATE TABLE IF NOT EXISTS file_snapshots (
    hash TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    size INTEGER NOT NULL,
    language TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for language filtering
CREATE INDEX IF NOT EXISTS idx_file_snapshots_language 
ON file_snapshots(language);

-- ============================================
-- Tags Table (Optional - for releases)
-- ============================================

CREATE TABLE IF NOT EXISTS tags (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    commit_id TEXT NOT NULL REFERENCES commits(id),
    message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT NOT NULL,
    
    UNIQUE(project_id, name)
);

CREATE INDEX IF NOT EXISTS idx_tags_project 
ON tags(project_id);

-- ============================================
-- Merge Requests Table (Optional - for PRs)
-- ============================================

CREATE TABLE IF NOT EXISTS merge_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    source_branch TEXT NOT NULL,
    target_branch TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'merged', 'closed')),
    author_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    merged_at TIMESTAMPTZ,
    merged_by TEXT,
    merge_commit_id TEXT REFERENCES commits(id)
);

CREATE INDEX IF NOT EXISTS idx_merge_requests_project 
ON merge_requests(project_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_merge_requests_author 
ON merge_requests(author_id);

-- ============================================
-- Views
-- ============================================

-- Branch with latest commit info
CREATE OR REPLACE VIEW branches_with_commits AS
SELECT 
    b.*,
    c.message AS last_commit_message,
    c.author_name AS last_commit_author,
    c.created_at AS last_commit_at,
    (SELECT COUNT(*) FROM commits WHERE branch = b.name AND project_id = b.project_id) AS commit_count
FROM branches b
LEFT JOIN commits c ON c.id = b.head_commit_id;

-- Project commit activity (last 30 days)
CREATE OR REPLACE VIEW project_commit_activity AS
SELECT 
    project_id,
    DATE(created_at) AS commit_date,
    COUNT(*) AS commit_count,
    COUNT(DISTINCT author_id) AS author_count
FROM commits
WHERE created_at > NOW() - INTERVAL '30 days'
GROUP BY project_id, DATE(created_at)
ORDER BY project_id, commit_date DESC;

-- ============================================
-- Functions
-- ============================================

-- Get commit history for a branch
CREATE OR REPLACE FUNCTION get_branch_history(
    p_project_id TEXT,
    p_branch TEXT,
    p_limit INTEGER DEFAULT 50,
    p_offset INTEGER DEFAULT 0
)
RETURNS TABLE (
    id TEXT,
    message TEXT,
    author_name TEXT,
    created_at TIMESTAMPTZ,
    files_count INTEGER,
    additions INTEGER,
    deletions INTEGER
) AS $$
BEGIN
    RETURN QUERY
    WITH RECURSIVE commit_chain AS (
        -- Start from branch head
        SELECT c.id, c.message, c.author_name, c.created_at, 
               c.parent_id, c.files, c.changes
        FROM commits c
        JOIN branches b ON b.head_commit_id = c.id
        WHERE b.project_id = p_project_id AND b.name = p_branch
        
        UNION ALL
        
        -- Follow parent chain
        SELECT c.id, c.message, c.author_name, c.created_at,
               c.parent_id, c.files, c.changes
        FROM commits c
        JOIN commit_chain cc ON cc.parent_id = c.id
    )
    SELECT 
        cc.id,
        cc.message,
        cc.author_name,
        cc.created_at,
        jsonb_object_keys(cc.files)::INTEGER AS files_count,
        COALESCE((SELECT SUM((item->>'additions')::INTEGER) FROM jsonb_array_elements(cc.changes) AS item), 0)::INTEGER AS additions,
        COALESCE((SELECT SUM((item->>'deletions')::INTEGER) FROM jsonb_array_elements(cc.changes) AS item), 0)::INTEGER AS deletions
    FROM commit_chain cc
    ORDER BY cc.created_at DESC
    LIMIT p_limit
    OFFSET p_offset;
END;
$$ LANGUAGE plpgsql;

-- Find common ancestor between two commits
CREATE OR REPLACE FUNCTION find_common_ancestor(
    p_commit_a TEXT,
    p_commit_b TEXT
)
RETURNS TEXT AS $$
DECLARE
    v_ancestors_a TEXT[];
    v_current TEXT;
    v_result TEXT;
BEGIN
    -- Build ancestor list for commit A
    v_current := p_commit_a;
    WHILE v_current IS NOT NULL LOOP
        v_ancestors_a := array_append(v_ancestors_a, v_current);
        SELECT parent_id INTO v_current FROM commits WHERE id = v_current;
    END LOOP;
    
    -- Walk back from commit B to find first common ancestor
    v_current := p_commit_b;
    WHILE v_current IS NOT NULL LOOP
        IF v_current = ANY(v_ancestors_a) THEN
            RETURN v_current;
        END IF;
        SELECT parent_id INTO v_current FROM commits WHERE id = v_current;
    END LOOP;
    
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- Triggers
-- ============================================

-- Update branch head on new commit
CREATE OR REPLACE FUNCTION update_branch_head()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE branches
    SET head_commit_id = NEW.id
    WHERE project_id = NEW.project_id AND name = NEW.branch;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_branch_head
AFTER INSERT ON commits
FOR EACH ROW
EXECUTE FUNCTION update_branch_head();

-- Update merge request on merge
CREATE OR REPLACE FUNCTION update_merge_request_on_merge()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.metadata ? 'merge' AND (NEW.metadata->>'merge')::BOOLEAN THEN
        UPDATE merge_requests
        SET 
            status = 'merged',
            merged_at = NOW(),
            merge_commit_id = NEW.id
        WHERE 
            project_id = NEW.project_id AND
            source_branch = NEW.metadata->>'source_branch' AND
            target_branch = NEW.branch AND
            status = 'open';
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_merge_request
AFTER INSERT ON commits
FOR EACH ROW
EXECUTE FUNCTION update_merge_request_on_merge();

-- ============================================
-- Row Level Security
-- ============================================

ALTER TABLE branches ENABLE ROW LEVEL SECURITY;
ALTER TABLE commits ENABLE ROW LEVEL SECURITY;
ALTER TABLE file_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE tags ENABLE ROW LEVEL SECURITY;
ALTER TABLE merge_requests ENABLE ROW LEVEL SECURITY;

-- Branches: Project members can read/write
CREATE POLICY "branches_project_access"
ON branches FOR ALL
USING (true);  -- Replace with actual project membership check

-- Commits: Project members can read/write
CREATE POLICY "commits_project_access"
ON commits FOR ALL
USING (true);

-- File snapshots: Anyone can read (content-addressable)
CREATE POLICY "file_snapshots_read_all"
ON file_snapshots FOR SELECT
USING (true);

-- File snapshots: Authenticated users can insert
CREATE POLICY "file_snapshots_insert"
ON file_snapshots FOR INSERT
WITH CHECK (true);

-- Tags: Project members can manage
CREATE POLICY "tags_project_access"
ON tags FOR ALL
USING (true);

-- Merge requests: Project members can manage
CREATE POLICY "merge_requests_project_access"
ON merge_requests FOR ALL
USING (true);

-- ============================================
-- Sample Data (Optional)
-- ============================================

-- Create default 'main' branch for demo project
-- INSERT INTO branches (project_id, name, is_default, created_by)
-- VALUES ('demo_project', 'main', true, 'system')
-- ON CONFLICT DO NOTHING;

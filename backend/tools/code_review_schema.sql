-- ============================================
-- Code Reviews Schema (PostgreSQL)
--
-- Based on forum recommendations (Dec 2025):
-- - Store all reviews for analytics
-- - Track provider performance
-- - Enable review history per project/PR
-- ============================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- Code Reviews Table
-- ============================================

CREATE TABLE IF NOT EXISTS code_reviews (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'claude',
    
    -- Review details
    files_reviewed TEXT[] NOT NULL DEFAULT '{}',
    findings JSONB NOT NULL DEFAULT '[]',
    findings_count INTEGER GENERATED ALWAYS AS (jsonb_array_length(findings)) STORED,
    score INTEGER NOT NULL DEFAULT 100,
    
    -- GitHub integration
    pr_number INTEGER,
    pr_url TEXT,
    commit_sha TEXT,
    base_branch TEXT,
    head_branch TEXT,
    
    -- Metadata
    review_time_ms INTEGER,
    metadata JSONB DEFAULT '{}',
    
    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Indexes for common queries
    CONSTRAINT valid_score CHECK (score >= 0 AND score <= 100)
);

-- Index for project queries
CREATE INDEX IF NOT EXISTS idx_code_reviews_project 
ON code_reviews(project_id, created_at DESC);

-- Index for PR queries
CREATE INDEX IF NOT EXISTS idx_code_reviews_pr 
ON code_reviews(pr_number) 
WHERE pr_number IS NOT NULL;

-- Index for provider analytics
CREATE INDEX IF NOT EXISTS idx_code_reviews_provider 
ON code_reviews(provider, created_at DESC);

-- GIN index for JSONB findings search
CREATE INDEX IF NOT EXISTS idx_code_reviews_findings 
ON code_reviews USING GIN (findings);

-- ============================================
-- Review Findings Table (Denormalized for queries)
-- ============================================

CREATE TABLE IF NOT EXISTS review_findings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    review_id TEXT NOT NULL REFERENCES code_reviews(id) ON DELETE CASCADE,
    
    -- Finding details
    title TEXT NOT NULL,
    description TEXT,
    severity TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
    category TEXT NOT NULL,
    
    -- Location
    file_path TEXT,
    line_start INTEGER,
    line_end INTEGER,
    
    -- Fix information
    suggestion TEXT,
    code_fix TEXT,
    auto_fixable BOOLEAN DEFAULT FALSE,
    
    -- Status tracking
    status TEXT DEFAULT 'open' CHECK (status IN ('open', 'fixed', 'ignored', 'wontfix')),
    fixed_at TIMESTAMPTZ,
    fixed_by TEXT,
    
    -- Metadata
    confidence DECIMAL(3,2),
    references TEXT[],
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for review findings
CREATE INDEX IF NOT EXISTS idx_review_findings_review 
ON review_findings(review_id);

-- Index for severity filtering
CREATE INDEX IF NOT EXISTS idx_review_findings_severity 
ON review_findings(severity);

-- Index for open findings
CREATE INDEX IF NOT EXISTS idx_review_findings_open 
ON review_findings(status) 
WHERE status = 'open';

-- ============================================
-- Review Statistics View
-- ============================================

CREATE OR REPLACE VIEW review_statistics AS
SELECT 
    project_id,
    COUNT(*) as total_reviews,
    AVG(score) as avg_score,
    SUM(findings_count) as total_findings,
    COUNT(DISTINCT pr_number) as prs_reviewed,
    
    -- Findings by severity
    SUM((SELECT COUNT(*) FROM jsonb_array_elements(findings) f WHERE f->>'severity' = 'critical')) as critical_findings,
    SUM((SELECT COUNT(*) FROM jsonb_array_elements(findings) f WHERE f->>'severity' = 'high')) as high_findings,
    SUM((SELECT COUNT(*) FROM jsonb_array_elements(findings) f WHERE f->>'severity' = 'medium')) as medium_findings,
    SUM((SELECT COUNT(*) FROM jsonb_array_elements(findings) f WHERE f->>'severity' = 'low')) as low_findings,
    
    -- Provider breakdown
    jsonb_object_agg(provider, provider_count) as by_provider,
    
    -- Time stats
    AVG(review_time_ms) as avg_review_time_ms,
    MIN(created_at) as first_review,
    MAX(created_at) as last_review
    
FROM code_reviews
CROSS JOIN LATERAL (
    SELECT provider, COUNT(*) as provider_count 
    FROM code_reviews c2 
    WHERE c2.project_id = code_reviews.project_id 
    GROUP BY provider
) provider_stats
GROUP BY project_id;

-- ============================================
-- Provider Performance View
-- ============================================

CREATE OR REPLACE VIEW provider_performance AS
SELECT 
    provider,
    COUNT(*) as total_reviews,
    AVG(score) as avg_score,
    AVG(findings_count) as avg_findings,
    AVG(review_time_ms) as avg_time_ms,
    
    -- Severity distribution
    SUM((SELECT COUNT(*) FROM jsonb_array_elements(findings) f WHERE f->>'severity' = 'critical')) as critical_found,
    SUM((SELECT COUNT(*) FROM jsonb_array_elements(findings) f WHERE f->>'severity' = 'high')) as high_found,
    
    -- Time range
    MIN(created_at) as first_used,
    MAX(created_at) as last_used
    
FROM code_reviews
GROUP BY provider;

-- ============================================
-- Functions
-- ============================================

-- Function to get review history for a project
CREATE OR REPLACE FUNCTION get_project_review_history(
    p_project_id TEXT,
    p_limit INTEGER DEFAULT 10,
    p_offset INTEGER DEFAULT 0
)
RETURNS TABLE (
    id TEXT,
    provider TEXT,
    files_count INTEGER,
    findings_count INTEGER,
    score INTEGER,
    pr_number INTEGER,
    created_at TIMESTAMPTZ
)
LANGUAGE SQL
STABLE
AS $$
    SELECT 
        id,
        provider,
        array_length(files_reviewed, 1) as files_count,
        findings_count,
        score,
        pr_number,
        created_at
    FROM code_reviews
    WHERE project_id = p_project_id
    ORDER BY created_at DESC
    LIMIT p_limit
    OFFSET p_offset;
$$;

-- Function to get common issues
CREATE OR REPLACE FUNCTION get_common_issues(
    p_project_id TEXT,
    p_limit INTEGER DEFAULT 10
)
RETURNS TABLE (
    title TEXT,
    category TEXT,
    severity TEXT,
    occurrence_count BIGINT
)
LANGUAGE SQL
STABLE
AS $$
    SELECT 
        f->>'title' as title,
        f->>'category' as category,
        f->>'severity' as severity,
        COUNT(*) as occurrence_count
    FROM code_reviews,
    LATERAL jsonb_array_elements(findings) as f
    WHERE project_id = p_project_id
    GROUP BY f->>'title', f->>'category', f->>'severity'
    ORDER BY occurrence_count DESC
    LIMIT p_limit;
$$;

-- Function to get score trend
CREATE OR REPLACE FUNCTION get_score_trend(
    p_project_id TEXT,
    p_days INTEGER DEFAULT 30
)
RETURNS TABLE (
    review_date DATE,
    avg_score NUMERIC,
    review_count BIGINT
)
LANGUAGE SQL
STABLE
AS $$
    SELECT 
        DATE(created_at) as review_date,
        AVG(score) as avg_score,
        COUNT(*) as review_count
    FROM code_reviews
    WHERE project_id = p_project_id
    AND created_at >= NOW() - (p_days || ' days')::INTERVAL
    GROUP BY DATE(created_at)
    ORDER BY review_date;
$$;

-- ============================================
-- Row Level Security
-- ============================================

ALTER TABLE code_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_findings ENABLE ROW LEVEL SECURITY;

-- Policy for reading reviews (public for now, can be restricted)
CREATE POLICY "Allow read access to reviews"
ON code_reviews FOR SELECT
USING (true);

-- Policy for inserting reviews (service role only)
CREATE POLICY "Allow insert for service role"
ON code_reviews FOR INSERT
WITH CHECK (true);

-- Policy for findings
CREATE POLICY "Allow read access to findings"
ON review_findings FOR SELECT
USING (true);

CREATE POLICY "Allow insert for service role findings"
ON review_findings FOR INSERT
WITH CHECK (true);

-- ============================================
-- Triggers
-- ============================================

-- Trigger to extract findings to separate table
CREATE OR REPLACE FUNCTION extract_findings()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    -- Insert each finding into review_findings table
    INSERT INTO review_findings (
        review_id,
        title,
        description,
        severity,
        category,
        file_path,
        line_start,
        line_end,
        suggestion,
        code_fix,
        auto_fixable,
        confidence,
        references
    )
    SELECT 
        NEW.id,
        f->>'title',
        f->>'description',
        f->>'severity',
        f->>'category',
        f->>'file',
        (f->>'line')::INTEGER,
        (f->>'end_line')::INTEGER,
        f->>'suggestion',
        f->>'code_fix',
        (f->>'auto_fixable')::BOOLEAN,
        (f->>'confidence')::DECIMAL,
        ARRAY(SELECT jsonb_array_elements_text(f->'references'))
    FROM jsonb_array_elements(NEW.findings) AS f;
    
    RETURN NEW;
END;
$$;

CREATE TRIGGER trigger_extract_findings
AFTER INSERT ON code_reviews
FOR EACH ROW
EXECUTE FUNCTION extract_findings();

-- ============================================
-- Sample Queries
-- ============================================

-- Get reviews with critical issues
-- SELECT * FROM code_reviews 
-- WHERE findings @> '[{"severity": "critical"}]'
-- ORDER BY created_at DESC;

-- Get review stats for a project
-- SELECT * FROM review_statistics WHERE project_id = 'proj_123';

-- Get common issues
-- SELECT * FROM get_common_issues('proj_123', 10);

-- Get score trend
-- SELECT * FROM get_score_trend('proj_123', 30);

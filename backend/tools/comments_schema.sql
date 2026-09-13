-- ============================================
-- Real-time Comments Schema (PostgreSQL)
--
-- Features:
-- - Line-level code comments
-- - Threaded discussions
-- - @mentions with notifications
-- - Reactions
-- - Resolution workflow
-- - Real-time updates
-- ============================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- Comments Table
-- ============================================

CREATE TABLE IF NOT EXISTS comments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL,
    user_avatar TEXT,
    
    -- Content
    content TEXT NOT NULL,
    suggestion_code TEXT,  -- Code suggestion block
    
    -- Location (for line-level comments)
    file_path TEXT,
    line_start INTEGER,
    line_end INTEGER,
    commit_sha TEXT,
    
    -- Type and status
    comment_type TEXT NOT NULL DEFAULT 'comment' 
        CHECK (comment_type IN ('comment', 'suggestion', 'question', 'issue', 'praise')),
    status TEXT NOT NULL DEFAULT 'open' 
        CHECK (status IN ('open', 'resolved', 'wontfix', 'outdated')),
    
    -- Threading
    parent_id UUID REFERENCES comments(id) ON DELETE CASCADE,
    
    -- Mentions (parsed from content)
    mentioned_users TEXT[] DEFAULT '{}',
    
    -- Resolution
    resolved_by TEXT,
    resolved_at TIMESTAMPTZ,
    
    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_comments_project ON comments(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_comments_file ON comments(project_id, file_path, line_start);
CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_id);
CREATE INDEX IF NOT EXISTS idx_comments_status ON comments(project_id, status) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_comments_user ON comments(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_comments_mentions ON comments USING GIN (mentioned_users);

-- ============================================
-- Comment Reactions Table
-- ============================================

CREATE TABLE IF NOT EXISTS comment_reactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    comment_id UUID NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    emoji TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- One reaction per user per emoji
    UNIQUE(comment_id, user_id, emoji)
);

CREATE INDEX IF NOT EXISTS idx_reactions_comment ON comment_reactions(comment_id);

-- ============================================
-- Notifications Table
-- ============================================

CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('mention', 'reply', 'resolved', 'reaction')),
    title TEXT NOT NULL,
    content TEXT,
    link TEXT,
    read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, read, created_at DESC);

-- ============================================
-- Views
-- ============================================

-- Comments with reaction counts
CREATE OR REPLACE VIEW comments_with_reactions AS
SELECT 
    c.*,
    COALESCE(
        (SELECT jsonb_agg(jsonb_build_object('emoji', r.emoji, 'count', r.count))
         FROM (
             SELECT emoji, COUNT(*) as count
             FROM comment_reactions
             WHERE comment_id = c.id
             GROUP BY emoji
         ) r),
        '[]'::jsonb
    ) as reactions,
    (SELECT COUNT(*) FROM comments WHERE parent_id = c.id) as reply_count
FROM comments c;

-- Unresolved comments per project
CREATE OR REPLACE VIEW unresolved_comments AS
SELECT 
    project_id,
    file_path,
    COUNT(*) as count
FROM comments
WHERE status = 'open' AND parent_id IS NULL
GROUP BY project_id, file_path;

-- ============================================
-- Functions
-- ============================================

-- Update timestamp trigger
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_comments_updated_at
    BEFORE UPDATE ON comments
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- Get thread with all replies
CREATE OR REPLACE FUNCTION get_comment_thread(p_comment_id UUID)
RETURNS TABLE (
    id UUID,
    project_id TEXT,
    user_id TEXT,
    user_name TEXT,
    user_avatar TEXT,
    content TEXT,
    file_path TEXT,
    line_start INTEGER,
    comment_type TEXT,
    status TEXT,
    parent_id UUID,
    created_at TIMESTAMPTZ,
    depth INTEGER
)
LANGUAGE SQL
STABLE
AS $$
    WITH RECURSIVE thread AS (
        -- Base case: the root comment
        SELECT 
            c.id, c.project_id, c.user_id, c.user_name, c.user_avatar,
            c.content, c.file_path, c.line_start, c.comment_type,
            c.status, c.parent_id, c.created_at,
            0 as depth
        FROM comments c
        WHERE c.id = p_comment_id
        
        UNION ALL
        
        -- Recursive: get all replies
        SELECT 
            c.id, c.project_id, c.user_id, c.user_name, c.user_avatar,
            c.content, c.file_path, c.line_start, c.comment_type,
            c.status, c.parent_id, c.created_at,
            t.depth + 1
        FROM comments c
        INNER JOIN thread t ON c.parent_id = t.id
    )
    SELECT * FROM thread ORDER BY created_at;
$$;

-- Create notification for mention
CREATE OR REPLACE FUNCTION notify_mention()
RETURNS TRIGGER AS $$
DECLARE
    mentioned TEXT;
BEGIN
    IF NEW.mentioned_users IS NOT NULL AND array_length(NEW.mentioned_users, 1) > 0 THEN
        FOREACH mentioned IN ARRAY NEW.mentioned_users
        LOOP
            INSERT INTO notifications (user_id, type, title, content, link)
            VALUES (
                mentioned,
                'mention',
                NEW.user_name || ' mentioned you in a comment',
                LEFT(NEW.content, 100),
                '/project/' || NEW.project_id || '?comment=' || NEW.id
            );
        END LOOP;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_notify_mention
    AFTER INSERT ON comments
    FOR EACH ROW
    EXECUTE FUNCTION notify_mention();

-- Create notification for reply
CREATE OR REPLACE FUNCTION notify_reply()
RETURNS TRIGGER AS $$
DECLARE
    parent_user TEXT;
BEGIN
    IF NEW.parent_id IS NOT NULL THEN
        SELECT user_id INTO parent_user FROM comments WHERE id = NEW.parent_id;
        
        IF parent_user IS NOT NULL AND parent_user != NEW.user_id THEN
            INSERT INTO notifications (user_id, type, title, content, link)
            VALUES (
                parent_user,
                'reply',
                NEW.user_name || ' replied to your comment',
                LEFT(NEW.content, 100),
                '/project/' || NEW.project_id || '?comment=' || NEW.id
            );
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_notify_reply
    AFTER INSERT ON comments
    FOR EACH ROW
    EXECUTE FUNCTION notify_reply();

-- ============================================
-- Row Level Security
-- ============================================

ALTER TABLE comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE comment_reactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

-- Comments: Anyone in project can read, author can update/delete
CREATE POLICY "Comments are viewable by project members"
ON comments FOR SELECT
USING (true);  -- Add project membership check in production

CREATE POLICY "Users can create comments"
ON comments FOR INSERT
WITH CHECK (true);  -- Add auth check in production

CREATE POLICY "Authors can update their comments"
ON comments FOR UPDATE
USING (user_id = auth.uid()::text);

CREATE POLICY "Authors can delete their comments"
ON comments FOR DELETE
USING (user_id = auth.uid()::text);

-- Reactions: Anyone can add, only own can remove
CREATE POLICY "Reactions are viewable by all"
ON comment_reactions FOR SELECT
USING (true);

CREATE POLICY "Users can add reactions"
ON comment_reactions FOR INSERT
WITH CHECK (true);

CREATE POLICY "Users can remove their reactions"
ON comment_reactions FOR DELETE
USING (user_id = auth.uid()::text);

-- Notifications: Users see only their own
CREATE POLICY "Users see their own notifications"
ON notifications FOR SELECT
USING (user_id = auth.uid()::text);

CREATE POLICY "System can create notifications"
ON notifications FOR INSERT
WITH CHECK (true);

CREATE POLICY "Users can update their notifications"
ON notifications FOR UPDATE
USING (user_id = auth.uid()::text);

-- ============================================
-- Sample Data
-- ============================================

-- Insert sample comment (for testing)
-- INSERT INTO comments (project_id, user_id, user_name, content, file_path, line_start, comment_type)
-- VALUES (
--     'proj_123',
--     'user_1',
--     'John Doe',
--     'This function could be optimized. Consider using memoization. @jane',
--     'src/utils.ts',
--     42,
--     'suggestion'
-- );

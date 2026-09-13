-- ============================================
-- Prompt History Schema
-- Based on forum research (December 2025)
-- ============================================

-- Prompt types enum
create type prompt_type as enum (
    'code_generation',
    'code_edit',
    'code_review',
    'chat',
    'documentation',
    'debugging',
    'refactoring',
    'testing'
);

-- Prompt status enum
create type prompt_status as enum (
    'pending',
    'processing',
    'completed',
    'failed',
    'cancelled'
);

-- ============================================
-- Prompt History Table
-- ============================================

create table prompt_history (
    id uuid primary key default gen_random_uuid(),
    
    -- User & Project
    user_id text not null references users(id) on delete cascade,
    project_id uuid references projects(id) on delete cascade,
    session_id text,
    
    -- Prompt content
    prompt text not null,
    system_prompt text,
    context jsonb default '{}',
    
    -- Response
    response text,
    generated_code text,
    generated_files jsonb default '[]',
    
    -- Metadata
    prompt_type prompt_type default 'chat',
    status prompt_status default 'pending',
    model text default '',
    
    -- Tokens
    prompt_tokens integer default 0,
    completion_tokens integer default 0,
    total_tokens integer default 0,
    
    -- Timing
    created_at timestamptz default now(),
    started_at timestamptz,
    completed_at timestamptz,
    duration_ms integer default 0,
    
    -- Error
    error text,
    
    -- Organization
    tags text[] default '{}',
    is_favorite boolean default false
);

-- Indexes
create index idx_prompt_history_user on prompt_history(user_id);
create index idx_prompt_history_project on prompt_history(project_id);
create index idx_prompt_history_created on prompt_history(created_at desc);
create index idx_prompt_history_type on prompt_history(prompt_type);
create index idx_prompt_history_status on prompt_history(status);
create index idx_prompt_history_favorite on prompt_history(user_id, is_favorite) where is_favorite = true;
create index idx_prompt_history_tags on prompt_history using gin(tags);

-- Full text search
alter table prompt_history add column search_vector tsvector
    generated always as (
        setweight(to_tsvector('english', coalesce(prompt, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(response, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(generated_code, '')), 'C')
    ) stored;

create index idx_prompt_history_search on prompt_history using gin(search_vector);

-- ============================================
-- RLS Policies (Forum Pattern)
-- ============================================

alter table prompt_history enable row level security;

-- Users can view their own prompts
create policy "Users can view their own prompts"
    on prompt_history for select
    using (user_id = auth.jwt() ->> 'sub');

-- Users can insert their own prompts
create policy "Users can insert their own prompts"
    on prompt_history for insert
    with check (user_id = auth.jwt() ->> 'sub');

-- Users can update their own prompts
create policy "Users can update their own prompts"
    on prompt_history for update
    using (user_id = auth.jwt() ->> 'sub');

-- Users can delete their own prompts
create policy "Users can delete their own prompts"
    on prompt_history for delete
    using (user_id = auth.jwt() ->> 'sub');

-- ============================================
-- Prompt Templates Table
-- ============================================

create table prompt_templates (
    id uuid primary key default gen_random_uuid(),
    user_id text not null references users(id) on delete cascade,
    
    name text not null,
    description text,
    prompt text not null,
    system_prompt text,
    prompt_type prompt_type default 'code_generation',
    
    -- Source
    created_from_prompt uuid references prompt_history(id) on delete set null,
    
    -- Usage
    usage_count integer default 0,
    
    -- Sharing
    is_public boolean default false,
    
    tags text[] default '{}',
    
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create index idx_prompt_templates_user on prompt_templates(user_id);
create index idx_prompt_templates_public on prompt_templates(is_public) where is_public = true;

alter table prompt_templates enable row level security;

create policy "Users can manage their own templates"
    on prompt_templates for all
    using (user_id = auth.jwt() ->> 'sub');

create policy "Users can view public templates"
    on prompt_templates for select
    using (is_public = true);

-- ============================================
-- Triggers
-- ============================================

-- Update timestamp trigger
create trigger update_prompt_templates_updated_at
    before update on prompt_templates
    for each row
    execute function update_updated_at();

-- Calculate duration on completion
create or replace function calculate_prompt_duration()
returns trigger as $$
begin
    if new.status in ('completed', 'failed') and new.started_at is not null then
        new.completed_at = now();
        new.duration_ms = extract(epoch from (new.completed_at - new.started_at)) * 1000;
    end if;
    return new;
end;
$$ language plpgsql;

create trigger calculate_prompt_duration_trigger
    before update on prompt_history
    for each row
    when (old.status != new.status and new.status in ('completed', 'failed'))
    execute function calculate_prompt_duration();

-- ============================================
-- Helper Functions
-- ============================================

-- Search prompts with full text
create or replace function search_prompts(
    p_user_id text,
    p_query text,
    p_limit integer default 50,
    p_offset integer default 0
)
returns table (
    id uuid,
    prompt text,
    response text,
    prompt_type prompt_type,
    status prompt_status,
    created_at timestamptz,
    rank real
) as $$
begin
    return query
    select 
        ph.id,
        ph.prompt,
        ph.response,
        ph.prompt_type,
        ph.status,
        ph.created_at,
        ts_rank(ph.search_vector, plainto_tsquery('english', p_query)) as rank
    from prompt_history ph
    where ph.user_id = p_user_id
      and ph.search_vector @@ plainto_tsquery('english', p_query)
    order by rank desc, ph.created_at desc
    limit p_limit
    offset p_offset;
end;
$$ language plpgsql security definer;

-- Get prompt statistics
create or replace function get_prompt_stats(
    p_user_id text,
    p_days integer default 30
)
returns jsonb as $$
declare
    result jsonb;
begin
    select jsonb_build_object(
        'total_prompts', count(*),
        'total_tokens', coalesce(sum(total_tokens), 0),
        'avg_tokens', coalesce(avg(total_tokens), 0),
        'avg_duration_ms', coalesce(avg(duration_ms), 0),
        'success_rate', 
            case when count(*) > 0 
                then (count(*) filter (where status = 'completed'))::float / count(*) * 100
                else 0 
            end,
        'by_type', (
            select jsonb_object_agg(prompt_type, cnt)
            from (
                select prompt_type, count(*) as cnt
                from prompt_history
                where user_id = p_user_id
                  and created_at >= now() - (p_days || ' days')::interval
                group by prompt_type
            ) t
        ),
        'by_model', (
            select jsonb_object_agg(model, cnt)
            from (
                select model, count(*) as cnt
                from prompt_history
                where user_id = p_user_id
                  and created_at >= now() - (p_days || ' days')::interval
                  and model != ''
                group by model
            ) t
        )
    )
    into result
    from prompt_history
    where user_id = p_user_id
      and created_at >= now() - (p_days || ' days')::interval;
    
    return result;
end;
$$ language plpgsql security definer;

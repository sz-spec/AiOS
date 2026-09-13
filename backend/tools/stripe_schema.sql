-- =============================================================================
-- Stripe/Payments Schema for AI App Builder
-- =============================================================================

-- =============================================================================
-- Subscriptions Table
-- =============================================================================

CREATE TABLE IF NOT EXISTS subscriptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    
    -- Stripe IDs
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    
    -- Plan info
    plan TEXT DEFAULT 'free' CHECK (plan IN ('free', 'monthly', 'yearly')),
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'canceled', 'past_due', 'trialing', 'incomplete')),
    
    -- Billing period
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    cancel_at_period_end BOOLEAN DEFAULT false,
    
    -- AI Credits (hybrid model)
    ai_credits INTEGER DEFAULT 10,  -- Free tier gets 10 credits
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe_customer ON subscriptions(stripe_customer_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe_sub ON subscriptions(stripe_subscription_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);

-- Enable RLS
ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;

-- RLS Policies
CREATE POLICY "Users can view own subscription" ON subscriptions
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "System can manage subscriptions" ON subscriptions
    FOR ALL USING (true);  -- Service role bypasses RLS anyway

-- Trigger for updated_at
CREATE TRIGGER subscriptions_updated_at
    BEFORE UPDATE ON subscriptions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- =============================================================================
-- Payment History Table
-- =============================================================================

CREATE TABLE IF NOT EXISTS payment_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    
    -- Stripe IDs
    stripe_payment_intent_id TEXT,
    stripe_invoice_id TEXT,
    
    -- Amount
    amount_cents INTEGER NOT NULL,
    currency TEXT DEFAULT 'usd',
    
    -- Status
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'pending', 'refunded')),
    
    -- Details
    description TEXT,
    plan TEXT,  -- Which plan or credit package
    credits_added INTEGER DEFAULT 0,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_payment_history_user ON payment_history(user_id);
CREATE INDEX IF NOT EXISTS idx_payment_history_created ON payment_history(created_at DESC);

-- Enable RLS
ALTER TABLE payment_history ENABLE ROW LEVEL SECURITY;

-- RLS Policies
CREATE POLICY "Users can view own payments" ON payment_history
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "System can insert payments" ON payment_history
    FOR INSERT WITH CHECK (true);

-- =============================================================================
-- Credit Transactions Table (for tracking)
-- =============================================================================

CREATE TABLE IF NOT EXISTS credit_transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    
    -- Transaction
    amount INTEGER NOT NULL,  -- Positive = add, Negative = use
    balance_after INTEGER NOT NULL,
    
    -- Reason
    type TEXT NOT NULL CHECK (type IN ('purchase', 'usage', 'bonus', 'refund', 'subscription_reset')),
    description TEXT,
    
    -- Reference
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    payment_id UUID REFERENCES payment_history(id) ON DELETE SET NULL,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index
CREATE INDEX IF NOT EXISTS idx_credit_transactions_user ON credit_transactions(user_id, created_at DESC);

-- Enable RLS
ALTER TABLE credit_transactions ENABLE ROW LEVEL SECURITY;

-- Policies
CREATE POLICY "Users can view own transactions" ON credit_transactions
    FOR SELECT USING (auth.uid() = user_id);

-- =============================================================================
-- Pricing Table (for display)
-- =============================================================================

CREATE TABLE IF NOT EXISTS pricing_plans (
    id TEXT PRIMARY KEY,  -- 'free', 'monthly', 'yearly'
    name TEXT NOT NULL,
    description TEXT,
    price_cents INTEGER NOT NULL,
    currency TEXT DEFAULT 'usd',
    interval TEXT CHECK (interval IN ('month', 'year', 'one_time')),
    
    -- Features
    ai_credits_monthly INTEGER DEFAULT 0,  -- 0 = unlimited for paid
    max_projects INTEGER DEFAULT 3,
    features JSONB DEFAULT '[]',
    
    -- Stripe
    stripe_price_id TEXT,
    
    -- Display
    is_popular BOOLEAN DEFAULT false,
    sort_order INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT true
);

-- Insert default plans
INSERT INTO pricing_plans (id, name, description, price_cents, interval, ai_credits_monthly, max_projects, features, is_popular, sort_order)
VALUES 
    ('free', 'Free', 'Get started with AI app building', 0, NULL, 10, 3, 
     '["10 AI generations/month", "3 projects", "Community support", "Basic templates"]'::jsonb, 
     false, 1),
    ('monthly', 'Pro Monthly', 'For serious builders', 1900, 'month', 0, -1,
     '["Unlimited AI generations", "Unlimited projects", "Priority support", "All templates", "GitHub sync", "Custom domains"]'::jsonb,
     true, 2),
    ('yearly', 'Pro Yearly', 'Best value - 2 months free', 19000, 'year', 0, -1,
     '["Everything in Pro", "2 months free", "Early access to features", "API access"]'::jsonb,
     false, 3)
ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- Credit Packages Table
-- =============================================================================

CREATE TABLE IF NOT EXISTS credit_packages (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    credits INTEGER NOT NULL,
    price_cents INTEGER NOT NULL,
    currency TEXT DEFAULT 'usd',
    stripe_price_id TEXT,
    bonus_credits INTEGER DEFAULT 0,  -- Extra credits as bonus
    is_active BOOLEAN DEFAULT true,
    sort_order INTEGER DEFAULT 0
);

-- Insert default packages
INSERT INTO credit_packages (id, name, credits, price_cents, bonus_credits, sort_order)
VALUES 
    ('credits_100', '100 Credits', 100, 499, 0, 1),
    ('credits_500', '500 Credits', 500, 1999, 50, 2),
    ('credits_1000', '1000 Credits', 1000, 3499, 150, 3)
ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- Functions
-- =============================================================================

-- Function to check if user has credits
CREATE OR REPLACE FUNCTION has_credits(p_user_id UUID, p_amount INTEGER DEFAULT 1)
RETURNS BOOLEAN AS $$
DECLARE
    v_plan TEXT;
    v_credits INTEGER;
BEGIN
    SELECT plan, ai_credits INTO v_plan, v_credits
    FROM subscriptions
    WHERE user_id = p_user_id;
    
    -- Paid plans have unlimited
    IF v_plan IN ('monthly', 'yearly') THEN
        RETURN true;
    END IF;
    
    -- Check credits
    RETURN COALESCE(v_credits, 0) >= p_amount;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to use credits
CREATE OR REPLACE FUNCTION use_credits(
    p_user_id UUID, 
    p_amount INTEGER DEFAULT 1,
    p_description TEXT DEFAULT NULL,
    p_project_id UUID DEFAULT NULL
)
RETURNS BOOLEAN AS $$
DECLARE
    v_plan TEXT;
    v_credits INTEGER;
    v_new_balance INTEGER;
BEGIN
    -- Get current subscription
    SELECT plan, ai_credits INTO v_plan, v_credits
    FROM subscriptions
    WHERE user_id = p_user_id
    FOR UPDATE;  -- Lock row
    
    -- Paid plans have unlimited
    IF v_plan IN ('monthly', 'yearly') THEN
        -- Still log the usage
        INSERT INTO credit_transactions (user_id, amount, balance_after, type, description, project_id)
        VALUES (p_user_id, 0, 999999, 'usage', p_description, p_project_id);
        RETURN true;
    END IF;
    
    -- Check credits
    IF COALESCE(v_credits, 0) < p_amount THEN
        RETURN false;
    END IF;
    
    -- Deduct credits
    v_new_balance := v_credits - p_amount;
    
    UPDATE subscriptions
    SET ai_credits = v_new_balance, updated_at = NOW()
    WHERE user_id = p_user_id;
    
    -- Log transaction
    INSERT INTO credit_transactions (user_id, amount, balance_after, type, description, project_id)
    VALUES (p_user_id, -p_amount, v_new_balance, 'usage', p_description, p_project_id);
    
    RETURN true;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to add credits
CREATE OR REPLACE FUNCTION add_credits(
    p_user_id UUID,
    p_amount INTEGER,
    p_type TEXT DEFAULT 'purchase',
    p_description TEXT DEFAULT NULL,
    p_payment_id UUID DEFAULT NULL
)
RETURNS INTEGER AS $$
DECLARE
    v_credits INTEGER;
    v_new_balance INTEGER;
BEGIN
    -- Get current credits
    SELECT ai_credits INTO v_credits
    FROM subscriptions
    WHERE user_id = p_user_id
    FOR UPDATE;
    
    -- Calculate new balance
    v_new_balance := COALESCE(v_credits, 0) + p_amount;
    
    -- Update
    UPDATE subscriptions
    SET ai_credits = v_new_balance, updated_at = NOW()
    WHERE user_id = p_user_id;
    
    -- Log transaction
    INSERT INTO credit_transactions (user_id, amount, balance_after, type, description, payment_id)
    VALUES (p_user_id, p_amount, v_new_balance, p_type, p_description, p_payment_id);
    
    RETURN v_new_balance;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- =============================================================================
-- Auto-create subscription on user signup
-- =============================================================================

CREATE OR REPLACE FUNCTION create_subscription_for_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO subscriptions (user_id, plan, status, ai_credits)
    VALUES (NEW.id, 'free', 'active', 10)
    ON CONFLICT (user_id) DO NOTHING;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Trigger (on auth.users - requires service role)
-- Note: This needs to be run with service role or in the database dashboard
-- DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
-- CREATE TRIGGER on_auth_user_created
--     AFTER INSERT ON auth.users
--     FOR EACH ROW
--     EXECUTE FUNCTION create_subscription_for_new_user();

-- =============================================================================
-- Done!
-- =============================================================================

COMMENT ON TABLE subscriptions IS 'User subscription and credit tracking';
COMMENT ON TABLE payment_history IS 'Payment transaction history';
COMMENT ON TABLE credit_transactions IS 'AI credit usage and purchase log';
COMMENT ON TABLE pricing_plans IS 'Available pricing plans for display';
COMMENT ON TABLE credit_packages IS 'AI credit packages for purchase';

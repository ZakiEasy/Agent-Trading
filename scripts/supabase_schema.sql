-- ==============================================================================
-- AGENT TRADING - SUPABASE PRODUCTION DATABASE SCHEMA (POSTGRESQL 17)
-- ==============================================================================

-- 1. Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 2. Trigger function for auto-updating timestamps
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS '
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
' LANGUAGE plpgsql;

-- 3. Table Watchlist (Univers de Surveillance & Actions Suivies)
CREATE TABLE IF NOT EXISTS public.watchlist (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    symbol VARCHAR(20) NOT NULL UNIQUE,
    name VARCHAR(150),
    category VARCHAR(100),
    category_icon VARCHAR(10) DEFAULT '📦',
    is_pea BOOLEAN DEFAULT FALSE,
    account_type VARCHAR(20) DEFAULT 'CTO (US)',
    sharia_status VARCHAR(40) DEFAULT 'DONNÉES INSUFFISANTES',
    currency VARCHAR(5) DEFAULT 'EUR',
    target_entry_price NUMERIC(12, 4),
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_watchlist_updated_at ON public.watchlist;
CREATE TRIGGER trg_watchlist_updated_at
BEFORE UPDATE ON public.watchlist
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 4. Table Positions (Portefeuille & Suivi Multi-Brokers Réel)
CREATE TABLE IF NOT EXISTS public.positions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    symbol VARCHAR(20) NOT NULL,
    company_name VARCHAR(150),
    broker VARCHAR(50) NOT NULL DEFAULT 'Trading 212', -- 'Trading 212', 'XTB', 'PEA Bourse Direct'
    account_type VARCHAR(20) DEFAULT 'CTO',
    pru NUMERIC(12, 4) NOT NULL,
    quantity NUMERIC(14, 6) NOT NULL,
    invested_capital NUMERIC(12, 2) NOT NULL,
    current_price NUMERIC(12, 4),
    stop_loss NUMERIC(12, 4) NOT NULL,
    take_profit_1 NUMERIC(12, 4),
    take_profit_2 NUMERIC(12, 4),
    r_max_amount NUMERIC(10, 2),
    r_max_pct NUMERIC(5, 2) DEFAULT 1.0,
    currency VARCHAR(5) DEFAULT 'EUR',
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE', -- 'ACTIVE', 'PARTIALLY_CLOSED', 'CLOSED'
    opened_at TIMESTAMPTZ DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    realized_pnl_eur NUMERIC(12, 2) DEFAULT 0.0,
    realized_pnl_pct NUMERIC(8, 4) DEFAULT 0.0,
    unrealized_pnl_eur NUMERIC(12, 2) DEFAULT 0.0,
    unrealized_pnl_pct NUMERIC(8, 4) DEFAULT 0.0,
    trailing_stop_active BOOLEAN DEFAULT FALSE,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_positions_updated_at ON public.positions;
CREATE TRIGGER trg_positions_updated_at
BEFORE UPDATE ON public.positions
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 5. Table Trading Signals (Signaux Détectés & Analyses Protocolaires)
CREATE TABLE IF NOT EXISTS public.trading_signals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    symbol VARCHAR(20) NOT NULL,
    company_name VARCHAR(150),
    signal_timestamp TIMESTAMPTZ DEFAULT NOW(),
    strategy VARCHAR(40) DEFAULT 'v3_institutional',
    verdict_swing VARCHAR(80),
    verdict_swing_badge VARCHAR(40),
    verdict_sniper VARCHAR(80),
    verdict_sniper_badge VARCHAR(40),
    confluence_score NUMERIC(3, 1),
    current_price NUMERIC(12, 4),
    pullback_pct NUMERIC(6, 2),
    rsi_14 NUMERIC(5, 2),
    atr_14_d1 NUMERIC(10, 4),
    m15_range NUMERIC(10, 4),
    m15_ratio_atr_pct NUMERIC(5, 1),
    entry_target NUMERIC(12, 4),
    stop_loss NUMERIC(12, 4),
    take_profit_1 NUMERIC(12, 4),
    take_profit_2 NUMERIC(12, 4),
    rr_ratio NUMERIC(5, 2),
    execution_phase VARCHAR(40),
    ideal_execution_time VARCHAR(40),
    max_execution_time VARCHAR(40),
    action_plan TEXT,
    status VARCHAR(20) DEFAULT 'EMIS', -- 'EMIS', 'EXECUTE', 'EXPIRE', 'IGNORE'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. Table Macro Regimes (Historique du Baromètre Macroéconomique)
CREATE TABLE IF NOT EXISTS public.macro_regimes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    recorded_at TIMESTAMPTZ DEFAULT NOW(),
    regime VARCHAR(20) NOT NULL, -- 'RISK-ON', 'NEUTRE', 'RISK-OFF'
    vix_value NUMERIC(6, 2),
    vix_status VARCHAR(60),
    dxy_value NUMERIC(6, 2),
    dxy_status VARCHAR(60),
    wti_value NUMERIC(6, 2),
    wti_status VARCHAR(60),
    yield_spread_10y_2y NUMERIC(6, 3),
    yield_curve_status VARCHAR(60),
    spy_trend_200d VARCHAR(60),
    composite_score NUMERIC(4, 1),
    action_rule TEXT,
    summary TEXT
);

-- 7. Table Backtest Runs (Historique & Logs des Backtests & Stress-Tests)
CREATE TABLE IF NOT EXISTS public.backtest_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_timestamp TIMESTAMPTZ DEFAULT NOW(),
    strategy_name VARCHAR(60) NOT NULL,
    period_tested VARCHAR(40),
    initial_capital NUMERIC(12, 2) NOT NULL,
    final_capital NUMERIC(12, 2) NOT NULL,
    net_gain_eur NUMERIC(12, 2),
    total_return_pct NUMERIC(8, 2),
    total_trades INTEGER,
    winning_trades INTEGER,
    losing_trades INTEGER,
    win_rate_pct NUMERIC(5, 2),
    profit_factor NUMERIC(6, 2),
    max_drawdown_pct NUMERIC(6, 2),
    notes TEXT
);

-- 8. Table App Settings (Paramètres & Configuration Globale)
CREATE TABLE IF NOT EXISTS public.app_settings (
    key VARCHAR(60) PRIMARY KEY,
    value JSONB NOT NULL,
    description TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_app_settings_updated_at ON public.app_settings;
CREATE TRIGGER trg_app_settings_updated_at
BEFORE UPDATE ON public.app_settings
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 9. Index de performance
CREATE INDEX IF NOT EXISTS idx_watchlist_symbol ON public.watchlist(symbol);
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON public.positions(symbol);
CREATE INDEX IF NOT EXISTS idx_positions_status ON public.positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_broker ON public.positions(broker);
CREATE INDEX IF NOT EXISTS idx_trading_signals_symbol ON public.trading_signals(symbol);
CREATE INDEX IF NOT EXISTS idx_trading_signals_time ON public.trading_signals(signal_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_macro_regimes_time ON public.macro_regimes(recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_time ON public.backtest_runs(run_timestamp DESC);

-- 10. Activation RLS (Row Level Security) avec Politiques Permissives
ALTER TABLE public.watchlist ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trading_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.macro_regimes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.backtest_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app_settings ENABLE ROW LEVEL SECURITY;

DO '
BEGIN
    DROP POLICY IF EXISTS "Allow full access to watchlist" ON public.watchlist;
    CREATE POLICY "Allow full access to watchlist" ON public.watchlist FOR ALL USING (true) WITH CHECK (true);
    
    DROP POLICY IF EXISTS "Allow full access to positions" ON public.positions;
    CREATE POLICY "Allow full access to positions" ON public.positions FOR ALL USING (true) WITH CHECK (true);

    DROP POLICY IF EXISTS "Allow full access to trading_signals" ON public.trading_signals;
    CREATE POLICY "Allow full access to trading_signals" ON public.trading_signals FOR ALL USING (true) WITH CHECK (true);

    DROP POLICY IF EXISTS "Allow full access to macro_regimes" ON public.macro_regimes;
    CREATE POLICY "Allow full access to macro_regimes" ON public.macro_regimes FOR ALL USING (true) WITH CHECK (true);

    DROP POLICY IF EXISTS "Allow full access to backtest_runs" ON public.backtest_runs;
    CREATE POLICY "Allow full access to backtest_runs" ON public.backtest_runs FOR ALL USING (true) WITH CHECK (true);

    DROP POLICY IF EXISTS "Allow full access to app_settings" ON public.app_settings;
    CREATE POLICY "Allow full access to app_settings" ON public.app_settings FOR ALL USING (true) WITH CHECK (true);
END;
';

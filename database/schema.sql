-- ============================================================
--  AI Car Deal Finder — PostgreSQL Database Schema
--  Version: 1.1 | Date: 2026-03-18
--  Data source: auto.dev API (https://api.auto.dev)
-- ============================================================

-- ─────────────────────────────────────────────────────────────
-- 1. DEALERS
-- ─────────────────────────────────────────────────────────────
CREATE TABLE dealers (
    id              SERIAL PRIMARY KEY,
    dealer_id       VARCHAR(64) UNIQUE NOT NULL,   -- external source ID
    name            VARCHAR(255) NOT NULL,
    address         TEXT,
    city            VARCHAR(100),
    state           CHAR(2),
    zip             VARCHAR(10),
    lat             DECIMAL(9, 6),
    lng             DECIMAL(9, 6),
    phone           VARCHAR(20),
    website         VARCHAR(255),
    rating          DECIMAL(3, 2),                  -- avg customer rating (0–5)
    review_count    INT DEFAULT 0,
    source          VARCHAR(50),                    -- 'marketcheck', 'edmunds', etc.
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_dealers_state ON dealers(state);
CREATE INDEX idx_dealers_location ON dealers(lat, lng);

-- ─────────────────────────────────────────────────────────────
-- 2. VEHICLES (master catalog — specs & metadata)
--    Fields mapped from auto.dev vehicle.* object
-- ─────────────────────────────────────────────────────────────
CREATE TABLE vehicles (
    id              SERIAL PRIMARY KEY,
    vin             VARCHAR(17) UNIQUE NOT NULL,
    year            SMALLINT NOT NULL,
    make            VARCHAR(50) NOT NULL,           -- vehicle.make
    model           VARCHAR(100) NOT NULL,          -- vehicle.model
    trim            VARCHAR(100),                   -- vehicle.trim
    series          VARCHAR(200),                   -- vehicle.series (full spec string)
    body_style      VARCHAR(50),                    -- vehicle.bodyStyle
    vehicle_type    VARCHAR(50),                    -- vehicle.type (Crossover, Sedan, etc.)
    drivetrain      VARCHAR(10),                    -- vehicle.drivetrain
    engine          VARCHAR(150),                   -- vehicle.engine
    transmission    VARCHAR(50),                    -- vehicle.transmission
    fuel            VARCHAR(100),                   -- vehicle.fuel
    cylinders       SMALLINT,                       -- vehicle.cylinders
    doors           SMALLINT,                       -- vehicle.doors
    seats           SMALLINT,                       -- vehicle.seats
    exterior_color  VARCHAR(50),                    -- vehicle.exteriorColor
    interior_color  VARCHAR(50),                    -- vehicle.interiorColor
    base_msrp       DECIMAL(10, 2),                 -- vehicle.baseMsrp
    base_invoice    DECIMAL(10, 2),                 -- vehicle.baseInvoice
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_vehicles_make_model ON vehicles(make, model);
CREATE INDEX idx_vehicles_year ON vehicles(year);
CREATE INDEX idx_vehicles_condition ON vehicles(condition);

-- ─────────────────────────────────────────────────────────────
-- 3. LISTINGS (a vehicle at a specific dealer with price)
--    Fields mapped from auto.dev retailListing.* object
-- ─────────────────────────────────────────────────────────────
CREATE TABLE listings (
    id                  SERIAL PRIMARY KEY,
    listing_url         TEXT UNIQUE NOT NULL,          -- auto.dev @id (canonical URL)
    vin                 VARCHAR(17) REFERENCES vehicles(vin),
    dealer_zip          VARCHAR(10),                   -- used to join dealers
    listing_price       DECIMAL(10, 2),
    base_msrp           DECIMAL(10, 2),
    discount_amount     DECIMAL(10, 2)
        GENERATED ALWAYS AS (
            CASE WHEN base_msrp IS NOT NULL AND listing_price IS NOT NULL
                 THEN base_msrp - listing_price ELSE NULL END
        ) STORED,
    discount_pct        DECIMAL(5, 2)
        GENERATED ALWAYS AS (
            CASE WHEN base_msrp > 0 AND listing_price IS NOT NULL
                 THEN ((base_msrp - listing_price) / base_msrp) * 100
            ELSE 0 END
        ) STORED,
    mileage             INT DEFAULT 0,
    is_used             BOOLEAN DEFAULT TRUE,
    is_cpo              BOOLEAN DEFAULT FALSE,         -- certified pre-owned
    is_online           BOOLEAN DEFAULT TRUE,
    photo_count         SMALLINT DEFAULT 0,
    primary_image       TEXT,
    carfax_url          TEXT,
    vdp_id              TEXT,                          -- dealer VDP identifier
    -- Vehicle history (from auto.dev history object)
    accident_count      SMALLINT,
    one_owner           BOOLEAN,
    personal_use        BOOLEAN,
    usage_type          VARCHAR(50),
    -- Deal score (recomputed on each refresh)
    deal_score          DECIMAL(5, 4),
    discount_score      DECIMAL(5, 4),
    history_score       DECIMAL(5, 4),
    availability_score  DECIMAL(5, 4),
    cpo_score           DECIMAL(5, 4),
    -- Timestamps
    src_created_at      TIMESTAMP,                    -- auto.dev createdAt
    last_seen_at        TIMESTAMP DEFAULT NOW(),
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_listings_vin ON listings(vin);
CREATE INDEX idx_listings_dealer ON listings(dealer_id);
CREATE INDEX idx_listings_available ON listings(is_available);
CREATE INDEX idx_listings_price ON listings(listing_price);
CREATE INDEX idx_listings_discount ON listings(discount_pct DESC);

-- ─────────────────────────────────────────────────────────────
-- 4. INCENTIVES (manufacturer rebates, financing offers)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE incentives (
    id                  SERIAL PRIMARY KEY,
    make                VARCHAR(50) NOT NULL,
    model               VARCHAR(100),
    trim                VARCHAR(100),
    year                SMALLINT,
    incentive_type      VARCHAR(50),   -- 'cash_back', 'apr', 'lease', 'loyalty'
    description         TEXT,
    amount              DECIMAL(10, 2),
    apr_rate            DECIMAL(5, 3),
    lease_monthly       DECIMAL(8, 2),
    valid_from          DATE,
    valid_until         DATE,
    region              VARCHAR(10) DEFAULT 'US',  -- 'US', state code, or 'ALL'
    source              VARCHAR(50),
    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_incentives_make_model ON incentives(make, model);
CREATE INDEX idx_incentives_validity ON incentives(valid_from, valid_until);

-- Deal scores are stored inline in the listings table (deal_score column).
-- This separate table can be used for historical score tracking.
CREATE TABLE deal_score_history (
    id                  SERIAL PRIMARY KEY,
    listing_url         TEXT REFERENCES listings(listing_url),
    score               DECIMAL(5, 4),
    discount_score      DECIMAL(5, 4),
    history_score       DECIMAL(5, 4),
    availability_score  DECIMAL(5, 4),
    cpo_score           DECIMAL(5, 4),
    scored_at           TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_score_history_listing ON deal_score_history(listing_url);
CREATE INDEX idx_score_history_score ON deal_score_history(score DESC);

-- ─────────────────────────────────────────────────────────────
-- 6. SEARCH LOG (analytics / future personalization)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE search_logs (
    id              SERIAL PRIMARY KEY,
    query_raw       TEXT,                           -- natural language query
    make            VARCHAR(50),
    model           VARCHAR(100),
    year_min        SMALLINT,
    year_max        SMALLINT,
    price_max       DECIMAL(10, 2),
    state_filter    CHAR(2),
    results_count   INT,
    session_id      VARCHAR(64),
    created_at      TIMESTAMP DEFAULT NOW()
);

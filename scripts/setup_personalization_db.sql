-- ============================================================================
-- Personalization Engine - Database Setup Script
-- ============================================================================
-- This script creates the Delta table for storing user preference profiles
-- Run this in a Databricks SQL notebook or SQL Warehouse
-- ============================================================================

-- Step 1: Use Existing Catalog and Schema
-- ============================================================================
-- Using your existing sandbox catalog and venkat schema
USE CATALOG sandbox;
USE SCHEMA venkat;

-- Step 2: Create User Profiles Table
-- ============================================================================
CREATE TABLE IF NOT EXISTS sandbox.venkat.user_profiles (
    user_id STRING NOT NULL
        COMMENT 'Unique user identifier from JWT token (email or OAuth ID)',
    
    profile_data STRING NOT NULL
        COMMENT 'JSON-serialized UserProfile object containing preferences, weights, and history',
    
    created_at TIMESTAMP
        COMMENT 'When the profile was first created',
    
    updated_at TIMESTAMP
        COMMENT 'Last time the profile was updated',
    
    CONSTRAINT user_profiles_pk PRIMARY KEY (user_id)
) 
USING DELTA
COMMENT 'User preference profiles for personalization engine. Stores learned preferences (colors, brands, materials) with confidence weights.'
TBLPROPERTIES (
    'delta.enableChangeDataFeed' = 'true',
    'delta.autoOptimize.optimizeWrite' = 'true',
    'delta.autoOptimize.autoCompact' = 'true'
);

-- Step 3: Grant Permissions
-- ============================================================================
-- Grant access to your application user/service principal
-- Note: Unity Catalog uses MODIFY privilege instead of INSERT/UPDATE

GRANT SELECT, MODIFY ON TABLE sandbox.venkat.user_profiles 
TO `venkat@xponent.ai`;

-- If using a service principal, use this format:
-- GRANT SELECT, MODIFY ON TABLE sandbox.venkat.user_profiles 
-- TO `<service-principal-application-id>`;

-- For production, consider using a dedicated service principal
-- and granting minimum required permissions

-- Step 4: Verify Setup
-- ============================================================================
-- Check table structure
DESCRIBE EXTENDED sandbox.venkat.user_profiles;

-- Check permissions
SHOW GRANTS ON TABLE sandbox.venkat.user_profiles;

-- Test insert (optional - will be done automatically by app)
-- INSERT INTO sandbox.venkat.user_profiles (user_id, profile_data, created_at, updated_at)
-- VALUES (
--     'test_user_123',
--     '{"user_id": "test_user_123", "colors": {"items": {}}, "brands": {"items": {}}}',
--     CURRENT_TIMESTAMP(),
--     CURRENT_TIMESTAMP()
-- );

-- Verify insert
-- SELECT * FROM sandbox.venkat.user_profiles WHERE user_id = 'test_user_123';

-- Clean up test data
-- DELETE FROM sandbox.venkat.user_profiles WHERE user_id = 'test_user_123';

-- ============================================================================
-- Monitoring Queries
-- ============================================================================

-- Count total users with profiles
SELECT COUNT(*) as total_users
FROM sandbox.venkat.user_profiles;

-- Check average profile size
SELECT 
    COUNT(*) as total_users,
    AVG(LENGTH(profile_data)) as avg_profile_size_bytes,
    MAX(LENGTH(profile_data)) as max_profile_size_bytes,
    MIN(updated_at) as oldest_profile,
    MAX(updated_at) as newest_profile
FROM sandbox.venkat.user_profiles;

-- Find most recently active users
SELECT 
    user_id,
    LENGTH(profile_data) as profile_size_bytes,
    created_at,
    updated_at,
    DATEDIFF(CURRENT_DATE(), DATE(updated_at)) as days_since_update
FROM sandbox.venkat.user_profiles
ORDER BY updated_at DESC
LIMIT 20;

-- Find users with largest profiles (might need cleanup)
SELECT 
    user_id,
    LENGTH(profile_data) as profile_size_bytes,
    updated_at
FROM sandbox.venkat.user_profiles
ORDER BY LENGTH(profile_data) DESC
LIMIT 10;

-- Profile update frequency (profiles updated in last 7 days)
SELECT 
    DATE(updated_at) as update_date,
    COUNT(*) as profiles_updated
FROM sandbox.venkat.user_profiles
WHERE updated_at >= DATE_SUB(CURRENT_DATE(), 7)
GROUP BY DATE(updated_at)
ORDER BY update_date DESC;

-- ============================================================================
-- Maintenance Queries
-- ============================================================================

-- Optimize table (improves query performance)
OPTIMIZE sandbox.venkat.user_profiles;

-- Vacuum old versions (free up storage - only keeps last 7 days by default)
-- CAUTION: Cannot undo vacuum operation
-- VACUUM sandbox.venkat.user_profiles RETAIN 168 HOURS;

-- Check table history
DESCRIBE HISTORY sandbox.venkat.user_profiles;

-- Check table stats
ANALYZE TABLE sandbox.venkat.user_profiles COMPUTE STATISTICS;

-- ============================================================================
-- Backup & Recovery
-- ============================================================================

-- Create backup table (snapshot)
-- CREATE TABLE sandbox.venkat.user_profiles_backup_20260112
-- DEEP CLONE sandbox.venkat.user_profiles;

-- Restore from backup (if needed)
-- DROP TABLE IF EXISTS sandbox.venkat.user_profiles;
-- CREATE TABLE sandbox.venkat.user_profiles
-- DEEP CLONE sandbox.venkat.user_profiles_backup_20260112;

-- ============================================================================
-- Cleanup (Development/Testing Only)
-- ============================================================================

-- Drop table (CAUTION: Deletes all user profiles!)
-- DROP TABLE IF EXISTS sandbox.venkat.user_profiles;

-- ============================================================================
-- Setup Complete!
-- ============================================================================
-- Next steps:
-- 1. Set ENABLE_PERSONALIZATION=true in your app environment
-- 2. Update core/workflow.py to use SparkSession (if not already done)
-- 3. Deploy app: databricks bundle deploy
-- 4. Test with a few users
-- 5. Monitor table growth and query performance
-- ============================================================================

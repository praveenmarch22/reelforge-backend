-- Runs once, when the local Postgres volume is first created.
-- A separate database for automated tests, so they never touch development data.
CREATE DATABASE reelforge_test OWNER reelforge;

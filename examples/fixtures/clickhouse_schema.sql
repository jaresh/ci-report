-- ─────────────────────────────────────────────────────────────────────────────
-- CI schema + fixture data for ci-report (ClickHouse)
--
-- Load into ClickHouse:
--   clickhouse-client --multiquery < examples/fixtures/clickhouse_schema.sql
--
-- Tables
--   test_runs            → tool_clickhouse.py "build" config key (failures)
--   performance_metrics  → tool_clickhouse.py "models" config key (performance)
--
-- Fixture: 8 builds (1240-1247), 2 test scenarios, 2 performance models.
-- Build 1247 is "current", build 1244 is the reference build.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE DATABASE IF NOT EXISTS ci_metrics;


-- ─────────────────────────────────────────────────────────────────────────────
-- Test results table (queried when "build" key is set in config)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ci_metrics.test_runs
(
    build        String,
    scenario     String,
    config       String     DEFAULT '',
    name         String,
    status       LowCardinality(String),     -- pass | fail | error | timeout | skip
    duration_s   Float32    DEFAULT 0,
    failure_msg  String     DEFAULT '',
    failure_txt  String     DEFAULT '',
    jira         String     DEFAULT '',
    jira_url     String     DEFAULT '',
    task_url     String     DEFAULT '',
    log_url      String     DEFAULT '',
    ran_at       DateTime   DEFAULT now()
)
ENGINE = MergeTree()
ORDER BY (build, scenario, name)
SETTINGS index_granularity = 8192;


-- ─────────────────────────────────────────────────────────────────────────────
-- Scenario: E2E Upload Pipeline
-- ─────────────────────────────────────────────────────────────────────────────

-- Bearer token expiry [PIPE-458] — passes 1240-1246, fails in 1247
INSERT INTO ci_metrics.test_runs
    (build, scenario, config, name, status, duration_s, failure_msg, failure_txt, jira, jira_url, task_url, log_url, ran_at)
VALUES
('1240','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Bearer token expiry at chunk boundary [PIPE-458]','pass',29.8,'','','PIPE-458','https://jira.example.com/browse/PIPE-458','https://ci.example.com/jobs/1240/tasks/38','https://ci.example.com/jobs/1240/logs/pipe-458','2024-01-08 09:00:00'),
('1241','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Bearer token expiry at chunk boundary [PIPE-458]','pass',31.2,'','','PIPE-458','https://jira.example.com/browse/PIPE-458','https://ci.example.com/jobs/1241/tasks/38','https://ci.example.com/jobs/1241/logs/pipe-458','2024-01-09 09:00:00'),
('1242','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Bearer token expiry at chunk boundary [PIPE-458]','pass',30.5,'','','PIPE-458','https://jira.example.com/browse/PIPE-458','https://ci.example.com/jobs/1242/tasks/38','https://ci.example.com/jobs/1242/logs/pipe-458','2024-01-10 09:00:00'),
('1243','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Bearer token expiry at chunk boundary [PIPE-458]','pass',28.9,'','','PIPE-458','https://jira.example.com/browse/PIPE-458','https://ci.example.com/jobs/1243/tasks/38','https://ci.example.com/jobs/1243/logs/pipe-458','2024-01-11 09:00:00'),
('1244','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Bearer token expiry at chunk boundary [PIPE-458]','pass',30.1,'','','PIPE-458','https://jira.example.com/browse/PIPE-458','https://ci.example.com/jobs/1244/tasks/38','https://ci.example.com/jobs/1244/logs/pipe-458','2024-01-12 09:00:00'),
('1245','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Bearer token expiry at chunk boundary [PIPE-458]','pass',31.0,'','','PIPE-458','https://jira.example.com/browse/PIPE-458','https://ci.example.com/jobs/1245/tasks/38','https://ci.example.com/jobs/1245/logs/pipe-458','2024-01-13 09:00:00'),
('1246','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Bearer token expiry at chunk boundary [PIPE-458]','pass',29.7,'','','PIPE-458','https://jira.example.com/browse/PIPE-458','https://ci.example.com/jobs/1246/tasks/38','https://ci.example.com/jobs/1246/logs/pipe-458','2024-01-14 09:00:00'),
('1247','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Bearer token expiry at chunk boundary [PIPE-458]','fail',18.1,'HTTP 403 Forbidden at chunk 7/8','HTTP 403 at chunk: 7/8\nbytes transferred: 56 MB of 64 MB\ntoken age at failure: 18m 02s\nhint: JFROG_CLI_TOKEN_REFRESH env var missing from agent-01','PIPE-458','https://jira.example.com/browse/PIPE-458','https://ci.example.com/jobs/1247/tasks/38','https://ci.example.com/jobs/1247/logs/pipe-458','2024-01-15 09:00:00');

-- Upload survives 60s network partition [PIPE-481] — passes 1240-1246, times out in 1247
INSERT INTO ci_metrics.test_runs
    (build, scenario, config, name, status, duration_s, failure_msg, failure_txt, jira, jira_url, task_url, log_url, ran_at)
VALUES
('1240','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Upload survives 60s network partition mid-transfer [PIPE-481]','pass',34.2,'','','PIPE-481','https://jira.example.com/browse/PIPE-481','https://ci.example.com/jobs/1240/tasks/39','https://ci.example.com/jobs/1240/logs/pipe-481','2024-01-08 09:00:00'),
('1241','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Upload survives 60s network partition mid-transfer [PIPE-481]','pass',35.8,'','','PIPE-481','https://jira.example.com/browse/PIPE-481','https://ci.example.com/jobs/1241/tasks/39','https://ci.example.com/jobs/1241/logs/pipe-481','2024-01-09 09:00:00'),
('1242','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Upload survives 60s network partition mid-transfer [PIPE-481]','pass',33.7,'','','PIPE-481','https://jira.example.com/browse/PIPE-481','https://ci.example.com/jobs/1242/tasks/39','https://ci.example.com/jobs/1242/logs/pipe-481','2024-01-10 09:00:00'),
('1243','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Upload survives 60s network partition mid-transfer [PIPE-481]','pass',36.1,'','','PIPE-481','https://jira.example.com/browse/PIPE-481','https://ci.example.com/jobs/1243/tasks/39','https://ci.example.com/jobs/1243/logs/pipe-481','2024-01-11 09:00:00'),
('1244','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Upload survives 60s network partition mid-transfer [PIPE-481]','pass',34.5,'','','PIPE-481','https://jira.example.com/browse/PIPE-481','https://ci.example.com/jobs/1244/tasks/39','https://ci.example.com/jobs/1244/logs/pipe-481','2024-01-12 09:00:00'),
('1245','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Upload survives 60s network partition mid-transfer [PIPE-481]','pass',35.3,'','','PIPE-481','https://jira.example.com/browse/PIPE-481','https://ci.example.com/jobs/1245/tasks/39','https://ci.example.com/jobs/1245/logs/pipe-481','2024-01-13 09:00:00'),
('1246','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Upload survives 60s network partition mid-transfer [PIPE-481]','pass',33.9,'','','PIPE-481','https://jira.example.com/browse/PIPE-481','https://ci.example.com/jobs/1246/tasks/39','https://ci.example.com/jobs/1246/logs/pipe-481','2024-01-14 09:00:00'),
('1247','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Upload survives 60s network partition mid-transfer [PIPE-481]','timeout',192.0,'Test timed out after 3 minutes','TimeoutError: CLI reached MAX_RETRY_WAIT=120s without completing upload\n  Regression: MAX_RETRY_WAIT changed from 60s to 120s in CLI 2.52.0','PIPE-481','https://jira.example.com/browse/PIPE-481','https://ci.example.com/jobs/1247/tasks/39','https://ci.example.com/jobs/1247/logs/pipe-481','2024-01-15 09:00:00');

-- Passing tests — all 8 builds
INSERT INTO ci_metrics.test_runs (build, scenario, config, name, status, duration_s, ran_at) VALUES
('1240','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Multipart checksum validation on reassembly','pass',11.2,'2024-01-08 09:00:00'),
('1241','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Multipart checksum validation on reassembly','pass',12.4,'2024-01-09 09:00:00'),
('1242','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Multipart checksum validation on reassembly','pass',10.8,'2024-01-10 09:00:00'),
('1243','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Multipart checksum validation on reassembly','pass',13.1,'2024-01-11 09:00:00'),
('1244','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Multipart checksum validation on reassembly','pass',11.9,'2024-01-12 09:00:00'),
('1245','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Multipart checksum validation on reassembly','pass',12.6,'2024-01-13 09:00:00'),
('1246','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Multipart checksum validation on reassembly','pass',11.4,'2024-01-14 09:00:00'),
('1247','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Multipart checksum validation on reassembly','pass',12.2,'2024-01-15 09:00:00'),
('1240','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Retry after 503 Service Unavailable','pass',7.1,'2024-01-08 09:00:00'),
('1241','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Retry after 503 Service Unavailable','pass',8.3,'2024-01-09 09:00:00'),
('1242','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Retry after 503 Service Unavailable','pass',6.9,'2024-01-10 09:00:00'),
('1243','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Retry after 503 Service Unavailable','pass',7.8,'2024-01-11 09:00:00'),
('1244','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Retry after 503 Service Unavailable','pass',8.1,'2024-01-12 09:00:00'),
('1245','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Retry after 503 Service Unavailable','pass',7.4,'2024-01-13 09:00:00'),
('1246','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Retry after 503 Service Unavailable','pass',8.0,'2024-01-14 09:00:00'),
('1247','E2E Upload Pipeline','docker-arm64 · Linux · agent-01','Retry after 503 Service Unavailable','pass',7.6,'2024-01-15 09:00:00');


-- ─────────────────────────────────────────────────────────────────────────────
-- Scenario: Checksum Deduplication
-- ─────────────────────────────────────────────────────────────────────────────

-- Cross-repo dedup timeout [PIPE-476] — intermittent: fails on 1242 and 1247
INSERT INTO ci_metrics.test_runs
    (build, scenario, config, name, status, duration_s, failure_msg, failure_txt, jira, jira_url, task_url, log_url, ran_at)
VALUES
('1240','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Cross-repo dedup timeout under concurrent load [PIPE-476]','pass',15.2,'','','PIPE-476','https://jira.example.com/browse/PIPE-476','https://ci.example.com/jobs/1240/tasks/44','https://ci.example.com/jobs/1240/logs/pipe-476','2024-01-08 09:05:00'),
('1241','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Cross-repo dedup timeout under concurrent load [PIPE-476]','pass',14.8,'','','PIPE-476','https://jira.example.com/browse/PIPE-476','https://ci.example.com/jobs/1241/tasks/44','https://ci.example.com/jobs/1241/logs/pipe-476','2024-01-09 09:05:00'),
('1242','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Cross-repo dedup timeout under concurrent load [PIPE-476]','fail',30.4,'DeduplicationTimeout: request exceeded 30s threshold','DeduplicationTimeout: sha256 dedup took 30.4s, threshold=30s\n  context: 3 concurrent uploads on k8s-pool-eu-west-1','PIPE-476','https://jira.example.com/browse/PIPE-476','https://ci.example.com/jobs/1242/tasks/44','https://ci.example.com/jobs/1242/logs/pipe-476','2024-01-10 09:05:00'),
('1243','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Cross-repo dedup timeout under concurrent load [PIPE-476]','pass',16.1,'','','PIPE-476','https://jira.example.com/browse/PIPE-476','https://ci.example.com/jobs/1243/tasks/44','https://ci.example.com/jobs/1243/logs/pipe-476','2024-01-11 09:05:00'),
('1244','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Cross-repo dedup timeout under concurrent load [PIPE-476]','pass',15.5,'','','PIPE-476','https://jira.example.com/browse/PIPE-476','https://ci.example.com/jobs/1244/tasks/44','https://ci.example.com/jobs/1244/logs/pipe-476','2024-01-12 09:05:00'),
('1245','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Cross-repo dedup timeout under concurrent load [PIPE-476]','pass',15.9,'','','PIPE-476','https://jira.example.com/browse/PIPE-476','https://ci.example.com/jobs/1245/tasks/44','https://ci.example.com/jobs/1245/logs/pipe-476','2024-01-13 09:05:00'),
('1246','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Cross-repo dedup timeout under concurrent load [PIPE-476]','pass',14.7,'','','PIPE-476','https://jira.example.com/browse/PIPE-476','https://ci.example.com/jobs/1246/tasks/44','https://ci.example.com/jobs/1246/logs/pipe-476','2024-01-14 09:05:00'),
('1247','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Cross-repo dedup timeout under concurrent load [PIPE-476]','fail',30.4,'DeduplicationTimeout: request exceeded 30s threshold','DeduplicationTimeout: sha256 dedup took 30.4s, threshold=30s\n  context: 3 concurrent uploads on k8s-pool-eu-west-1','PIPE-476','https://jira.example.com/browse/PIPE-476','https://ci.example.com/jobs/1247/tasks/44','https://ci.example.com/jobs/1247/logs/pipe-476','2024-01-15 09:05:00');

-- Dedup SHA-256 edge case [PIPE-477] — passes 1240-1246, errors in 1247
INSERT INTO ci_metrics.test_runs
    (build, scenario, config, name, status, duration_s, failure_msg, failure_txt, jira, jira_url, task_url, log_url, ran_at)
VALUES
('1240','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup skips binary on SHA-256 edge case [PIPE-477]','pass',1.1,'','','PIPE-477','https://jira.example.com/browse/PIPE-477','https://ci.example.com/jobs/1240/tasks/45','https://ci.example.com/jobs/1240/logs/pipe-477','2024-01-08 09:05:00'),
('1241','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup skips binary on SHA-256 edge case [PIPE-477]','pass',0.9,'','','PIPE-477','https://jira.example.com/browse/PIPE-477','https://ci.example.com/jobs/1241/tasks/45','https://ci.example.com/jobs/1241/logs/pipe-477','2024-01-09 09:05:00'),
('1242','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup skips binary on SHA-256 edge case [PIPE-477]','pass',1.0,'','','PIPE-477','https://jira.example.com/browse/PIPE-477','https://ci.example.com/jobs/1242/tasks/45','https://ci.example.com/jobs/1242/logs/pipe-477','2024-01-10 09:05:00'),
('1243','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup skips binary on SHA-256 edge case [PIPE-477]','pass',0.8,'','','PIPE-477','https://jira.example.com/browse/PIPE-477','https://ci.example.com/jobs/1243/tasks/45','https://ci.example.com/jobs/1243/logs/pipe-477','2024-01-11 09:05:00'),
('1244','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup skips binary on SHA-256 edge case [PIPE-477]','pass',1.2,'','','PIPE-477','https://jira.example.com/browse/PIPE-477','https://ci.example.com/jobs/1244/tasks/45','https://ci.example.com/jobs/1244/logs/pipe-477','2024-01-12 09:05:00'),
('1245','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup skips binary on SHA-256 edge case [PIPE-477]','pass',0.9,'','','PIPE-477','https://jira.example.com/browse/PIPE-477','https://ci.example.com/jobs/1245/tasks/45','https://ci.example.com/jobs/1245/logs/pipe-477','2024-01-13 09:05:00'),
('1246','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup skips binary on SHA-256 edge case [PIPE-477]','pass',1.0,'','','PIPE-477','https://jira.example.com/browse/PIPE-477','https://ci.example.com/jobs/1246/tasks/45','https://ci.example.com/jobs/1246/logs/pipe-477','2024-01-14 09:05:00'),
('1247','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup skips binary on SHA-256 edge case [PIPE-477]','error',0.2,'NullPointerException in DeduplicationService.resolveCollision()','java.lang.NullPointerException\n  at com.jfrog.artifactory.storage.DeduplicationService.resolveCollision(DeduplicationService.java:412)','PIPE-477','https://jira.example.com/browse/PIPE-477','https://ci.example.com/jobs/1247/tasks/45','https://ci.example.com/jobs/1247/logs/pipe-477','2024-01-15 09:05:00');

INSERT INTO ci_metrics.test_runs (build, scenario, config, name, status, duration_s, ran_at) VALUES
('1240','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup correctly handles zero-byte files','pass',0.4,'2024-01-08 09:05:00'),
('1241','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup correctly handles zero-byte files','pass',0.5,'2024-01-09 09:05:00'),
('1242','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup correctly handles zero-byte files','pass',0.3,'2024-01-10 09:05:00'),
('1243','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup correctly handles zero-byte files','pass',0.4,'2024-01-11 09:05:00'),
('1244','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup correctly handles zero-byte files','pass',0.5,'2024-01-12 09:05:00'),
('1245','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup correctly handles zero-byte files','pass',0.4,'2024-01-13 09:05:00'),
('1246','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup correctly handles zero-byte files','pass',0.3,'2024-01-14 09:05:00'),
('1247','Checksum Deduplication','k8s-pod · Linux · pool-eu-west-1','Dedup correctly handles zero-byte files','pass',0.4,'2024-01-15 09:05:00');


-- ─────────────────────────────────────────────────────────────────────────────
-- Performance metrics table (queried when "models" key is set in config)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ci_metrics.performance_metrics
(
    build        String,
    model        String,
    metric_name  String,
    unit         String,
    direction    LowCardinality(String),   -- 'higher_better' | 'lower_better'
    value        Float64,
    recorded_at  DateTime DEFAULT now()
)
ENGINE = MergeTree()
ORDER BY (model, metric_name, build)
SETTINGS index_granularity = 8192;


-- ─────────────────────────────────────────────────────────────────────────────
-- Model: JFrog CLI 2.x · Linux arm64
-- Steady improving trend; note the ClickHouse tool will show current vs ref.
-- ─────────────────────────────────────────────────────────────────────────────

INSERT INTO ci_metrics.performance_metrics
    (build, model, metric_name, unit, direction, value, recorded_at)
VALUES
-- Upload Throughput (MB/s) — higher is better; improving build-over-build
('1240','JFrog CLI 2.x · Linux arm64','Upload Throughput','MB/s','higher_better',74.0,'2024-01-08 09:30:00'),
('1241','JFrog CLI 2.x · Linux arm64','Upload Throughput','MB/s','higher_better',77.1,'2024-01-09 09:30:00'),
('1242','JFrog CLI 2.x · Linux arm64','Upload Throughput','MB/s','higher_better',78.4,'2024-01-10 09:30:00'),
('1243','JFrog CLI 2.x · Linux arm64','Upload Throughput','MB/s','higher_better',79.2,'2024-01-11 09:30:00'),
('1244','JFrog CLI 2.x · Linux arm64','Upload Throughput','MB/s','higher_better',81.0,'2024-01-12 09:30:00'),
('1245','JFrog CLI 2.x · Linux arm64','Upload Throughput','MB/s','higher_better',83.3,'2024-01-13 09:30:00'),
('1246','JFrog CLI 2.x · Linux arm64','Upload Throughput','MB/s','higher_better',85.1,'2024-01-14 09:30:00'),
('1247','JFrog CLI 2.x · Linux arm64','Upload Throughput','MB/s','higher_better',87.3,'2024-01-15 09:30:00'),

-- Upload Latency p95 (ms) — lower is better; improving (latency is falling)
('1240','JFrog CLI 2.x · Linux arm64','Upload Latency p95','ms','lower_better',268,'2024-01-08 09:30:00'),
('1241','JFrog CLI 2.x · Linux arm64','Upload Latency p95','ms','lower_better',260,'2024-01-09 09:30:00'),
('1242','JFrog CLI 2.x · Linux arm64','Upload Latency p95','ms','lower_better',255,'2024-01-10 09:30:00'),
('1243','JFrog CLI 2.x · Linux arm64','Upload Latency p95','ms','lower_better',250,'2024-01-11 09:30:00'),
('1244','JFrog CLI 2.x · Linux arm64','Upload Latency p95','ms','lower_better',245,'2024-01-12 09:30:00'),
('1245','JFrog CLI 2.x · Linux arm64','Upload Latency p95','ms','lower_better',241,'2024-01-13 09:30:00'),
('1246','JFrog CLI 2.x · Linux arm64','Upload Latency p95','ms','lower_better',237,'2024-01-14 09:30:00'),
('1247','JFrog CLI 2.x · Linux arm64','Upload Latency p95','ms','lower_better',234,'2024-01-15 09:30:00'),

-- CPU Utilisation (%) — lower is better; gradually improving
('1240','JFrog CLI 2.x · Linux arm64','CPU Utilisation','%','lower_better',42.1,'2024-01-08 09:30:00'),
('1241','JFrog CLI 2.x · Linux arm64','CPU Utilisation','%','lower_better',41.3,'2024-01-09 09:30:00'),
('1242','JFrog CLI 2.x · Linux arm64','CPU Utilisation','%','lower_better',40.8,'2024-01-10 09:30:00'),
('1243','JFrog CLI 2.x · Linux arm64','CPU Utilisation','%','lower_better',40.2,'2024-01-11 09:30:00'),
('1244','JFrog CLI 2.x · Linux arm64','CPU Utilisation','%','lower_better',39.5,'2024-01-12 09:30:00'),
('1245','JFrog CLI 2.x · Linux arm64','CPU Utilisation','%','lower_better',38.9,'2024-01-13 09:30:00'),
('1246','JFrog CLI 2.x · Linux arm64','CPU Utilisation','%','lower_better',37.4,'2024-01-14 09:30:00'),
('1247','JFrog CLI 2.x · Linux arm64','CPU Utilisation','%','lower_better',36.8,'2024-01-15 09:30:00');


-- ─────────────────────────────────────────────────────────────────────────────
-- Model: Curl Fallback · Linux amd64
-- Degrading trend — useful for seeing regression indicators in the dashboard.
-- ─────────────────────────────────────────────────────────────────────────────

INSERT INTO ci_metrics.performance_metrics
    (build, model, metric_name, unit, direction, value, recorded_at)
VALUES
-- Upload Throughput (MB/s) — higher is better; slowly declining
('1240','Curl Fallback · Linux amd64','Upload Throughput','MB/s','higher_better',76.2,'2024-01-08 09:30:00'),
('1241','Curl Fallback · Linux amd64','Upload Throughput','MB/s','higher_better',75.8,'2024-01-09 09:30:00'),
('1242','Curl Fallback · Linux amd64','Upload Throughput','MB/s','higher_better',75.1,'2024-01-10 09:30:00'),
('1243','Curl Fallback · Linux amd64','Upload Throughput','MB/s','higher_better',74.9,'2024-01-11 09:30:00'),
('1244','Curl Fallback · Linux amd64','Upload Throughput','MB/s','higher_better',74.3,'2024-01-12 09:30:00'),
('1245','Curl Fallback · Linux amd64','Upload Throughput','MB/s','higher_better',73.7,'2024-01-13 09:30:00'),
('1246','Curl Fallback · Linux amd64','Upload Throughput','MB/s','higher_better',72.8,'2024-01-14 09:30:00'),
('1247','Curl Fallback · Linux amd64','Upload Throughput','MB/s','higher_better',72.1,'2024-01-15 09:30:00'),

-- Upload Latency p95 (ms) — lower is better; worsening (latency is climbing)
('1240','Curl Fallback · Linux amd64','Upload Latency p95','ms','lower_better',280,'2024-01-08 09:30:00'),
('1241','Curl Fallback · Linux amd64','Upload Latency p95','ms','lower_better',285,'2024-01-09 09:30:00'),
('1242','Curl Fallback · Linux amd64','Upload Latency p95','ms','lower_better',290,'2024-01-10 09:30:00'),
('1243','Curl Fallback · Linux amd64','Upload Latency p95','ms','lower_better',295,'2024-01-11 09:30:00'),
('1244','Curl Fallback · Linux amd64','Upload Latency p95','ms','lower_better',300,'2024-01-12 09:30:00'),
('1245','Curl Fallback · Linux amd64','Upload Latency p95','ms','lower_better',305,'2024-01-13 09:30:00'),
('1246','Curl Fallback · Linux amd64','Upload Latency p95','ms','lower_better',308,'2024-01-14 09:30:00'),
('1247','Curl Fallback · Linux amd64','Upload Latency p95','ms','lower_better',312,'2024-01-15 09:30:00'),

-- Connection Reuse Rate (%) — higher is better; steadily declining
('1240','Curl Fallback · Linux amd64','Connection Reuse Rate','%','higher_better',72.4,'2024-01-08 09:30:00'),
('1241','Curl Fallback · Linux amd64','Connection Reuse Rate','%','higher_better',71.1,'2024-01-09 09:30:00'),
('1242','Curl Fallback · Linux amd64','Connection Reuse Rate','%','higher_better',70.3,'2024-01-10 09:30:00'),
('1243','Curl Fallback · Linux amd64','Connection Reuse Rate','%','higher_better',68.9,'2024-01-11 09:30:00'),
('1244','Curl Fallback · Linux amd64','Connection Reuse Rate','%','higher_better',66.7,'2024-01-12 09:30:00'),
('1245','Curl Fallback · Linux amd64','Connection Reuse Rate','%','higher_better',64.2,'2024-01-13 09:30:00'),
('1246','Curl Fallback · Linux amd64','Connection Reuse Rate','%','higher_better',62.5,'2024-01-14 09:30:00'),
('1247','Curl Fallback · Linux amd64','Connection Reuse Rate','%','higher_better',61.1,'2024-01-15 09:30:00');
